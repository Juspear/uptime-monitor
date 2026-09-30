import os
import tempfile
import unittest

import httpx

from uptime_monitor.checker import CheckResult
from uptime_monitor.config import ConfigError, parse_config
from uptime_monitor.monitor import Monitor
from uptime_monitor.storage import History

OK = CheckResult(ok=True, status=200, latency_ms=100)
SLOW = CheckResult(ok=True, status=200, latency_ms=300)
FAIL = CheckResult(ok=False, error="timeout")


class HistoryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.history = History(":memory:")

    async def asyncTearDown(self):
        self.history.close()

    async def test_failure_streak_after_last_success(self):
        for ts, r in [(1, FAIL), (2, OK), (3, FAIL), (4, FAIL)]:
            await self.history.record("a", ts, r)
        streak = await self.history.failure_streak("a")
        self.assertEqual(streak.count, 2)
        self.assertEqual(streak.started_at, 3)

    async def test_no_streak_when_last_check_ok(self):
        await self.history.record("a", 1, FAIL)
        await self.history.record("a", 2, OK)
        streak = await self.history.failure_streak("a")
        self.assertEqual(streak.count, 0)
        self.assertIsNone(streak.started_at)

    async def test_streak_with_no_successes_at_all(self):
        await self.history.record("a", 5, FAIL)
        await self.history.record("a", 6, FAIL)
        self.assertEqual((await self.history.failure_streak("a")).count, 2)

    async def test_sites_are_separate(self):
        await self.history.record("a", 1, FAIL)
        await self.history.record("b", 1, OK)
        self.assertEqual((await self.history.failure_streak("b")).count, 0)

    async def test_stats(self):
        for ts, r in [(10, OK), (20, SLOW), (30, FAIL), (40, OK), (50, FAIL), (60, FAIL)]:
            await self.history.record("a", ts, r)
        st = await self.history.stats("a", since=0)
        self.assertEqual(st.checks, 6)
        self.assertEqual(st.uptime_percent, 50.0)
        self.assertAlmostEqual(st.avg_latency_ms, (100 + 300 + 100) / 3)
        self.assertEqual(st.max_latency_ms, 300)
        self.assertEqual(st.incidents, 2)

    async def test_stats_respects_since_and_counts_ongoing_incident(self):
        for ts, r in [(10, OK), (20, FAIL), (30, FAIL), (40, OK)]:
            await self.history.record("a", ts, r)
        st = await self.history.stats("a", since=25)   # window starts mid-outage
        self.assertEqual(st.checks, 2)
        self.assertEqual(st.incidents, 1)

    async def test_stats_empty(self):
        st = await self.history.stats("nothing", since=0)
        self.assertEqual(st.checks, 0)
        self.assertIsNone(st.uptime_percent)

    async def test_prune(self):
        for ts in (1, 2, 3, 100):
            await self.history.record("a", ts, OK)
        self.assertEqual(await self.history.prune(older_than=50), 3)
        self.assertEqual((await self.history.stats("a", since=0)).checks, 1)

    async def test_persists_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "uptime.db")   # parent dir is created
            h = History(path)
            await h.record("a", 1, FAIL)
            h.close()
            h = History(path)
            self.assertEqual((await h.failure_streak("a")).count, 1)
            h.close()


class FakeNotifier:
    def __init__(self):
        self.messages = []

    async def send(self, text):
        self.messages.append(text)


class RestartTest(unittest.IsolatedAsyncioTestCase):
    """The monitor must not repeat a DOWN alert after being restarted."""

    def make(self, history, handler, now):
        config = parse_config({"sites": [
            {"name": "Shop", "url": "https://shop.example.com", "failures_before_alert": 2,
             "ssl_expiry_warning_days": 0}]})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clock = iter(now)
        return Monitor(config, client, FakeNotifier(), history=history, clock=lambda: next(clock)), client

    async def test_no_duplicate_down_alert_and_correct_downtime(self):
        history = History(":memory:")
        down = lambda r: httpx.Response(503)
        up = lambda r: httpx.Response(200)

        # first run: site goes down, one DOWN alert
        m1, c1 = self.make(history, down, now=[1000, 1060])
        async with c1:
            await m1.check_once(m1.config.sites[0])
            await m1.check_once(m1.config.sites[0])
        self.assertEqual(len(m1.notifier.messages), 1)

        # restart: still down -> no second alert; then it recovers
        m2, c2 = self.make(history, down, now=[1500])
        async with c2:
            await m2.restore_state()
            self.assertTrue(m2.states["Shop"].is_down)
            await m2.check_once(m2.config.sites[0])
            m2.client = httpx.AsyncClient(transport=httpx.MockTransport(up))
            m2.clock = lambda: 1900
            async with m2.client:
                await m2.check_once(m2.config.sites[0])

        self.assertEqual(len(m2.notifier.messages), 1)          # only RECOVERED
        self.assertIn("back UP", m2.notifier.messages[0])
        self.assertIn("15m 0s", m2.notifier.messages[0])        # 1900 - 1000 seconds
        history.close()

    async def test_history_write_failure_does_not_stop_checks(self):
        class Broken:
            async def record(self, *a):
                raise OSError("disk full")

        m, c = self.make(Broken(), lambda r: httpx.Response(200), now=[1])
        async with c:
            with self.assertLogs("uptime_monitor.monitor", level="ERROR"):
                result = await m.check_once(m.config.sites[0])
        self.assertTrue(result.ok)


class StorageConfigTest(unittest.TestCase):
    base = {"sites": [{"url": "https://a.com"}]}

    def test_defaults(self):
        cfg = parse_config(self.base)
        self.assertEqual(cfg.storage.path, "data/uptime.db")
        self.assertEqual(cfg.storage.retention_days, 90)

    def test_empty_path_disables(self):
        cfg = parse_config({**self.base, "storage": {"path": ""}})
        self.assertIsNone(cfg.storage.path)

    def test_invalid(self):
        for storage in ({"retention_days": 0}, {"path": 5}, {"typo": 1}):
            with self.subTest(storage=storage), self.assertRaises(ConfigError):
                parse_config({**self.base, "storage": storage})


if __name__ == "__main__":
    unittest.main()
