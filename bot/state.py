"""Состояние: какие слоты уже вышли, какие новости и цитаты уже опубликованы.
Хранится в data/state.json (в GitHub — ветка bot-state)."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)
HISTORY_KEEP = 200
SEEN_KEEP = 3000


class State:
    def __init__(self, path: Path, sync_cmd: str = ""):
        self.path = path
        self.sync_cmd = sync_cmd
        self.data = {"posted": {}, "history": [], "seen_news": [], "used_quotes": []}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                log.warning("state.json повреждён — начинаю с пустого состояния")

    @property
    def history(self) -> list[dict]:
        return self.data["history"]

    @property
    def seen_news(self) -> set[str]:
        return set(self.data["seen_news"])

    @property
    def used_quotes(self) -> set[str]:
        return set(self.data["used_quotes"])

    def is_posted(self, day: str, kind: str) -> bool:
        return kind in self.data["posted"].get(day, [])

    def mark_posted(self, day: str, kind: str, entry: dict, news_url: str = "", quote_key: str = "") -> None:
        self.data["posted"].setdefault(day, []).append(kind)
        for old in sorted(self.data["posted"])[:-10]:
            del self.data["posted"][old]
        self.history.append(entry)
        del self.history[:-HISTORY_KEEP]
        if news_url:
            self.data["seen_news"].append(news_url)
            del self.data["seen_news"][:-SEEN_KEEP]
        if quote_key:
            self.data["used_quotes"].append(quote_key)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        if self.sync_cmd:
            result = subprocess.run(self.sync_cmd, shell=True, timeout=120)
            if result.returncode:
                log.warning("Не удалось сохранить состояние на GitHub (код %s)", result.returncode)
