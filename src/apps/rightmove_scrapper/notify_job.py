"""
Notify job — run by cron after the poll job.

Reads src/config/jobs.yaml, checks each config's paired database for
properties discovered since the previous scraper run, prints them, and
(if telegram.channel_id is set in jobs.yaml) sends them to Telegram.

Usage:
    uv run python -m src.apps.rightmove_scrapper.notify_job
    uv run python -m src.apps.rightmove_scrapper.notify_job --jobs src/config/jobs.yaml
"""

import argparse
import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.clients.telegram import TelegramClient
from src.store.rightmove import RightmoveStore, StoredProperty

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DEFAULT_JOBS = Path(__file__).parents[2] / "config" / "jobs.yaml"


def _load_jobs(jobs_path: Path) -> tuple[list[Path], str | None]:
    """Return (config_paths, telegram_channel_id_or_None)."""
    raw = yaml.safe_load(jobs_path.read_text())
    root = jobs_path.parents[2]
    config_paths = [root / p for p in raw.get("configs", [])]
    channel_id = raw.get("telegram", {}).get("channel_id") or None
    return config_paths, channel_id


def _format(prop: StoredProperty) -> str:
    price = f"£{prop.price_amount:,}" if prop.price_amount else "?"
    if prop.price_frequency:
        price += f" {prop.price_frequency}"
    beds = f"{prop.bedrooms}bd" if prop.bedrooms is not None else "?bd"
    found = prop.discovered_at.strftime("%d %b %H:%M UTC")
    listed = prop.listing_date.isoformat() if prop.listing_date else "unknown"
    return (
        f"  [{beds}] {prop.display_address or 'Unknown'} — {price}\n"
        f"         Listed: {listed}  |  Discovered: {found}\n"
        f"         {prop.url}"
    )


async def check_config(config_path: Path) -> list[StoredProperty]:
    db_path = RightmoveStore.db_path_for_config(config_path)
    if not db_path.exists():
        log.warning("No database for %s — has the poll job run yet?", config_path.name)
        return []

    async with RightmoveStore(db_path) as store:
        last_run = await store.last_run_at()
        new_props = await store.get_new_since_last_run()

    if last_run:
        log.info(
            "%s: last run %s — %d new listing(s)",
            config_path.stem, last_run.strftime("%d %b %H:%M UTC"), len(new_props),
        )
    else:
        log.info("%s: no runs recorded yet", config_path.stem)

    return new_props


async def run(jobs_path: Path) -> None:
    config_paths, channel_id = _load_jobs(jobs_path)
    if not config_paths:
        log.warning("No configs in %s", jobs_path)
        return

    checked_at = datetime.now(UTC).strftime("%d %b %Y %H:%M UTC")
    log.info("Notify check at %s", checked_at)

    # Collect new listings grouped by config stem
    groups: dict[str, list[StoredProperty]] = {}
    for config_path in config_paths:
        props = await check_config(config_path)
        if props:
            groups[config_path.stem] = props

    total = sum(len(v) for v in groups.values())
    if not total:
        log.info("No new listings found.")
        return

    # --- print to stdout ---
    print(f"\n{'=' * 60}")
    print(f"  NEW LISTINGS  —  {checked_at}")
    print(f"{'=' * 60}\n")
    for config_stem, props in groups.items():
        print(f"[ {config_stem} ]")
        for prop in props:
            print(_format(prop))
            print()
    print(f"{'=' * 60}")
    print(f"  {total} new listing(s) total")
    print(f"{'=' * 60}\n")

    # --- send to Telegram ---
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not channel_id:
        log.info("Telegram channel_id not set in jobs.yaml — skipping Telegram notifications.")
        return
    if not bot_token:
        log.warning("TELEGRAM_BOT_TOKEN not set in environment — skipping Telegram notifications.")
        return

    log.info("Sending %d listing(s) to Telegram channel %s", total, channel_id)
    async with TelegramClient(token=bot_token) as tg:
        for config_stem, props in groups.items():
            sent = await tg.send_listings_batch(
                chat_id=channel_id,
                properties=props,
                search_name=config_stem.removeprefix("rightmove_"),
            )
            log.info("  Sent %d/%d messages for %s", sent, len(props), config_stem)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rightmove notify job")
    parser.add_argument("--jobs", type=Path, default=DEFAULT_JOBS)
    args = parser.parse_args()
    asyncio.run(run(args.jobs))


if __name__ == "__main__":
    main()
