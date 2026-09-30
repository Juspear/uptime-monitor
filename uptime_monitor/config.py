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
    ssl_expiry_warning_days: int = 14  # warn when the certificate expires sooner (0 = off)

    @property
    def checks_ssl(self) -> bool:
        return self.url.startswith("https://") and self.ssl_expiry_warning_days > 0


@dataclass(frozen=True)
class TelegramSettings:
    token: str
    chat_id: str


@dataclass(frozen=True)
class StorageSettings:
    path: str | None = "data/uptime.db"  # None = history disabled
    retention_days: int = 90


@dataclass(frozen=True)
class Config:
    sites: list[Site]
    telegram: TelegramSettings | None
    storage: StorageSettings = StorageSettings()


_SITE_FIELDS = {
    "name", "url", "interval", "timeout",
    "expected_status", "keyword", "failures_before_alert",
    "ssl_expiry_warning_days",
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
    if site.ssl_expiry_warning_days < 0:
        raise ConfigError(f"{site.name}: ssl_expiry_warning_days must be >= 0")
    return site


def _telegram_from_env() -> TelegramSettings | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        return TelegramSettings(token=token, chat_id=chat_id)
    return None


def _parse_storage(raw: dict) -> StorageSettings:
    unknown = set(raw) - {"path", "retention_days"}
    if unknown:
        raise ConfigError(f"[storage]: unknown field(s): {', '.join(sorted(unknown))}")
    path = raw.get("path", StorageSettings.path)
    if not isinstance(path, str):
        raise ConfigError("[storage]: path must be a string")
    retention = raw.get("retention_days", StorageSettings.retention_days)
    if not isinstance(retention, int) or retention < 1:
        raise ConfigError("[storage]: retention_days must be a positive integer")
    return StorageSettings(path=path or None, retention_days=retention)


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

    storage = _parse_storage(data.get("storage", {}))
    return Config(sites=sites, telegram=_telegram_from_env(), storage=storage)


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
