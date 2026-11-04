"""Минимальный клиент Telegram Bot API."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str, timeout: int = 60):
        if not token or not chat_id:
            raise TelegramError("Не заданы TELEGRAM_BOT_TOKEN и/или TELEGRAM_CHAT_ID")
        self.chat_id = chat_id
        self.base = f"https://api.telegram.org/bot{token}"
        self.http = httpx.Client(timeout=timeout)

    def _call(self, method: str, payload: dict, files: dict | None = None) -> dict:
        for attempt in range(4):
            if files:
                response = self.http.post(f"{self.base}/{method}", data=payload, files=files)
            else:
                response = self.http.post(f"{self.base}/{method}", json=payload)
            data = response.json()
            if data.get("ok"):
                return data["result"]
            retry_after = data.get("parameters", {}).get("retry_after")
            if response.status_code == 429 and retry_after:
                time.sleep(int(retry_after) + 1)
                continue
            if response.status_code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise TelegramError(f"{method}: {data.get('description', response.text)}")
        raise TelegramError(f"{method}: превышено число попыток")

    def send_photo(self, jpeg: bytes, caption_html: str) -> dict:
        payload = {"chat_id": self.chat_id, "caption": caption_html, "parse_mode": "HTML"}
        return self._call("sendPhoto", payload, files={"photo": ("post.jpg", jpeg, "image/jpeg")})

    def send_message(self, html: str) -> dict:
        return self._call("sendMessage", {"chat_id": self.chat_id, "text": html, "parse_mode": "HTML",
                                          "link_preview_options": {"is_disabled": True}})

    def get_me(self) -> dict:
        return self._call("getMe", {})

    def get_chat(self) -> dict:
        return self._call("getChat", {"chat_id": self.chat_id})

    def get_member(self, user_id: int) -> dict:
        return self._call("getChatMember", {"chat_id": self.chat_id, "user_id": user_id})
