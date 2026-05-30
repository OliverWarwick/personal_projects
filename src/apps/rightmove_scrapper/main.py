"""
Rightmove scrapper — runs configured searches, stores results in SQLite,
and can report new listings.

Usage:
    # Run searches and store results
    uv run python -m src.apps.rightmove_scrapper.main

    # Use a specific config (and its paired DB)
    uv run python -m src.apps.rightmove_scrapper.main --config src/config/rightmove_ow.yaml

    # Show only properties new since the last run
    uv run python -m src.apps.rightmove_scrapper.main --new

    # Show all properties discovered since a date
    uv run python -m src.apps.rightmove_scrapper.main --since 2026-04-01

The database is stored alongside the config, in data/<config-stem>.db.
E.g. src/config/rightmove_ow.yaml -> data/rightmove_ow.db
"""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from src.apps.rightmove_scrapper.config import AppConfig, load_config
from src.clients.rightmove import RightmoveClient
from src.clients.rightmove.models import PropertySummary
from src.store.features import extract_from_rightmove
from src.store.rightmove import RightmoveStore, StoredProperty

DEFAULT_CONFIG = Path(__file__).parents[2] / "config" / "rightmove_ow.yaml"

_SOURCE = "rightmove"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt_property(prop: PropertySummary | StoredProperty) -> str:
    if isinstance(prop, StoredProperty):
        price = f"£{prop.price_amount:,}" if prop.price_amount else "?"
        if prop.price_frequency:
            price += f" {prop.price_frequency}"
        beds = f"{prop.bedrooms}bd" if prop.bedrooms is not None else "?bd"
        addr = prop.display_address or "Unknown address"
        url = prop.url
        discovered = prop.discovered_at.strftime("%d %b %H:%M")
        return f"  [{beds}] {addr} — {price}  {url}  (found {discovered})"
    price = f"£{prop.price.amount:,}"
    if prop.price.frequency:
        price += f" {prop.price.frequency}"
    beds = f"{prop.bedrooms}bd" if prop.bedrooms is not None else "?bd"
    return f"  [{beds}] {prop.address.display_address} — {price}  {prop.rightmove_url}"


# ---------------------------------------------------------------------------
# Scrape + store
# ---------------------------------------------------------------------------

async def run_searches(config: AppConfig, store: RightmoveStore, user: str) -> dict[str, int]:
    """Run all searches, persist results. Returns {search_name: new_count}."""
    totals: dict[str, int] = {}

    async with RightmoveClient(request_delay=1.5) as client:
        for search in config.searches:
            print(f"\n{'=' * 60}")
            print(f"Search: {search.name}  ({search.region})")
            print(f"  Price:    £{search.min_price or 0:,} - £{search.max_price or 'any'}")
            print(f"  Bedrooms: {search.min_bedrooms or 'any'} - {search.max_bedrooms or 'any'}")
            print(f"{'=' * 60}")

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
                [(prop.rightmove_url, extract_from_rightmove(prop)) for prop in results.properties],
                source=_SOURCE,
                user=user,
            )

            print(
                f"  {results.total_results} total on Rightmove | "
                f"{len(results.properties)} fetched | "
                f"{new_count} new\n"
            )
            totals[search.name] = new_count

    return totals


# ---------------------------------------------------------------------------
# Report modes
# ---------------------------------------------------------------------------

async def report_new_since_last_run(store: RightmoveStore) -> None:
    properties = await store.get_new_since_last_run()
    last_run = await store.last_run_at()
    cutoff_str = last_run.strftime("%d %b %Y %H:%M") if last_run else "ever"

    print(f"\nNew properties since last run ({cutoff_str}):")
    if not properties:
        print("  None.")
        return
    for prop in properties:
        print(_fmt_property(prop))


async def report_since(store: RightmoveStore, since: datetime) -> None:
    properties = await store.get_properties_since(since)
    print(f"\nProperties discovered since {since.strftime('%d %b %Y')}:")
    if not properties:
        print("  None.")
        return
    for prop in properties:
        print(_fmt_property(prop))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run(config: AppConfig, db_path: Path, mode: str, since: datetime | None) -> None:
    user = db_path.stem.removeprefix("rightmove_")
    async with RightmoveStore(db_path) as store:
        if mode == "scrape":
            await run_searches(config, store, user)
        elif mode == "new":
            await report_new_since_last_run(store)
        elif mode == "since":
            assert since is not None
            await report_since(store, since)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rightmove property scrapper")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to YAML config file (DB is inferred from this path)",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--new",
        action="store_true",
        help="Report properties new since the last scrape (no new scrape)",
    )
    mode_group.add_argument(
        "--since",
        type=lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC),
        metavar="DATE",
        help="Report all properties discovered since DATE (ISO-8601, e.g. 2026-04-01)",
    )

    args = parser.parse_args()

    config_path = args.config.resolve()
    config = load_config(config_path)
    db_path = RightmoveStore.db_path_for_config(config_path)

    if args.new:
        mode = "new"
    elif args.since:
        mode = "since"
    else:
        mode = "scrape"

    asyncio.run(run(config, db_path, mode, args.since))


if __name__ == "__main__":
    main()
