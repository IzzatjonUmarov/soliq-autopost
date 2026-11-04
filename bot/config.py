"""Настройки (переменные окружения; в GitHub — Settings → Secrets and variables → Actions)
и списки источников: config/sources.yaml (новости) и config/quotes.yaml (цитаты)."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TASHKENT = dt.timezone(dt.timedelta(hours=5))


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _hhmm(value: str) -> dt.time:
    hours, minutes = value.split(":")
    return dt.time(int(hours), int(minutes))


@dataclass
class Settings:
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID", "@Soliqmobilxizmati"))
    # время публикаций по Ташкенту: утром цитата дня, вечером новость дня
    morning_time: dt.time = field(default_factory=lambda: _hhmm(_env("MORNING_TIME", "07:00")))
    evening_time: dt.time = field(default_factory=lambda: _hhmm(_env("EVENING_TIME", "18:00")))
    # за сколько минут до публикации собирать источники и готовить пост
    prepare_minutes: int = field(default_factory=lambda: int(_env("PREPARE_MINUTES", "15")))
    # если запуск опоздал больше чем на столько минут, пост этого слота пропускается (без залпов)
    late_minutes: int = field(default_factory=lambda: int(_env("LATE_MINUTES", "180")))
    # новости старше стольких дней не публикуются
    max_age_days: int = field(default_factory=lambda: int(_env("MAX_AGE_DAYS", "3")))
    request_timeout: int = field(default_factory=lambda: int(_env("REQUEST_TIMEOUT", "25")))
    sources_file: Path = ROOT / "config" / "sources.yaml"
    quotes_file: Path = ROOT / "config" / "quotes.yaml"
    state_file: Path = DATA / "state.json"
    preview_dir: Path = DATA / "preview"
    state_sync_cmd: str = field(default_factory=lambda: _env("STATE_SYNC_CMD"))

    @property
    def slots(self) -> dict[str, dt.time]:
        return {"morning": self.morning_time, "evening": self.evening_time}

    @property
    def watermark(self):
        from .images import WatermarkConfig

        text = self.telegram_chat_id if self.telegram_chat_id.startswith("@") else ""
        return WatermarkConfig(text)


@dataclass
class Source:
    id: str
    name: str
    url: str
    group: str = "bank"
    type: str = "auto"
    selectors: dict = field(default_factory=dict)
    link_pattern: str | None = None
    authority: str | None = None
    # true — общий поток новостей (СМИ, госпортал): берём только с явными словами про кешбэк, переводы и т. п.
    strict: bool = False
    enabled: bool = True


def load_sources(path: Path) -> list[Source]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources = [Source(**item) for item in data.get("sources", [])]
    ids = [s.id for s in sources]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Повторяющиеся id источников: {', '.join(sorted(duplicates))}")
    return [s for s in sources if s.enabled]


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def now_tashkent() -> dt.datetime:
    return dt.datetime.now(TASHKENT)
