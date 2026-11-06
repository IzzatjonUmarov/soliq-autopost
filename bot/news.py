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


MIN_SCORE = 2  # одного общего слова («карта», «тариф») мало — нужно хотя бы два совпадения или одно главное


def pick_news(settings: Settings, seen: set[str]) -> NewsItem | None:
    """Лучшая свежая новость, которой ещё не было в канале, или None."""
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
            candidates.append((score, item))
    log.info("Кандидатов по ключевым словам: %d", len(candidates))

    now = datetime.now(timezone.utc)
    oldest = now - timedelta(days=settings.max_age_days)
    candidates.sort(key=lambda c: (c[0], c[1].published or oldest), reverse=True)
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
            checked.append((final, item))
            log.info("  [%.0f] %s  %s", final, published.date(), item.title[:90])
    if not checked:
        return None
    checked.sort(key=lambda c: (c[0], c[1].published), reverse=True)
    return checked[0][1]
