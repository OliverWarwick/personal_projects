"""
Poll job — run by cron every 15 minutes.

Reads src/config/jobs.yaml, scrapes Rightmove for each listed config,
and persists results to that config's paired SQLite database.

Usage:
    uv run python -m src.apps.rightmove_scrapper.poll_job
    uv run python -m src.apps.rightmove_scrapper.poll_job --jobs src/config/jobs.yaml
"""

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml

from src.apps.rightmove_scrapper.config import load_config
from src.clients.rightmove import RightmoveClient
from src.store.features import extract_from_rightmove
from src.store.rightmove import RightmoveStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DEFAULT_JOBS = Path(__file__).parents[2] / "config" / "jobs.yaml"


def _load_jobs(jobs_path: Path) -> list[Path]:
    raw = yaml.safe_load(jobs_path.read_text())
    root = jobs_path.parents[2]  # project root
    return [root / p for p in raw.get("configs", [])]


async def poll_config(config_path: Path) -> dict:
    """
    Scrape all searches defined in `config_path` and store results.
    Returns a summary dict {search_name: new_count}.
    """
    config = load_config(config_path)
    db_path = RightmoveStore.db_path_for_config(config_path)
    user = db_path.stem.removeprefix("rightmove_")
    summary: dict[str, int] = {}

    async with RightmoveStore(db_path) as store, RightmoveClient(request_delay=1.5) as client:
        for search in config.searches:
            log.info("Searching: %s (%s)", search.name, search.region)
            try:
                results = await client.search(
                    location_identifier=search.location_identifier,
                    channel=search.channel,
                    min_price=search.min_price,
                    max_price=search.max_price,
                    min_bedrooms=search.min_bedrooms,
                    max_bedrooms=search.max_bedrooms,
                    property_types=search.property_types or None,
                    min_area_size=search.min_area_size,
                    max_area_size=search.max_area_size,
                    area_size_unit=search.area_size_unit,
                    sort_type=search.sort_type,
                    radius=search.radius,
                    added_to_site=search.added_to_site,
                    furnish_types=search.furnish_types or None,
                    let_type=search.let_type,
                    must_have=search.must_have or None,
                    dont_show=search.dont_show or None,
                    include_sstc=search.include_sstc,
                    new_homes_only=search.new_homes_only,
                )
                new_count = await store.upsert_properties(
                    results.properties,
                    search_name=search.name,
                    location_identifier=search.location_identifier,
                )
                await store.record_run(
                    search_name=search.name,
                    total_found=results.total_results,
                    new_count=new_count,
                )
                await store.upsert_features_bulk(
                    [(p.rightmove_url, extract_from_rightmove(p)) for p in results.properties],
                    source="rightmove",
                    user=user,
                )
                log.info(
                    "  %s: %d on Rightmove | %d fetched | %d new",
                    search.name, results.total_results, len(results.properties), new_count,
                )
                summary[search.name] = new_count

            except Exception:
                log.exception("  Failed to scrape '%s'", search.name)
                summary[search.name] = -1

    return summary


async def run(jobs_path: Path) -> None:
    config_paths = _load_jobs(jobs_path)
    if not config_paths:
        log.warning("No configs found in %s", jobs_path)
        return

    started = datetime.now(UTC)
    log.info("Poll started at %s — %d config(s)", started.strftime("%H:%M UTC"), len(config_paths))

    total_new = 0
    for config_path in config_paths:
        log.info("Config: %s", config_path)
        summary = await poll_config(config_path)
        total_new += sum(v for v in summary.values() if v >= 0)

    log.info("Poll complete — %d new listing(s) across all configs", total_new)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rightmove poll job")
    parser.add_argument("--jobs", type=Path, default=DEFAULT_JOBS)
    args = parser.parse_args()
    asyncio.run(run(args.jobs))


if __name__ == "__main__":
    main()
