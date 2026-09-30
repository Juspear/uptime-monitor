"""Command-line entry point: python -m uptime_monitor"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from . import __version__
from .config import ConfigError, load_config
from .monitor import Monitor, make_client
from .notifier import ConsoleNotifier, TelegramNotifier
from .storage import History


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="uptime-monitor",
        description="Async website uptime monitor with Telegram alerts.",
    )
    parser.add_argument("-c", "--config", default="config.toml", help="path to config file")
    parser.add_argument("--once", action="store_true",
                        help="check every site once, print a report and exit (exit code 1 if any site is down)")
    parser.add_argument("--stats", action="store_true",
                        help="print uptime statistics from the stored history and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def _fmt(value: float | None, pattern: str) -> str:
    return "—" if value is None else pattern.format(value)


async def print_stats(config) -> int:
    if not config.storage.path:
        print("history is disabled ([storage] path is empty)", file=sys.stderr)
        return 2
    history = History(config.storage.path)
    try:
        now = time.time()
        periods = [("24h", 86400), ("7d", 7 * 86400), ("30d", 30 * 86400)]
        width = max(len(s.name) for s in config.sites)
        header = f"{'site':<{width}}  " + "  ".join(f"{p:>7}" for p, _ in periods) + "  avg ms  max ms  incidents(24h)"
        print(header)
        print("-" * len(header))
        for site in config.sites:
            cells = []
            for _, seconds in periods:
                st = await history.stats(site.name, now - seconds)
                cells.append(f"{_fmt(st.uptime_percent, '{:6.2f}%'):>7}")
            day = await history.stats(site.name, now - 86400)
            print(f"{site.name:<{width}}  " + "  ".join(cells)
                  + f"  {_fmt(day.avg_latency_ms, '{:6.0f}'):>6}  {_fmt(day.max_latency_ms, '{:6.0f}'):>6}"
                  + f"  {day.incidents:>14}")
    finally:
        history.close()
    return 0


async def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)

    if args.stats:
        return await print_stats(config)

    async with make_client() as client:
        if config.telegram:
            notifier = TelegramNotifier(client, config.telegram)
        else:
            logging.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set, alerts go to console")
            notifier = ConsoleNotifier()

        monitor = Monitor(config, client, notifier)

        if args.once:
            reports = await monitor.run_once()
            sites = {s.name: s for s in config.sites}
            width = max(len(name) for name in reports)
            problems = 0
            for name, rep in reports.items():
                r = rep.result
                if r.ok:
                    line = f"✅ {name:<{width}}  {r.status}  {r.latency_ms:5.0f} ms"
                else:
                    line = f"❌ {name:<{width}}  {r.error}"
                    problems += 1

                if rep.ssl_days_left is not None:
                    line += f"  | SSL: {rep.ssl_days_left} days left"
                    if rep.ssl_days_left <= sites[name].ssl_expiry_warning_days:
                        line += " ⚠️"
                        problems += 1
                elif rep.ssl_error and r.ok:
                    line += f"  | SSL: could not read ({rep.ssl_error})"
                print(line)
            return 0 if problems == 0 else 1

        if config.storage.path:
            monitor.history = History(config.storage.path)
            logging.info("saving check history to %s", config.storage.path)
        try:
            await monitor.run_forever()
        finally:
            if monitor.history is not None:
                monitor.history.close()
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
