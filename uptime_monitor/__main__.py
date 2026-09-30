"""Command-line entry point: python -m uptime_monitor"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__
from .config import ConfigError, load_config
from .monitor import Monitor, make_client
from .notifier import ConsoleNotifier, TelegramNotifier


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="uptime-monitor",
        description="Async website uptime monitor with Telegram alerts.",
    )
    parser.add_argument("-c", "--config", default="config.toml", help="path to config file")
    parser.add_argument("--once", action="store_true",
                        help="check every site once, print a report and exit (exit code 1 if any site is down)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)

    async with make_client() as client:
        if config.telegram:
            notifier = TelegramNotifier(client, config.telegram)
        else:
            logging.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set, alerts go to console")
            notifier = ConsoleNotifier()

        monitor = Monitor(config, client, notifier)

        if args.once:
            results = await monitor.run_once()
            width = max(len(name) for name in results)
            for name, r in results.items():
                if r.ok:
                    print(f"✅ {name:<{width}}  {r.status}  {r.latency_ms:.0f} ms")
                else:
                    print(f"❌ {name:<{width}}  {r.error}")
            return 0 if all(r.ok for r in results.values()) else 1

        await monitor.run_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # hide per-request logs (and bot token in URLs)

    try:
        return asyncio.run(run(args))
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
