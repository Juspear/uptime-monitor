import asyncio
import ssl
import unittest
from datetime import datetime, timedelta, timezone

import httpx

from uptime_monitor.config import parse_config
from uptime_monitor.monitor import Monitor


class FakeNotifier:
    def __init__(self):
        self.messages = []

    async def send(self, text):
        self.messages.append(text)


def make_monitor(site_overrides=None, handler=None, cert_fetcher=None):
    site = {"name": "Shop", "url": "https://shop.example.com", "interval": 5}
    site.update(site_overrides or {})
    config = parse_config({"sites": [site]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler or (lambda r: httpx.Response(200))))
    notifier = FakeNotifier()
    monitor = Monitor(config, client, notifier, cert_fetcher=cert_fetcher or expiring_in(90))
    return monitor, notifier, client


def expiring_in(days):
    async def fetch(url, timeout):
        return datetime.now(timezone.utc) + timedelta(days=days, hours=1)
    return fetch


class SslCheckTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_alert_when_far_from_expiry(self):
        monitor, notifier, client = make_monitor(cert_fetcher=expiring_in(60))
        async with client:
            days = await monitor.check_ssl_once(monitor.config.sites[0])
        self.assertEqual(days, 60)
        self.assertEqual(notifier.messages, [])

    async def test_alert_once_when_expiring(self):
        monitor, notifier, client = make_monitor(cert_fetcher=expiring_in(5))
        site = monitor.config.sites[0]
        async with client:
            await monitor.check_ssl_once(site)
            await monitor.check_ssl_once(site)
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("expires in 5 days", notifier.messages[0])

    async def test_warns_again_after_renewal(self):
        monitor, notifier, client = make_monitor(cert_fetcher=expiring_in(5))
        site = monitor.config.sites[0]
        async with client:
            await monitor.check_ssl_once(site)
            monitor.cert_fetcher = expiring_in(90)   # renewed
            await monitor.check_ssl_once(site)
            monitor.cert_fetcher = expiring_in(3)    # expiring again later
            await monitor.check_ssl_once(site)
        self.assertEqual(len(notifier.messages), 2)

    async def test_custom_threshold(self):
        monitor, notifier, client = make_monitor({"ssl_expiry_warning_days": 30}, cert_fetcher=expiring_in(20))
        async with client:
            await monitor.check_ssl_once(monitor.config.sites[0])
        self.assertEqual(len(notifier.messages), 1)

    async def test_unreadable_certificate_is_logged_not_alerted(self):
        async def broken(url, timeout):
            raise ssl.SSLCertVerificationError("certificate has expired")

        monitor, notifier, client = make_monitor(cert_fetcher=broken)
        async with client:
            with self.assertLogs("uptime_monitor.monitor", level="WARNING"):
                days = await monitor.check_ssl_once(monitor.config.sites[0])
        self.assertIsNone(days)
        self.assertEqual(notifier.messages, [])

    def test_ssl_disabled_for_http_and_zero_days(self):
        http_site = parse_config({"sites": [{"url": "http://a.com"}]}).sites[0]
        off_site = parse_config({"sites": [{"url": "https://a.com", "ssl_expiry_warning_days": 0}]}).sites[0]
        self.assertFalse(http_site.checks_ssl)
        self.assertFalse(off_site.checks_ssl)


class RunOnceTest(unittest.IsolatedAsyncioTestCase):
    async def test_reports_status_and_ssl(self):
        monitor, _, client = make_monitor(cert_fetcher=expiring_in(40))
        async with client:
            reports = await monitor.run_once()
        report = reports["Shop"]
        self.assertTrue(report.result.ok)
        self.assertEqual(report.ssl_days_left, 40)


class RunForeverTest(unittest.IsolatedAsyncioTestCase):
    async def test_stops_cleanly(self):
        monitor, _, client = make_monitor()
        async with client:
            task = asyncio.create_task(monitor.run_forever())
            await asyncio.sleep(0.1)
            monitor.stop()
            await asyncio.wait_for(task, timeout=2)  # must finish quickly, not hang
        self.assertTrue(task.done())

    async def test_down_alert_through_loop(self):
        monitor, notifier, client = make_monitor(
            {"failures_before_alert": 1}, handler=lambda r: httpx.Response(503))
        async with client:
            task = asyncio.create_task(monitor.run_forever())
            await asyncio.sleep(0.2)
            monitor.stop()
            await asyncio.wait_for(task, timeout=2)
        self.assertTrue(any("DOWN" in m for m in notifier.messages))


if __name__ == "__main__":
    unittest.main()
