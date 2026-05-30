"""
Urban Spaces scrapper — fetches all listings, stores results in SQLite,
extracts structured features, and prints results.

Usage:
    uv run python -m src.apps.urbanspaces_scrapper.main --user ow
    uv run python -m src.apps.urbanspaces_scrapper.main --user ow --max-rent 4000 --min-beds 2 --available-only
    uv run python -m src.apps.urbanspaces_scrapper.main --user ow --new
"""

import argparse
import asyncio
import hashlib

from src.clients.urbanspaces import UrbanSpacesClient
from src.clients.urbanspaces.models import PropertySummary
from src.store.features import extract_from_urbanspaces
from src.store.urbanspaces import UrbanSpacesStore

_SOURCE = "urbanspaces"


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _format_property(prop: PropertySummary) -> str:
    beds = f"{prop.bedrooms}bd" if prop.bedrooms is not None else "?bd"
    monthly = f"£{prop.price.monthly:,.0f}/mo"
    weekly = f"£{prop.price.weekly:,.0f}/pw"
    status = f"[{prop.status}]"
    ptype = f"({prop.property_type})" if prop.property_type else ""
    return f"  {status} [{beds}] {ptype} {prop.address.display_address} — {monthly} / {weekly}  {prop.url}"


async def run(args: argparse.Namespace) -> None:
    db_path = UrbanSpacesStore.db_path_for_user(args.user)

    async with UrbanSpacesClient(request_delay=1.0) as client:
        results = await client.search(
            min_bedrooms=args.min_beds,
            max_bedrooms=args.max_beds,
            max_monthly_rent=args.max_rent,
            min_monthly_rent=args.min_rent,
            available_only=args.available_only,
            property_type=args.property_type,
        )

    async with UrbanSpacesStore(db_path) as store:
        if args.new:
            new_props = await store.get_new_since_last_run()
            last_run = await store.last_run_at()
            cutoff_str = last_run.strftime("%d %b %Y %H:%M") if last_run else "ever"
            print(f"\nNew Urban Spaces properties since last run ({cutoff_str}):")
            if not new_props:
                print("  None.")
            for p in new_props:
                print(f"  [{p.status}] [{p.bedrooms}bd] {p.display_address} — £{p.price_monthly:,.0f}/mo  {p.url}")
            return

        new_count = await store.upsert_properties(results.properties)
        await store.record_run(
            total_found=results.total_results,
            new_count=new_count,
        )
        await store.upsert_features_bulk(
            [(_url_hash(p.url), extract_from_urbanspaces(p)) for p in results.properties],
            source=_SOURCE,
            user=args.user,
        )

    print(f"\nUrban Spaces — {results.total_results} properties found  ({new_count} new)\n{'=' * 60}")
    for prop in results.properties:
        print(_format_property(prop))


def main() -> None:
    parser = argparse.ArgumentParser(description="Urban Spaces property scrapper")
    parser.add_argument("--user", type=str, required=True, help="User identifier (e.g. ow)")
    parser.add_argument("--min-beds", type=int, default=None, help="Minimum bedrooms")
    parser.add_argument("--max-beds", type=int, default=None, help="Maximum bedrooms")
    parser.add_argument("--min-rent", type=int, default=None, help="Minimum monthly rent (£)")
    parser.add_argument("--max-rent", type=int, default=None, help="Maximum monthly rent (£)")
    parser.add_argument("--available-only", action="store_true", help="Exclude Under Offer")
    parser.add_argument("--property-type", type=str, default=None, help="Filter by type, e.g. Flat")
    parser.add_argument("--new", action="store_true", help="Report new since last run (no new scrape)")
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
