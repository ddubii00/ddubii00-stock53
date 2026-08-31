from __future__ import annotations

import os
from typing import Protocol

import requests


class Notifier(Protocol):
    name: str

    def send(self, message: str) -> None: ...


class NoneNotifier:
    name = "none"

    def send(self, message: str) -> None:
        return None


class TelegramNotifier:
    name = "telegram"

    def __init__(self, timeout: float = 7.0):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not self.token or not self.chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are required")
        self.timeout = timeout

    def send(self, message: str) -> None:
        r = requests.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={"chat_id": self.chat_id, "text": message, "disable_web_page_preview": True},
            timeout=self.timeout,
        )
        if not r.ok:
            # requests' default HTTPError includes the URL, which contains the bot token.
            raise RuntimeError(f"Telegram send failed with HTTP {r.status_code}")


def build_notifier() -> Notifier:
    mode = os.getenv("NOTIFY_PROVIDER", "none").strip().lower()
    if mode == "telegram":
        return TelegramNotifier()
    if mode != "none":
        raise ValueError(f"Unknown NOTIFY_PROVIDER={mode}")
    return NoneNotifier()
