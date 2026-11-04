"""Настройки бота. Всё задаётся переменными окружения (в GitHub: Settings → Secrets and variables → Actions)."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path

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
    anthropic_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    ai_model: str = field(default_factory=lambda: _env("AI_MODEL", "claude-opus-5-5"))
    # время публикаций по Ташкенту
    morning_time: dt.time = field(default_factory=lambda: _hhmm(_env("MORNING_TIME", "07:00")))
    evening_time: dt.time = field(default_factory=lambda: _hhmm(_env("EVENING_TIME", "18:00")))
    # за сколько минут до публикации готовить пост (поиск + текст + картинка)
    prepare_minutes: int = field(default_factory=lambda: int(_env("PREPARE_MINUTES", "20")))
    # если запуск опоздал больше чем на столько минут, пост этого слота пропускается (без залпов)
    late_minutes: int = field(default_factory=lambda: int(_env("LATE_MINUTES", "180")))
    state_file: Path = DATA / "state.json"
    preview_dir: Path = DATA / "preview"
    state_sync_cmd: str = field(default_factory=lambda: _env("STATE_SYNC_CMD"))

    @property
    def slots(self) -> dict[str, dt.time]:
        return {"morning": self.morning_time, "evening": self.evening_time}

    @property
    def watermark(self) -> str:
        return self.telegram_chat_id if self.telegram_chat_id.startswith("@") else ""


def now_tashkent() -> dt.datetime:
    return dt.datetime.now(TASHKENT)
