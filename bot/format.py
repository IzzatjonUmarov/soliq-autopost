"""Оформление поста: русский сверху, узбекский (новая латиница) снизу, источники, хэштеги."""

from __future__ import annotations

import html
import re
from urllib.parse import urlparse

CAPTION_LIMIT = 1024  # лимит Telegram на подпись к картинке (считаются видимые символы)


def _e(text: str) -> str:
    return html.escape(text.strip(), quote=False)


def build_caption(post: dict, channel: str) -> str:
    parts = [
        f"<b>{_e(post['headline_ru'])}</b>",
        _e(post["text_ru"]),
        "➖➖➖",
        f"<b>{_e(post['headline_uz'])}</b>",
        _e(post["text_uz"]),
    ]
    if post.get("sources"):
        links = []
        for s in post["sources"]:
            name = urlparse(s["url"]).netloc.removeprefix("www.") or s["name"]
            links.append(f'<a href="{html.escape(s["url"])}">{_e(name)}</a>')
        parts.append("🔗 Manba / Источник: " + ", ".join(links))
    footer = " ".join(f"#{t}" for t in post.get("tags", []))
    if channel.startswith("@"):
        footer = f"{footer}\n\n👉 {channel}".strip()
    if footer:
        parts.append(footer)
    return "\n\n".join(parts)


def visible_length(html_text: str) -> int:
    """Длина так, как её считает Telegram: в UTF-16 (эмодзи — 2 символа), без HTML-тегов."""
    text = html.unescape(re.sub(r"<[^>]+>", "", html_text))
    return len(text.encode("utf-16-le")) // 2
