"""
SQLite store for Rightmove property listings.

Each user gets their own database file derived from their config name,
e.g. config/rightmove_ow.yaml -> data/rightmove_ow.db.

Schema
------
properties  — one row per unique listing (keyed by url_hash)
runs        — one row per scraper execution, per search

Key design choices:
- url_hash (SHA-256 of the canonical URL) is the deduplication key.
- discovered_at is set only on first insertion; subsequent scrapes of the
  same URL are ignored (INSERT OR IGNORE).
- listing_date is parsed best-effort from Rightmove's addedOrReduced string.
- "New since last run" = properties whose discovered_at >= the previous run's
  ran_at timestamp for the same search.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import aiosqlite

from src.clients.rightmove.models import PropertySummary
from src.store.features import FEATURES_SCHEMA, FeaturesStoreMixin

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS properties (
    url_hash            TEXT PRIMARY KEY,
    rightmove_id        INTEGER NOT NULL,
    url                 TEXT NOT NULL,
    discovered_at       TEXT NOT NULL,      -- ISO-8601 UTC
    listing_date        TEXT,               -- ISO-8601 date parsed from added_or_reduced
    search_name         TEXT NOT NULL,
    location_identifier TEXT NOT NULL,
    display_address     TEXT,
    price_amount        INTEGER,
    price_currency      TEXT DEFAULT 'GBP',
    price_frequency     TEXT,
    bedrooms            INTEGER,
    bathrooms           INTEGER,
    property_type       TEXT,
    summary             TEXT,
    let_available_date  TEXT,
    added_or_reduced    TEXT,
    thumbnail_url       TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at       TEXT NOT NULL,             -- ISO-8601 UTC
    search_name  TEXT NOT NULL,
    total_found  INTEGER NOT NULL DEFAULT 0,
    new_count    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_properties_discovered
    ON properties (discovered_at);
CREATE INDEX IF NOT EXISTS idx_properties_search
    ON properties (search_name, discovered_at);
CREATE INDEX IF NOT EXISTS idx_runs_search
    ON runs (search_name, ran_at);
""" + FEATURES_SCHEMA


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


# Patterns Rightmove uses in addedOrReduced, e.g.:
#   "Added today", "Added yesterday", "Added on 01/04/2026",
#   "Reduced today", "Reduced on 25/03/2026"
_DATE_PATTERN = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")


def _parse_listing_date(added_or_reduced: str | None, reference: date | None = None) -> str | None:
    """
    Return an ISO-8601 date string parsed from Rightmove's addedOrReduced field.
    Falls back to None if the date cannot be determined.
    """
    if not added_or_reduced:
        return None

    ref = reference or datetime.now(UTC).date()
    text = added_or_reduced.lower()

    if "today" in text:
        return ref.isoformat()
    if "yesterday" in text:
        return (ref - timedelta(days=1)).isoformat()

    m = _DATE_PATTERN.search(added_or_reduced)
    if m:
        try:
            return datetime.strptime(m.group(1), "%d/%m/%Y").replace(tzinfo=UTC).date().isoformat()
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# Data class returned by queries
# ---------------------------------------------------------------------------

