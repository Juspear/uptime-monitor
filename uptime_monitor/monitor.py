"""Runs checks for all sites concurrently, each on its own schedule."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .checker import CheckResult, check_site
from .config import Config, Site
from .notifier import Notifier, format_event
from .state import SiteState

log = logging.getLogger(__name__)

USER_AGENT = "uptime-monitor/1.0 (+https://github.com/Juspear/uptime-monitor)"


class Monitor:
    def __init__(self, config: Config, client: httpx.AsyncClient, notifier: Notifier):
        self.config = config
        self.client = client
        self.notifier = notifier
        self.states = {
            site.name: SiteState(site.name, site.failures_before_alert) for site in config.sites
        }

    async def check_once(self, site: Site) -> CheckResult:
        result = await check_site(self.client, site)
        state = self.states[site.name]
        event = state.update(result, now=time.monotonic())

        if result.ok:
            log.info("%-20s UP    %4d  %6.0f ms", site.name, result.status, result.latency_ms)
        else:
            log.warning("%-20s FAIL  %s", site.name, result.error)

        if event is not None:
            await self.notifier.send(format_event(event, site.url))
        return result

    async def _watch(self, site: Site) -> None:
        while True:
            try:
                await self.check_once(site)
            except Exception:  # keep the loop alive no matter what
                log.exception("unexpected error while checking %s", site.name)
            await asyncio.sleep(site.interval)

    async def run_forever(self) -> None:
        log.info("monitoring %d site(s)", len(self.config.sites))
        async with asyncio.TaskGroup() as tg:
            for site in self.config.sites:
                tg.create_task(self._watch(site), name=f"watch:{site.name}")

    async def run_once(self) -> dict[str, CheckResult]:
        """Check every site one time, concurrently. Used by --once."""
        results = await asyncio.gather(*(check_site(self.client, s) for s in self.config.sites))
        return {site.name: r for site, r in zip(self.config.sites, results)}


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": USER_AGENT})
