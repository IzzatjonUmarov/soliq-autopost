"""Состояние: какие слоты уже опубликованы и история постов. Хранится в data/state.json (в GitHub — ветка bot-state)."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)
HISTORY_KEEP = 120


class State:
    def __init__(self, path: Path, sync_cmd: str = ""):
        self.path = path
        self.sync_cmd = sync_cmd
        self.data = {"posted": {}, "history": []}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                log.warning("state.json повреждён — начинаю с пустого состояния")

    @property
    def history(self) -> list[dict]:
        return self.data["history"]

    def is_posted(self, day: str, kind: str) -> bool:
        return kind in self.data["posted"].get(day, [])

    def mark_posted(self, day: str, kind: str, entry: dict) -> None:
        self.data["posted"].setdefault(day, []).append(kind)
        # храним отметки только за последние 10 дней
        for old in sorted(self.data["posted"])[:-10]:
            del self.data["posted"][old]
        self.history.append(entry)
        del self.history[:-HISTORY_KEEP]
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        if self.sync_cmd:
            result = subprocess.run(self.sync_cmd, shell=True)
            if result.returncode:
                log.warning("Не удалось сохранить состояние на GitHub (код %s)", result.returncode)