@dataclass
class StoredProperty:
    url_hash: str
    rightmove_id: int
    url: str
    discovered_at: datetime
    listing_date: date | None
    search_name: str
    location_identifier: str
    display_address: str | None
    price_amount: int | None
    price_currency: str
    price_frequency: str | None
    bedrooms: int | None
    bathrooms: int | None
    property_type: str | None
    summary: str | None
    let_available_date: str | None
    added_or_reduced: str | None
    thumbnail_url: str | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "StoredProperty":
        return cls(
            url_hash=row["url_hash"],
            rightmove_id=row["rightmove_id"],
            url=row["url"],
            discovered_at=datetime.fromisoformat(row["discovered_at"]),
            listing_date=date.fromisoformat(row["listing_date"]) if row["listing_date"] else None,
            search_name=row["search_name"],
            location_identifier=row["location_identifier"],
            display_address=row["display_address"],
            price_amount=row["price_amount"],
            price_currency=row["price_currency"] or "GBP",
            price_frequency=row["price_frequency"],
            bedrooms=row["bedrooms"],
            bathrooms=row["bathrooms"],
            property_type=row["property_type"],
            summary=row["summary"],
            let_available_date=row["let_available_date"],
            added_or_reduced=row["added_or_reduced"],
            thumbnail_url=row["thumbnail_url"],
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class RightmoveStore(FeaturesStoreMixin):
    """
    Async context manager wrapping a user's SQLite database.

    Usage:
        async with RightmoveStore.for_user("ow") as store:
            new = await store.upsert_properties(props, search_name="Wandsworth")
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def db_path_for_config(cls, config_path: Path) -> Path:
        """
        Derive the DB path from a config filename.
        e.g. src/config/rightmove_ow.yaml -> data/rightmove_ow.db
        """
        stem = config_path.stem  # "rightmove_ow"
        root = config_path.parents[2]  # project root
        return root / "data" / f"{stem}.db"

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> "RightmoveStore":
        await self.open()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Store is not open. Use 'async with RightmoveStore(...)'")
        return self._db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def upsert_properties(
        self,
        properties: list[PropertySummary],
        *,
        search_name: str,
        location_identifier: str,
    ) -> int:
        """
        Insert new properties, ignoring duplicates.
        Returns the count of genuinely new rows inserted.
        """
        now = _now_utc()
        rows = []
        for prop in properties:
            url = prop.rightmove_url
            rows.append((
                _url_hash(url),
                prop.id,
                url,
                now,
                _parse_listing_date(prop.added_or_reduced),
                search_name,
                location_identifier,
                prop.address.display_address,
                prop.price.amount,
                prop.price.currency_code,
                prop.price.frequency,
                prop.bedrooms,
                prop.bathrooms,
                prop.property_type,
                prop.summary,
                prop.let_available_date,
                prop.added_or_reduced,
                prop.thumbnail_url,
            ))

        before = await self._row_count()
        await self._conn.executemany(
            """
            INSERT OR IGNORE INTO properties (
                url_hash, rightmove_id, url, discovered_at, listing_date,
                search_name, location_identifier, display_address,
                price_amount, price_currency, price_frequency,
                bedrooms, bathrooms, property_type, summary,
                let_available_date, added_or_reduced, thumbnail_url
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        await self._conn.commit()
        after = await self._row_count()
        return after - before

    async def record_run(
        self,
        *,
        search_name: str,
        total_found: int,
        new_count: int,
    ) -> None:
        await self._conn.execute(
            "INSERT INTO runs (ran_at, search_name, total_found, new_count) VALUES (?,?,?,?)",
            (_now_utc(), search_name, total_found, new_count),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_properties_since(
        self,
        since: datetime,
        *,
        search_name: str | None = None,
    ) -> list[StoredProperty]:
        """All properties discovered at or after `since`."""
        since_str = since.isoformat()
        if search_name:
            cursor = await self._conn.execute(
                "SELECT * FROM properties WHERE discovered_at >= ? AND search_name = ? ORDER BY discovered_at DESC",
                (since_str, search_name),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM properties WHERE discovered_at >= ? ORDER BY discovered_at DESC",
                (since_str,),
            )
        return [StoredProperty.from_row(row) for row in await cursor.fetchall()]

    async def get_new_since_last_run(
        self,
        *,
        search_name: str | None = None,
    ) -> list[StoredProperty]:
        """
        Properties discovered since the previous scraper run.

        Finds the second-most-recent run timestamp (per search if given) and
        returns all properties discovered after it — i.e. everything that was
        new in the most recent run.
        """
        if search_name:
            cursor = await self._conn.execute(
                "SELECT ran_at FROM runs WHERE search_name = ? ORDER BY ran_at DESC LIMIT 2",
                (search_name,),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT ran_at FROM runs ORDER BY ran_at DESC LIMIT 2",
            )
        rows = list(await cursor.fetchall())

        if len(rows) < 2:
            # Only one run on record — return everything discovered so far
            cutoff_str = datetime.min.replace(tzinfo=UTC).isoformat()
        else:
            cutoff_str = rows[1]["ran_at"]

        # Use strictly-greater-than so properties from the previous run are excluded
        if search_name:
            cursor = await self._conn.execute(
                "SELECT * FROM properties WHERE discovered_at > ? AND search_name = ? ORDER BY discovered_at DESC",
                (cutoff_str, search_name),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM properties WHERE discovered_at > ? ORDER BY discovered_at DESC",
                (cutoff_str,),
            )
        return [StoredProperty.from_row(row) for row in await cursor.fetchall()]

    async def get_all_properties(
        self,
        *,
        search_name: str | None = None,
    ) -> list[StoredProperty]:
        if search_name:
            cursor = await self._conn.execute(
                "SELECT * FROM properties WHERE search_name = ? ORDER BY discovered_at DESC",
                (search_name,),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM properties ORDER BY discovered_at DESC",
            )
        return [StoredProperty.from_row(row) for row in await cursor.fetchall()]

    async def last_run_at(self, *, search_name: str | None = None) -> datetime | None:
        """Timestamp of the most recent run, or None if no runs recorded."""
        if search_name:
            cursor = await self._conn.execute(
                "SELECT ran_at FROM runs WHERE search_name = ? ORDER BY ran_at DESC LIMIT 1",
                (search_name,),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT ran_at FROM runs ORDER BY ran_at DESC LIMIT 1",
            )
        row = await cursor.fetchone()
        return datetime.fromisoformat(row["ran_at"]) if row else None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _row_count(self) -> int:
        cursor = await self._conn.execute("SELECT COUNT(*) FROM properties")
        row = await cursor.fetchone()
        return row[0] if row else 0
