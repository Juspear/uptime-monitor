"""Reading the expiry date of a site's TLS certificate."""

from __future__ import annotations

import asyncio
import ssl
from datetime import datetime, timezone
from urllib.parse import urlsplit


async def fetch_cert_expiry(url: str, timeout: float) -> datetime:
    """Open a TLS connection to the site and return when its certificate expires.

    Raises OSError / ssl.SSLError / asyncio.TimeoutError if the certificate
    cannot be read (for example, it is already expired or invalid).
    """
    parts = urlsplit(url)
    host = parts.hostname
    port = parts.port or 443
    context = ssl.create_default_context()

    _, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=context, server_hostname=host),
        timeout=timeout,
    )
    try:
        cert = writer.get_extra_info("peercert")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ssl.SSLError):
            pass

    return datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=timezone.utc)
