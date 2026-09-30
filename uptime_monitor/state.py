"""Tracks up/down state per site and decides when to send alerts.

A site is reported DOWN only after `failures_before_alert` failures in a row,
so a single network hiccup does not wake anyone up. Once a site is down,
the first successful check produces a RECOVERED event with the downtime.

SSL expiry is tracked separately: one SSL_EXPIRING warning per certificate,
which resets once the certificate is renewed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .checker import CheckResult


class EventType(Enum):
    DOWN = "down"
    RECOVERED = "recovered"
    SSL_EXPIRING = "ssl_expiring"


@dataclass(frozen=True)
class Event:
    type: EventType
    site_name: str
    result: CheckResult | None = None
    downtime_seconds: float | None = None
    ssl_days_left: int | None = None
    ssl_expires_at: datetime | None = None


@dataclass
class SiteState:
    site_name: str
    failures_before_alert: int
    consecutive_failures: int = 0
    is_down: bool = False
    down_since: float | None = None
    checks_total: int = 0
    checks_ok: int = 0
    last_result: CheckResult | None = field(default=None, repr=False)
    ssl_warned: bool = False

    def update(self, result: CheckResult, now: float) -> Event | None:
        """Apply a new check result. Returns an event if an alert should be sent."""
        self.checks_total += 1
        self.last_result = result

        if result.ok:
            self.checks_ok += 1
            self.consecutive_failures = 0
            if self.is_down:
                downtime = now - self.down_since if self.down_since is not None else None
                self.is_down = False
                self.down_since = None
                return Event(EventType.RECOVERED, self.site_name, result, downtime)
            self.down_since = None
            return None

        self.consecutive_failures += 1
        if self.consecutive_failures == 1:
            self.down_since = now  # remember when the problem started
        if not self.is_down and self.consecutive_failures >= self.failures_before_alert:
            self.is_down = True
            return Event(EventType.DOWN, self.site_name, result)
        return None

    def update_ssl(self, expires_at: datetime, now: datetime, warn_days: int) -> Event | None:
        """Apply a certificate check. Returns an event the first time it is close to expiry."""
        days_left = (expires_at - now).days
        if days_left <= warn_days:
            if not self.ssl_warned:
                self.ssl_warned = True
                return Event(EventType.SSL_EXPIRING, self.site_name,
                             ssl_days_left=days_left, ssl_expires_at=expires_at)
        else:
            self.ssl_warned = False  # certificate was renewed, warn again next time
        return None

    def restore(self, consecutive_failures: int, first_failure_at: float | None) -> None:
        """Rebuild the state after a restart from the stored failure streak."""
        self.consecutive_failures = consecutive_failures
        self.down_since = first_failure_at if consecutive_failures else None
        self.is_down = consecutive_failures >= self.failures_before_alert

    @property
    def uptime_percent(self) -> float:
        if self.checks_total == 0:
            return 100.0
        return 100.0 * self.checks_ok / self.checks_total
