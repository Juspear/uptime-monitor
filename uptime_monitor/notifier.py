"""Where alerts go: Telegram, or the console when Telegram is not configured."""

from __future__ import annotations

import html
import logging
from typing import Protocol

import httpx

from .config import TelegramSettings
from .state import Event, EventType

log = logging.getLogger(__name__)


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_event(event: Event, url: str) -> str:
    """Build a short HTML message for Telegram."""
    name = html.escape(event.site_name)
    url = html.escape(url)
    if event.type is EventType.DOWN:
        reason = html.escape(event.result.error or "unknown error")
        return f"🔴 <b>{name}</b> is DOWN\n{url}\nReason: {reason}"

    text = f"🟢 <b>{name}</b> is back UP\n{url}"
    if event.downtime_seconds is not None:
        text += f"\nDowntime: {format_duration(event.downtime_seconds)}"
    if event.result.latency_ms is not None:
        text += f"\nResponse time: {event.result.latency_ms:.0f} ms"
    return text


class Notifier(Protocol):
    async def send(self, text: str) -> None: ...


class ConsoleNotifier:
    async def send(self, text: str) -> None:
        print(text.replace("<b>", "").replace("</b>", ""), flush=True)


class TelegramNotifier:
    API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, client: httpx.AsyncClient, settings: TelegramSettings):
        self._client = client
        self._settings = settings

    async def send(self, text: str) -> None:
        url = self.API_URL.format(token=self._settings.token)
        payload = {
            "chat_id": self._settings.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            response = await self._client.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except httpx.HTTPError as e:
            # A failed alert must never crash the monitor itself.
            log.error("failed to send Telegram message: %s", type(e).__name__)
