"""
Tests for the Rightmove SQLite store.

All tests use an in-memory SQLite database (":memory:") so they are fast
and leave no files on disk.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from src.clients.rightmove.models import Address, Price, PropertySummary
from src.store.rightmove import (
    RightmoveStore,
    _parse_listing_date,
    _url_hash,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_property(
    property_id: int = 111111111,
    address: str = "Flat 1, Wandsworth High Street, SW18",
    price: int = 2200,
    bedrooms: int = 2,
    added_or_reduced: str | None = "Added today",
) -> PropertySummary:
    return PropertySummary(
        id=property_id,
        bedrooms=bedrooms,
        bathrooms=1,
        property_type="Flat",
        address=Address(display_address=address, country_code="GB"),
        price=Price(amount=price, currency_code="GBP", frequency="monthly"),
        url=f"https://www.rightmove.co.uk/properties/{property_id}",
        thumbnail_url=None,
        summary="A nice flat",
        let_available_date="Now",
        added_or_reduced=added_or_reduced,
    )


async def _open_memory_store() -> RightmoveStore:
    """Open a store backed by an in-memory SQLite database."""
    store = RightmoveStore(Path(":memory:"))
    # Bypass the mkdir call for in-memory DB
    import aiosqlite
    store._db = await aiosqlite.connect(":memory:")
    store._db.row_factory = aiosqlite.Row
    from src.store.rightmove import _SCHEMA
    await store._db.executescript(_SCHEMA)
    await store._db.commit()
    return store


# ---------------------------------------------------------------------------
# Unit tests — helpers
# ---------------------------------------------------------------------------

class TestParseListingDate:
    def test_added_today(self):
        ref = date(2026, 4, 7)
        assert _parse_listing_date("Added today", ref) == "2026-04-07"

    def test_reduced_today(self):
        ref = date(2026, 4, 7)
        assert _parse_listing_date("Reduced today", ref) == "2026-04-07"

    def test_added_yesterday(self):
        ref = date(2026, 4, 7)
        assert _parse_listing_date("Added yesterday", ref) == "2026-04-06"

    def test_added_on_explicit_date(self):
        assert _parse_listing_date("Added on 01/04/2026") == "2026-04-01"

    def test_reduced_on_explicit_date(self):
        assert _parse_listing_date("Reduced on 25/03/2026") == "2026-03-25"

    def test_none_input(self):
        assert _parse_listing_date(None) is None

    def test_unrecognised_returns_none(self):
        assert _parse_listing_date("Some unknown string") is None


class TestUrlHash:
    def test_same_url_same_hash(self):
        url = "https://www.rightmove.co.uk/properties/111111111"
        assert _url_hash(url) == _url_hash(url)

    def test_different_urls_different_hashes(self):
        assert _url_hash("https://www.rightmove.co.uk/properties/111") != \
               _url_hash("https://www.rightmove.co.uk/properties/222")

    def test_hash_is_64_chars(self):
        assert len(_url_hash("https://www.rightmove.co.uk/properties/1")) == 64


class TestDbPathForConfig:
    def test_derives_db_path_from_config(self, tmp_path):
        config_path = tmp_path / "src" / "config" / "rightmove_ow.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.touch()
        db_path = RightmoveStore.db_path_for_config(config_path)
        assert db_path == tmp_path / "data" / "rightmove_ow.db"


# ---------------------------------------------------------------------------
# Integration tests — store operations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestUpsertProperties:
    async def test_inserts_new_properties(self):
        store = await _open_memory_store()
        try:
            props = [_make_property(111), _make_property(222)]
            new_count = await store.upsert_properties(
                props, search_name="Wandsworth", location_identifier="REGION^96855"
            )
            assert new_count == 2
        finally:
            await store.close()

    async def test_deduplicates_on_second_insert(self):
        store = await _open_memory_store()
        try:
            props = [_make_property(111)]
            await store.upsert_properties(
                props, search_name="Wandsworth", location_identifier="REGION^96855"
            )
            # Insert the same property again
            new_count = await store.upsert_properties(
                props, search_name="Wandsworth", location_identifier="REGION^96855"
            )
            assert new_count == 0
        finally:
            await store.close()

    async def test_partial_deduplication(self):
        store = await _open_memory_store()
        try:
            await store.upsert_properties(
                [_make_property(111)],
                search_name="Wandsworth", location_identifier="REGION^96855"
            )
            new_count = await store.upsert_properties(
                [_make_property(111), _make_property(222)],  # 111 already exists
                search_name="Wandsworth", location_identifier="REGION^96855"
            )
            assert new_count == 1
        finally:
            await store.close()

    async def test_stores_listing_date_from_added_or_reduced(self):
        store = await _open_memory_store()
        try:
            today = datetime.now(UTC).date()
            await store.upsert_properties(
                [_make_property(111, added_or_reduced="Added today")],
                search_name="W", location_identifier="REGION^96855"
            )
            results = await store.get_all_properties()
            assert results[0].listing_date == today
        finally:
            await store.close()

    async def test_stores_all_property_fields(self):
        store = await _open_memory_store()
        try:
            await store.upsert_properties(
                [_make_property(111111111, address="High Street, SW18", price=2500, bedrooms=2)],
                search_name="Wandsworth", location_identifier="REGION^96855"
            )
            results = await store.get_all_properties()
            p = results[0]
            assert p.rightmove_id == 111111111
            assert p.display_address == "High Street, SW18"
            assert p.price_amount == 2500
            assert p.price_frequency == "monthly"
            assert p.bedrooms == 2
            assert p.search_name == "Wandsworth"
            assert p.location_identifier == "REGION^96855"
            assert p.url == "https://www.rightmove.co.uk/properties/111111111"
        finally:
            await store.close()


@pytest.mark.asyncio
class TestQueryMethods:
    async def test_get_properties_since(self):
        store = await _open_memory_store()
        try:
            # Insert a property and record its discovered_at
            await store.upsert_properties(
                [_make_property(111)],
                search_name="W", location_identifier="REGION^96855"
            )
            # Query since 1 hour ago — should find it
            since = datetime.now(UTC) - timedelta(hours=1)
            results = await store.get_properties_since(since)
            assert len(results) == 1
            assert results[0].rightmove_id == 111
        finally:
            await store.close()

    async def test_get_properties_since_future_returns_empty(self):
        store = await _open_memory_store()
        try:
            await store.upsert_properties(
                [_make_property(111)],
                search_name="W", location_identifier="REGION^96855"
            )
            since = datetime.now(UTC) + timedelta(hours=1)
            results = await store.get_properties_since(since)
            assert results == []
        finally:
            await store.close()

    async def test_get_properties_since_filters_by_search_name(self):
        store = await _open_memory_store()
        try:
            await store.upsert_properties(
                [_make_property(111)], search_name="Wandsworth", location_identifier="REGION^96855"
            )
            await store.upsert_properties(
                [_make_property(222)], search_name="Islington", location_identifier="REGION^87498"
            )
            since = datetime.now(UTC) - timedelta(hours=1)
            results = await store.get_properties_since(since, search_name="Wandsworth")
            assert len(results) == 1
            assert results[0].rightmove_id == 111
        finally:
            await store.close()

    async def test_get_new_since_last_run_single_run_returns_all(self):
        """With only one run on record, everything is 'new'."""
        store = await _open_memory_store()
        try:
            await store.upsert_properties(
                [_make_property(111), _make_property(222)],
                search_name="W", location_identifier="REGION^96855"
            )
            await store.record_run(search_name="W", total_found=2, new_count=2)
            results = await store.get_new_since_last_run(search_name="W")
            assert len(results) == 2
        finally:
            await store.close()

    async def test_get_new_since_last_run_two_runs(self):
        """
        Simulate two scraper runs:
          Run 1: 2 properties inserted
          Run 2: 1 additional property inserted
        get_new_since_last_run should return only the 1 from run 2.
        """
        store = await _open_memory_store()
        try:
            # Simulate run 1
            t_run1 = datetime(2026, 4, 7, 9, 0, 0, tzinfo=UTC)
            with patch("src.store.rightmove._now_utc", return_value=t_run1.isoformat()):
                await store.upsert_properties(
                    [_make_property(111), _make_property(222)],
                    search_name="W", location_identifier="REGION^96855"
                )
                await store.record_run(search_name="W", total_found=2, new_count=2)

            # Simulate run 2 (later)
            t_run2 = datetime(2026, 4, 7, 18, 0, 0, tzinfo=UTC)
            with patch("src.store.rightmove._now_utc", return_value=t_run2.isoformat()):
                await store.upsert_properties(
                    [_make_property(111), _make_property(222), _make_property(333)],
                    search_name="W", location_identifier="REGION^96855"
                )
                await store.record_run(search_name="W", total_found=3, new_count=1)

            new = await store.get_new_since_last_run(search_name="W")
            assert len(new) == 1
            assert new[0].rightmove_id == 333
        finally:
            await store.close()

    async def test_last_run_at_none_when_no_runs(self):
        store = await _open_memory_store()
        try:
            assert await store.last_run_at() is None
        finally:
            await store.close()

    async def test_last_run_at_returns_most_recent(self):
        store = await _open_memory_store()
        try:
            await store.record_run(search_name="W", total_found=10, new_count=3)
            await store.record_run(search_name="W", total_found=12, new_count=1)
            last = await store.last_run_at()
            assert last is not None
            # Should be within the last few seconds
            assert (datetime.now(UTC) - last).total_seconds() < 5
        finally:
            await store.close()

    async def test_get_all_properties(self):
        store = await _open_memory_store()
        try:
            await store.upsert_properties(
                [_make_property(111), _make_property(222), _make_property(333)],
                search_name="W", location_identifier="REGION^96855"
            )
            results = await store.get_all_properties()
            assert len(results) == 3
        finally:
            await store.close()
