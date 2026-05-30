"""
SQLite store for Urban Spaces property listings.

Schema
------
urbanspaces_properties  — one row per unique listing (keyed by url_hash of the property URL)
property_features       — JSON blob of extracted features, keyed by url_hash
runs                    — one row per scraper execution
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from src.clients.urbanspaces.models import PropertySummary
from src.store.features import FEATURES_SCHEMA, FeaturesStoreMixin

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS urbanspaces_properties (
    url_hash        TEXT PRIMARY KEY,
    pcode           TEXT NOT NULL,
    record_id       TEXT NOT NULL,
    url             TEXT NOT NULL,
    discovered_at   TEXT NOT NULL,      -- ISO-8601 UTC
    display_address TEXT,
    postcode        TEXT,
    price_monthly   REAL,
    price_weekly    REAL,
    bedrooms        INTEGER,
    property_type   TEXT,
    status          TEXT,
    thumbnail_url   TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at       TEXT NOT NULL,         -- ISO-8601 UTC
    total_found  INTEGER NOT NULL DEFAULT 0,
    new_count    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_us_properties_discovered
    ON urbanspaces_properties (discovered_at);
CREATE INDEX IF NOT EXISTS idx_us_runs
    ON runs (ran_at);
"""
    + FEATURES_SCHEMA
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Stored record
# ---------------------------------------------------------------------------


@dataclass
class StoredProperty:
    url_hash: str
    pcode: str
    record_id: str
    url: str
    discovered_at: datetime
    display_address: str | None
    postcode: str | None
    price_monthly: float | None
    price_weekly: float | None
    bedrooms: int | None
    property_type: str | None
    status: str | None
    thumbnail_url: str | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "StoredProperty":
        return cls(
            url_hash=row["url_hash"],
            pcode=row["pcode"],
            record_id=row["record_id"],
            url=row["url"],
            discovered_at=datetime.fromisoformat(row["discovered_at"]),
            display_address=row["display_address"],
            postcode=row["postcode"],
            price_monthly=row["price_monthly"],
            price_weekly=row["price_weekly"],
            bedrooms=row["bedrooms"],
            property_type=row["property_type"],
            status=row["status"],
            thumbnail_url=row["thumbnail_url"],
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class UrbanSpacesStore(FeaturesStoreMixin):
    """
    Async context manager wrapping a user's SQLite database.

    Usage:
        async with UrbanSpacesStore.for_user("ow") as store:
            new = await store.upsert_properties(props)
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def db_path_for_user(cls, user: str, root: Path | None = None) -> Path:
        """
        Derive the DB path from a user identifier.
        e.g. user="ow" -> data/urbanspaces_ow.db
        """
        base = root or Path(__file__).parents[2]
        return base / "data" / f"urbanspaces_{user}.db"

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

    async def __aenter__(self) -> "UrbanSpacesStore":
        await self.open()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Store is not open. Use 'async with UrbanSpacesStore(...)'")
        return self._db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def upsert_properties(self, properties: list[PropertySummary]) -> int:
        """
        Insert new properties, ignoring duplicates (same URL = same listing).
        Returns the count of genuinely new rows inserted.
        """
        now = datetime.now(UTC).isoformat()
        rows = [
            (
                _url_hash(p.url),
                p.pcode,
                p.record_id,
                p.url,
                now,
                p.address.display_address,
                p.address.postcode,
                p.price.monthly,
                p.price.weekly,
                p.bedrooms,
                p.property_type,
                p.status,
                p.thumbnail_url,
            )
            for p in properties
        ]

        before = await self._row_count()
        await self._conn.executemany(
            """
            INSERT OR IGNORE INTO urbanspaces_properties (
                url_hash, pcode, record_id, url, discovered_at,
                display_address, postcode,
                price_monthly, price_weekly,
                bedrooms, property_type, status, thumbnail_url
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        await self._conn.commit()
        after = await self._row_count()
        return after - before

    async def record_run(self, *, total_found: int, new_count: int) -> None:
        await self._conn.execute(
            "INSERT INTO runs (ran_at, total_found, new_count) VALUES (?,?,?)",
            (datetime.now(UTC).isoformat(), total_found, new_count),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_all_properties(self) -> list[StoredProperty]:
        cursor = await self._conn.execute(
            "SELECT * FROM urbanspaces_properties ORDER BY discovered_at DESC",
        )
        return [StoredProperty.from_row(row) for row in await cursor.fetchall()]

    async def get_properties_since(self, since: datetime) -> list[StoredProperty]:
        cursor = await self._conn.execute(
            "SELECT * FROM urbanspaces_properties WHERE discovered_at >= ? ORDER BY discovered_at DESC",
            (since.isoformat(),),
        )
        return [StoredProperty.from_row(row) for row in await cursor.fetchall()]

    async def get_new_since_last_run(self) -> list[StoredProperty]:
        cursor = await self._conn.execute(
            "SELECT ran_at FROM runs ORDER BY ran_at DESC LIMIT 2",
        )
        rows = await cursor.fetchall()
        cutoff = rows[1]["ran_at"] if len(rows) >= 2 else datetime.min.replace(tzinfo=UTC).isoformat()
        cursor = await self._conn.execute(
            "SELECT * FROM urbanspaces_properties WHERE discovered_at > ? ORDER BY discovered_at DESC",
            (cutoff,),
        )
        return [StoredProperty.from_row(row) for row in await cursor.fetchall()]

    async def last_run_at(self) -> datetime | None:
        cursor = await self._conn.execute(
            "SELECT ran_at FROM runs ORDER BY ran_at DESC LIMIT 1",
        )
        row = await cursor.fetchone()
        return datetime.fromisoformat(row["ran_at"]) if row else None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _row_count(self) -> int:
        cursor = await self._conn.execute("SELECT COUNT(*) FROM urbanspaces_properties")
        row = await cursor.fetchone()
        return row[0] if row else 0
