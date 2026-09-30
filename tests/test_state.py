import unittest

from uptime_monitor.checker import CheckResult
from uptime_monitor.state import EventType, SiteState

OK = CheckResult(ok=True, status=200, latency_ms=50)
FAIL = CheckResult(ok=False, error="timeout")


class SiteStateTest(unittest.TestCase):
    def test_single_failure_does_not_alert(self):
        state = SiteState("site", failures_before_alert=2)
        self.assertIsNone(state.update(FAIL, now=0))
        self.assertFalse(state.is_down)

    def test_alerts_after_threshold(self):
        state = SiteState("site", failures_before_alert=2)
        state.update(FAIL, now=0)
        event = state.update(FAIL, now=60)
        self.assertEqual(event.type, EventType.DOWN)
        self.assertTrue(state.is_down)

    def test_down_alert_sent_only_once(self):
        state = SiteState("site", failures_before_alert=1)
        self.assertIsNotNone(state.update(FAIL, now=0))
        self.assertIsNone(state.update(FAIL, now=60))
        self.assertIsNone(state.update(FAIL, now=120))

    def test_recovery_reports_downtime_from_first_failure(self):
        state = SiteState("site", failures_before_alert=2)
        state.update(FAIL, now=100)
        state.update(FAIL, now=160)
        event = state.update(OK, now=400)
        self.assertEqual(event.type, EventType.RECOVERED)
        self.assertEqual(event.downtime_seconds, 300)
        self.assertFalse(state.is_down)

    def test_success_resets_failure_counter(self):
        state = SiteState("site", failures_before_alert=2)
        state.update(FAIL, now=0)
        state.update(OK, now=60)
        self.assertIsNone(state.update(FAIL, now=120))
        self.assertEqual(state.consecutive_failures, 1)

    def test_uptime_percent(self):
        state = SiteState("site", failures_before_alert=5)
        self.assertEqual(state.uptime_percent, 100.0)
        for r in (OK, OK, OK, FAIL):
            state.update(r, now=0)
        self.assertEqual(state.uptime_percent, 75.0)


if __name__ == "__main__":
    unittest.main()
