import json
import unittest

import httpx

from uptime_monitor.checker import CheckResult
from uptime_monitor.config import TelegramSettings
from uptime_monitor.notifier import TelegramNotifier, format_duration, format_event
from uptime_monitor.state import Event, EventType


class FormatTest(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(format_duration(42), "42s")
        self.assertEqual(format_duration(125), "2m 5s")
        self.assertEqual(format_duration(3 * 3600 + 20 * 60), "3h 20m")

    def test_down_message(self):
        event = Event(EventType.DOWN, "Shop", CheckResult(ok=False, error="timeout after 10s"))
        text = format_event(event, "https://shop.com")
        self.assertIn("DOWN", text)
        self.assertIn("timeout after 10s", text)

    def test_recovered_message(self):
        event = Event(EventType.RECOVERED, "Shop",
                      CheckResult(ok=True, status=200, latency_ms=120), downtime_seconds=300)
        text = format_event(event, "https://shop.com")
        self.assertIn("back UP", text)
        self.assertIn("5m 0s", text)

    def test_ssl_message(self):
        from datetime import datetime, timezone
        event = Event(EventType.SSL_EXPIRING, "Shop", ssl_days_left=7,
                      ssl_expires_at=datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc))
        text = format_event(event, "https://shop.com")
        self.assertIn("expires in 7 days", text)
        self.assertIn("2026-10-07 12:00 UTC", text)

    def test_ssl_message_singular_and_today(self):
        one = Event(EventType.SSL_EXPIRING, "Shop", ssl_days_left=1)
        zero = Event(EventType.SSL_EXPIRING, "Shop", ssl_days_left=0)
        self.assertIn("in 1 day\n", format_event(one, "https://shop.com"))
        self.assertIn("expires today", format_event(zero, "https://shop.com"))

    def test_html_is_escaped(self):
        event = Event(EventType.DOWN, "<script>", CheckResult(ok=False, error="a < b"))
        text = format_event(event, "https://x.com")
        self.assertNotIn("<script>", text)
        self.assertIn("a &lt; b", text)


class TelegramNotifierTest(unittest.IsolatedAsyncioTestCase):
    async def test_sends_message(self):
        sent = []

        def handler(request):
            sent.append((str(request.url), json.loads(request.content)))
            return httpx.Response(200, json={"ok": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            notifier = TelegramNotifier(client, TelegramSettings(token="T", chat_id="42"))
            await notifier.send("hello")

        url, payload = sent[0]
        self.assertTrue(url.endswith("/botT/sendMessage"))
        self.assertEqual(payload["chat_id"], "42")
        self.assertEqual(payload["text"], "hello")

    async def test_api_error_does_not_raise(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
            notifier = TelegramNotifier(client, TelegramSettings(token="T", chat_id="42"))
            with self.assertLogs("uptime_monitor.notifier", level="ERROR"):
                await notifier.send("hello")


if __name__ == "__main__":
    unittest.main()
