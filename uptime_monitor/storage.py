"""Check history stored in SQLite.

sqlite3 is blocking, so every call runs in a worker thread (asyncio.to_thread)
and is serialized with a lock: the event loop never waits on the disk.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from .checker import CheckResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id          INTEGER PRIMARY KEY,
    site        TEXT    NOT NULL,
    ts          REAL    NOT NULL,   -- unix time, seconds
    ok          INTEGER NOT NULL,   -- 1 = up, 0 = down
    status      INTEGER,
    latency_ms  REAL,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS checks_site_ts ON checks (site, ts);
"""


@dataclass(frozen=True)
class FailureStreak:
    """The unbroken run of failed checks at the end of a site's history."""
    count: int
    started_at: float | None


@dataclass(frozen=True)
class SiteStats:
    checks: int
    uptime_percent: float | None
    avg_latency_ms: float | None
    max_latency_ms: float | None
    incidents: int  # transitions from up to down


class History:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- sync implementations (run in a worker thread) -----------------

    def _record(self, site: str, ts: float, r: CheckResult) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO checks (site, ts, ok, status, latency_ms, error) VALUES (?, ?, ?, ?, ?, ?)",
                (site, ts, int(r.ok), r.status, r.latency_ms, r.error),
            )

    def _failure_streak(self, site: str) -> FailureStreak:
        with self._lock:
            last_ok = self._conn.execute(
                "SELECT MAX(ts) FROM checks WHERE site = ? AND ok = 1", (site,)
            ).fetchone()[0]
            count, started = self._conn.execute(
                "SELECT COUNT(*), MIN(ts) FROM checks WHERE site = ? AND ok = 0 AND ts > ?",
                (site, last_ok if last_ok is not None else float("-inf")),
            ).fetchone()
        return FailureStreak(count, started)

    def _stats(self, site: str, since: float) -> SiteStats:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ok, latency_ms FROM checks WHERE site = ? AND ts >= ? ORDER BY ts",
                (site, since),
            ).fetchall()
        if not rows:
            return SiteStats(0, None, None, None, 0)

        latencies = [lat for ok, lat in rows if ok and lat is not None]
        incidents = sum(1 for (prev, _), (cur, _) in zip(rows, rows[1:]) if prev and not cur)
        if not rows[0][0]:
            incidents += 1  # the period started while the site was already down
        return SiteStats(
            checks=len(rows),
            uptime_percent=100.0 * sum(ok for ok, _ in rows) / len(rows),
            avg_latency_ms=sum(latencies) / len(latencies) if latencies else None,
            max_latency_ms=max(latencies) if latencies else None,
            incidents=incidents,
        )

    def _prune(self, older_than: float) -> int:
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM checks WHERE ts < ?", (older_than,)).rowcount

    # ---- async API -----------------------------------------------------

    async def record(self, site: str, ts: float, result: CheckResult) -> None:
        await asyncio.to_thread(self._record, site, ts, result)

    async def failure_streak(self, site: str) -> FailureStreak:
        return await asyncio.to_thread(self._failure_streak, site)

    async def stats(self, site: str, since: float) -> SiteStats:
        return await asyncio.to_thread(self._stats, site, since)

    async def prune(self, older_than: float) -> int:
        return await asyncio.to_thread(self._prune, older_than)
