"""A single HTTP availability check."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .config import Site


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    status: int | None = None
    latency_ms: float | None = None
    error: str | None = None


async def check_site(client: httpx.AsyncClient, site: Site) -> CheckResult:
    """Request the site once and decide whether it is up."""
    started = time.perf_counter()
    try:
        response = await client.get(site.url, timeout=site.timeout, follow_redirects=True)
    except httpx.TimeoutException:
        return CheckResult(ok=False, error=f"timeout after {site.timeout:g}s")
    except httpx.HTTPError as e:
        return CheckResult(ok=False, error=f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)

    latency_ms = (time.perf_counter() - started) * 1000

    if response.status_code != site.expected_status:
        return CheckResult(
            ok=False,
            status=response.status_code,
            latency_ms=latency_ms,
            error=f"expected HTTP {site.expected_status}, got {response.status_code}",
        )

    if site.keyword and site.keyword not in response.text:
        return CheckResult(
            ok=False,
            status=response.status_code,
            latency_ms=latency_ms,
            error=f"keyword {site.keyword!r} not found on page",
        )

    return CheckResult(ok=True, status=response.status_code, latency_ms=latency_ms)
