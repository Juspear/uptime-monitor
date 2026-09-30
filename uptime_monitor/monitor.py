"""Runs checks for all sites concurrently, each on its own schedule."""

from __future__ import annotations

import asyncio
import logging
import signal
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

import httpx

from .checker import CheckResult, check_site
from .config import Config, Site
from .notifier import Notifier, format_event
from .ssl_check import fetch_cert_expiry
from .state import SiteState
from .storage import History

log = logging.getLogger(__name__)

USER_AGENT = "uptime-monitor/1.2 (+https://github.com/Juspear/uptime-monitor)"
SSL_CHECK_INTERVAL = 6 * 3600  # certificates change rarely, no need to check often
PRUNE_INTERVAL = 24 * 3600     # delete old history once a day

CertFetcher = Callable[[str, float], Awaitable[datetime]]


@dataclass(frozen=True)
class OnceReport:
    result: CheckResult
    ssl_days_left: int | None = None
    ssl_error: str | None = None


class Monitor:
    def __init__(
        self,
        config: Config,
        client: httpx.AsyncClient,
        notifier: Notifier,
        cert_fetcher: CertFetcher = fetch_cert_expiry,
        history: History | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.config = config
        self.client = client
        self.notifier = notifier
        self.cert_fetcher = cert_fetcher
        self.history = history
        self.clock = clock
        self.states = {
            site.name: SiteState(site.name, site.failures_before_alert) for site in config.sites
        }
        self._stop = asyncio.Event()

    # ---- single checks -------------------------------------------------

    async def check_once(self, site: Site) -> CheckResult:
        result = await check_site(self.client, site)
        now = self.clock()
        state = self.states[site.name]
        event = state.update(result, now=now)

        if self.history is not None:
            try:
                await self.history.record(site.name, now, result)
            except Exception:  # a disk problem must not stop monitoring
                log.exception("failed to save check result for %s", site.name)

        if result.ok:
            log.info("%-20s UP    %4d  %6.0f ms", site.name, result.status, result.latency_ms)
        else:
            log.warning("%-20s FAIL  %s", site.name, result.error)

        if event is not None:
            await self.notifier.send(format_event(event, site.url))
        return result

    async def check_ssl_once(self, site: Site) -> int | None:
        """Check the certificate expiry. Returns days left, or None if it could not be read."""
        try:
            expires_at = await self.cert_fetcher(site.url, site.timeout)
        except (OSError, ssl.SSLError, asyncio.TimeoutError) as e:
            # An invalid or expired certificate also breaks the HTTP check,
            # which already sends a DOWN alert, so only log it here.
            log.warning("%-20s SSL   could not read certificate: %s", site.name, type(e).__name__)
            return None

        now = datetime.now(timezone.utc)
        days_left = (expires_at - now).days
        log.info("%-20s SSL   %d days left", site.name, days_left)

        event = self.states[site.name].update_ssl(expires_at, now, site.ssl_expiry_warning_days)
        if event is not None:
            await self.notifier.send(format_event(event, site.url))
        return days_left

    # ---- loops ---------------------------------------------------------

    async def _every(self, seconds: float, action: Callable[[], Awaitable[object]], name: str) -> None:
        while not self._stop.is_set():
            try:
                await action()
            except Exception:  # keep the loop alive no matter what
                log.exception("unexpected error in %s", name)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            except asyncio.TimeoutError:
                pass

    async def restore_state(self) -> None:
        """After a restart, continue from the stored history instead of starting clean.

        Without this, a site that was already down would get a second DOWN alert,
        and its eventual RECOVERED message would report the wrong downtime.
        """
        if self.history is None:
            return
        for site in self.config.sites:
            streak = await self.history.failure_streak(site.name)
            state = self.states[site.name]
            state.restore(streak.count, streak.started_at)
            if state.is_down:
                log.info("%-20s was DOWN before restart (%d failed checks)", site.name, streak.count)

    async def prune_history(self) -> None:
        if self.history is None:
            return
        cutoff = self.clock() - self.config.storage.retention_days * 86400
        deleted = await self.history.prune(cutoff)
        if deleted:
            log.info("history: removed %d checks older than %d days", deleted, self.config.storage.retention_days)

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop)
            except (NotImplementedError, RuntimeError):
                pass  # e.g. Windows, or not in the main thread

        await self.restore_state()

        tasks = []
        if self.history is not None:
            tasks.append(asyncio.create_task(self._every(PRUNE_INTERVAL, self.prune_history, "prune")))
        for site in self.config.sites:
            tasks.append(asyncio.create_task(
                self._every(site.interval, lambda s=site: self.check_once(s), f"check:{site.name}")))
            if site.checks_ssl:
                tasks.append(asyncio.create_task(
                    self._every(SSL_CHECK_INTERVAL, lambda s=site: self.check_ssl_once(s), f"ssl:{site.name}")))

        log.info("monitoring %d site(s), press Ctrl+C to stop", len(self.config.sites))
        await self._stop.wait()

        log.info("stopping...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        log.info("stopped")

    async def run_once(self) -> dict[str, OnceReport]:
        """Check every site one time, concurrently. Used by --once."""

        async def one(site: Site) -> OnceReport:
            if not site.checks_ssl:
                return OnceReport(await check_site(self.client, site))

            async def ssl_days() -> tuple[int | None, str | None]:
                try:
                    expires_at = await self.cert_fetcher(site.url, site.timeout)
                except (OSError, ssl.SSLError, asyncio.TimeoutError) as e:
                    return None, type(e).__name__
                return (expires_at - datetime.now(timezone.utc)).days, None

            result, (days, error) = await asyncio.gather(check_site(self.client, site), ssl_days())
            return OnceReport(result, days, error)

        reports = await asyncio.gather(*(one(s) for s in self.config.sites))
        return {site.name: r for site, r in zip(self.config.sites, reports)}


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": USER_AGENT})
