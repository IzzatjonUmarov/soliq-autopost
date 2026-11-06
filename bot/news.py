"""Новость дня: свежая новость из источников про кешбэк, переводы, карты, вклады, тарифы.

Заголовок и описание берутся со страницы источника как есть — бот ничего не пишет сам.
Отбор по ключевым словам: чем больше совпадений с темами канала, тем выше новость в очереди.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx

from .config import Settings, Source, load_sources, load_yaml
from .fetch import NewsItem, enrich, fetch_source, make_client

log = logging.getLogger(__name__)
ENRICH_TOP = 15  # сколько лучших кандидатов открывать целиком (дата, описание, картинка)


def collect(settings: Settings, sources: list[Source]) -> dict[str, list[NewsItem] | Exception]:
    def one(source: Source):
        try:
            with make_client(settings.request_timeout) as client:
                return source.id, fetch_source(client, source)
        except Exception as exc:  # noqa: BLE001 — один сломанный сайт не должен ронять остальные
            return source.id, exc

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(one, sources))


class Scorer:
    def __init__(self, cfg: dict):
        self.top = [re.compile(p, re.I) for p in cfg.get("top", [])]
        self.strong = [re.compile(p, re.I) for p in cfg.get("strong", [])]
        self.normal = [re.compile(p, re.I) for p in cfg.get("normal", [])]
        self.negative = [re.compile(p, re.I) for p in cfg.get("negative", [])]

    def is_negative(self, text: str) -> bool:
        return any(rx.search(text) for rx in self.negative)

    def has_strong(self, text: str) -> bool:
        return any(rx.search(text) for rx in self.top + self.strong)

    def score(self, text: str) -> float:
        if self.is_negative(text):
            return 0.0
        hits = lambda rxs: sum(bool(rx.search(text)) for rx in rxs)  # noqa: E731
        return 3.0 * hits(self.top) + 2.0 * hits(self.strong) + hits(self.normal)

    def is_top(self, text: str) -> bool:
        return any(rx.search(text) for rx in self.top)


MIN_SCORE = 2  # одного общего слова («карта», «тариф») мало — нужно хотя бы два совпадения или одно главное


def _stems(title: str) -> set[str]:
    return {w[:5] for w in re.findall(r"\w+", title.lower()) if len(w) >= 4 and not w.isdigit()}


def same_story(a: str, b: str) -> bool:
    """Похожие заголовки — одна и та же новость с разных сайтов («ЦБ ожидает снижения ставок…»)."""
    sa, sb = _stems(a), _stems(b)
    if len(sa) < 3 or len(sb) < 3:
        return False
    return len(sa & sb) / min(len(sa), len(sb)) >= 0.5


def pick_news(settings: Settings, seen: set[str], recent_titles: list[str] | None = None) -> NewsItem | None:
    """Лучшая свежая новость, которой ещё не было в канале, или None.

    Порядок: сначала главные темы канала (кешбэк, переводы, платёжные сервисы), затем по баллам, затем свежее."""
    recent_titles = recent_titles or []
    sources = {s.id: s for s in load_sources(settings.sources_file)}
    scorer = Scorer(load_yaml(settings.sources_file).get("keywords", {}))
    results = collect(settings, list(sources.values()))

    candidates: list[tuple[float, NewsItem]] = []
    for source_id, result in results.items():
        if isinstance(result, Exception):
            log.warning("Источник %s: %s", source_id, result)
            continue
        source = sources[source_id]
        for item in result:
            if item.url in seen:
                continue
            score = scorer.score(f"{item.title} {item.summary}")
            if score < MIN_SCORE:
                continue
            # СМИ и госпортал (strict): главное слово (кешбэк, перевод, комиссия, НДС, Click…) — прямо в заголовке
            if source.strict and not (scorer.has_strong(item.title) and not scorer.is_negative(item.title)):
                continue
            if any(same_story(item.title, t) for t in recent_titles):
                continue  # эта новость уже выходила (с другого сайта)
            candidates.append((score, item))
    log.info("Кандидатов по ключевым словам: %d", len(candidates))

    now = datetime.now(timezone.utc)
    oldest = now - timedelta(days=settings.max_age_days)

    def priority(c: tuple[float, NewsItem]):
        return (scorer.is_top(f"{c[1].title} {c[1].summary}"), c[0], c[1].published or oldest)

    candidates.sort(key=priority, reverse=True)
    checked: list[tuple[float, NewsItem]] = []
    with make_client(settings.request_timeout) as client:
        for score, item in candidates[:ENRICH_TOP]:
            try:
                enrich(client, item)
            except httpx.HTTPError as exc:
                log.debug("Не открылась %s: %s", item.url, exc)
                continue
            published = item.published
            if published and published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            # без даты новость не берём: нельзя проверить, что она свежая
            if not published or not (oldest <= published <= now + timedelta(days=1)):
                log.info("  не подходит по дате (%s): %s", published, item.title[:80])
                continue
            if not item.summary:
                continue
            # полный текст — только для отсева (преступления, суды и т. п.), оценку он не повышает:
            # иначе статья «про другое», где мимоходом упомянуты карты и переводы, обгоняет профильные новости
            if scorer.is_negative(f"{item.title} {item.summary} {item.text[:1500]}"):
                log.info("  стоп-слово в тексте: %s", item.title[:80])
                continue
            final = scorer.score(f"{item.title} {item.summary}")
            if final < MIN_SCORE:
                continue
            if any(same_story(item.title, t) for t in recent_titles):
                continue
            checked.append((final, item))
            mark = "★" if scorer.is_top(f"{item.title} {item.summary}") else " "
            log.info("  %s[%.0f] %s  %s", mark, final, published.date(), item.title[:90])
    if not checked:
        return None
    checked.sort(key=priority, reverse=True)
    return checked[0][1]
