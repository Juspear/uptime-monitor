"""Loading and validating the monitor configuration (TOML)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Raised when the configuration file is missing or invalid."""


@dataclass(frozen=True)
class Site:
    name: str
    url: str
    interval: float = 60.0          # seconds between checks
    timeout: float = 10.0           # request timeout in seconds
    expected_status: int = 200      # HTTP status that counts as "up"
    keyword: str | None = None      # text that must appear in the response body
    failures_before_alert: int = 2  # consecutive failures before a "down" alert


@dataclass(frozen=True)
class TelegramSettings:
    token: str
    chat_id: str


@dataclass(frozen=True)
class Config:
    sites: list[Site]
    telegram: TelegramSettings | None


_SITE_FIELDS = {
    "name", "url", "interval", "timeout",
    "expected_status", "keyword", "failures_before_alert",
}


def _parse_site(raw: dict, defaults: dict, index: int) -> Site:
    unknown = set(raw) - _SITE_FIELDS
    if unknown:
        raise ConfigError(f"site #{index}: unknown field(s): {', '.join(sorted(unknown))}")

    data = {**defaults, **raw}
    url = data.get("url")
    if not url or not isinstance(url, str):
        raise ConfigError(f"site #{index}: 'url' is required")
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"site #{index}: url must start with http:// or https://")

    data.setdefault("name", url)
    site = Site(**data)

    if site.interval < 5:
        raise ConfigError(f"{site.name}: interval must be at least 5 seconds")
    if site.timeout <= 0:
        raise ConfigError(f"{site.name}: timeout must be positive")
    if site.failures_before_alert < 1:
        raise ConfigError(f"{site.name}: failures_before_alert must be >= 1")
    return site


def _telegram_from_env() -> TelegramSettings | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        return TelegramSettings(token=token, chat_id=chat_id)
    return None


def parse_config(data: dict) -> Config:
    defaults = data.get("defaults", {})
    unknown_defaults = set(defaults) - (_SITE_FIELDS - {"name", "url"})
    if unknown_defaults:
        raise ConfigError(f"[defaults]: unknown field(s): {', '.join(sorted(unknown_defaults))}")

    raw_sites = data.get("sites", [])
    if not raw_sites:
        raise ConfigError("no sites configured: add at least one [[sites]] entry")

    sites = [_parse_site(raw, defaults, i) for i, raw in enumerate(raw_sites, start=1)]

    names = [s.name for s in sites]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise ConfigError(f"duplicate site name(s): {', '.join(sorted(duplicates))}")

    return Config(sites=sites, telegram=_telegram_from_env())


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e
    return parse_config(data)
