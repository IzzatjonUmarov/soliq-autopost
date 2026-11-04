"""Цитата дня: готовые цитаты со страниц Викицитатника (ru.wikiquote.org) — текст и автор как в источнике.

На страницах тем («Деньги», «Богатство», «Долг»…) цитаты оформлены шаблоном
{{Q|Цитата=…|Автор=…}} или {{Q|…|Автор=…}} — его и разбираем. Ничего не сочиняется.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import logging
import random
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

RAW_URL = "https://ru.wikiquote.org/w/index.php"
PAGE_URL = "https://ru.wikiquote.org/wiki/{}"
USER_AGENT = "soliq-autopost/1.0 (https://github.com/IzzatjonUmarov/soliq-autopost)"
DOUBTFUL = re.compile(r"приписыва|сомнительн|апокриф|не подтвержд|ошибочно", re.I)


@dataclass
class Quote:
    text: str
    author: str
    page: str

    @property
    def url(self) -> str:
        return PAGE_URL.format(quote(self.page.replace(" ", "_")))

    @property
    def key(self) -> str:
        return hashlib.sha1(f"{self.author}|{self.text}".encode()).hexdigest()[:16]


# ── разбор вики-разметки ──────────────────────────────────────────

def _templates(text: str, name: str = "Q") -> list[str]:
    """Содержимое всех шаблонов {{Q|…}} верхнего уровня (с учётом вложенных {{…}})."""
    found, i = [], 0
    opener = "{{" + name + "|"
    while (start := text.find(opener, i)) != -1:
        depth, j = 0, start
        while j < len(text) - 1:
            pair = text[j:j + 2]
            if pair == "{{":
                depth += 1
                j += 2
            elif pair == "}}":
                depth -= 1
                j += 2
                if depth == 0:
                    break
            else:
                j += 1
        found.append(text[start + len(opener):j - 2])
        i = j
    return found


def _split_params(body: str) -> list[str]:
    """Разделить параметры шаблона по «|», не трогая [[…|…]] и {{…|…}}."""
    parts, depth_link, depth_tpl, current, i = [], 0, 0, [], 0
    while i < len(body):
        two = body[i:i + 2]
        if two == "[[":
            depth_link += 1
        elif two == "]]":
            depth_link = max(0, depth_link - 1)
        elif two == "{{":
            depth_tpl += 1
        elif two == "}}":
            depth_tpl = max(0, depth_tpl - 1)
        if two in ("[[", "]]", "{{", "}}"):
            current.append(two)
            i += 2
            continue
        if body[i] == "|" and not depth_link and not depth_tpl:
            parts.append("".join(current))
            current = []
        else:
            current.append(body[i])
        i += 1
    parts.append("".join(current))
    return parts


def clean_wiki(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<ref[^>/]*/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    for _ in range(3):  # вложенные шаблоны ({{книга|…}}, {{comment|…}})
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:Файл|File|Изображение|Image):[^\]]*\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_page(wikitext: str, page: str) -> list[Quote]:
    quotes = []
    for body in _templates(wikitext):
        # сноски убираем до разбора: внутри них бывают свои «|»
        body = re.sub(r"<ref[^>]*>.*?</ref>|<ref[^>/]*/>|<!--.*?-->", "", body, flags=re.S)
        params = _split_params(body)
        named, positional = {}, []
        for p in params:
            m = re.match(r"\s*([А-Яа-яA-Za-z]+)\s*=(.*)", p, re.S)
            if m:
                named[m.group(1).lower()] = m.group(2)
            else:
                positional.append(p)
        raw_text = named.get("цитата") or (positional[0] if positional else "")
        raw_author = named.get("автор", "")
        comment = named.get("комментарий", "")
        if DOUBTFUL.search(raw_text + raw_author + comment):
            continue
        if "\n" in raw_text.strip() or raw_text.lstrip().startswith(("—", "-", "–")):
            continue  # диалоги и стихи в несколько строк — не для поста
        text, author = clean_wiki(raw_text), clean_wiki(raw_author).strip(" ,.;")
        if text and author:
            quotes.append(Quote(text, author, page))
    return quotes


# ── загрузка и выбор ─────────────────────────────────────────────

def fetch_page(client: httpx.Client, page: str) -> list[Quote]:
    response = client.get(RAW_URL, params={"title": page, "action": "raw"},
                          headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    text = response.text
    redirect = re.match(r"#(?:REDIRECT|перенаправление)\s*\[\[([^\]]+)\]\]", text, re.I)
    if redirect:
        return fetch_page(client, redirect.group(1))
    return parse_page(text, page)


UNKNOWN_AUTHOR = re.compile(r"неизвест|аноним|^NN$|^N\.?N\.?$|пословиц|поговорк|народн", re.I)


def suitable(q: Quote, cfg: dict) -> bool:
    min_len, max_len = cfg.get("min_length", 25), cfg.get("max_length", 260)
    if not (min_len <= len(q.text) <= max_len) or len(q.author) > 90:
        return False
    # нужен конкретный человек: «карельская», «Автор неизвестен», «NN» не подходят
    if not q.author[:1].isupper() or UNKNOWN_AUTHOR.search(q.author):
        return False
    blocked = [w.lower() for w in cfg.get("blocked_words", [])]
    low = f"{q.text} {q.author}".lower()
    return not any(w in low for w in blocked)


def pick_quote(client: httpx.Client, cfg: dict, used: set[str], day: dt.date) -> Quote | None:
    """Страницы идут по кругу (по дню), цитата — случайная из ещё не опубликованных."""
    pages = cfg.get("pages", [])
    if not pages:
        return None
    start = day.toordinal() % len(pages)
    rng = random.Random(day.toordinal())
    for page in pages[start:] + pages[:start]:
        try:
            candidates = [q for q in fetch_page(client, page) if suitable(q, cfg) and q.key not in used]
        except httpx.HTTPError as exc:
            log.warning("Викицитатник «%s» недоступен: %s", page, exc)
            continue
        log.info("Цитаты «%s»: подходящих %d", page, len(candidates))
        if candidates:
            return rng.choice(candidates)
    return None
