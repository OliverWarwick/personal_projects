"""
Shared PropertyFeatures model and extraction logic for all property sources.

PropertyFeatures is stored as a JSON blob in the property_features table,
which lives in each source's SQLite database keyed by url_hash.

Joining across sources
----------------------
Each source keeps its own DB (data/rightmove_ow.db, data/urbanspaces_ow.db etc).
The property_features table in each uses the same schema, so you can either:

  # Python merge
  rm_feats = await rm_store.get_all_features()
  us_feats = await us_store.get_all_features()
  all_feats = rm_feats + us_feats

  # Or ATTACH both DBs in SQLite
  ATTACH 'data/urbanspaces_ow.db' AS us;
  SELECT * FROM main.property_features
  UNION ALL
  SELECT * FROM us.property_features;
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite
from pydantic import BaseModel

from src.clients.rightmove.models import PropertyDetail as RightmoveDetail
from src.clients.rightmove.models import PropertySummary as RightmoveSummary
from src.clients.urbanspaces.models import PropertyDetail as UrbanSpacesDetail
from src.clients.urbanspaces.models import PropertySummary as UrbanSpacesSummary

# ---------------------------------------------------------------------------
# Schema (included in each source's DB)
# ---------------------------------------------------------------------------

FEATURES_SCHEMA = """
CREATE TABLE IF NOT EXISTS property_features (
    url_hash     TEXT NOT NULL,
    source       TEXT NOT NULL,  -- 'rightmove' | 'urbanspaces'
    user         TEXT NOT NULL,  -- config stem, e.g. 'ow'
    extracted_at TEXT NOT NULL,  -- ISO-8601 UTC
    features     TEXT NOT NULL,  -- JSON blob matching PropertyFeatures
    PRIMARY KEY (url_hash, source)
);

CREATE INDEX IF NOT EXISTS idx_features_user
    ON property_features (user, source);
"""

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class PropertyFeatures(BaseModel):
    """
    Source-agnostic features extracted from a property listing.
    None means the data was not available from the fetched page level.
    New fields can be added here without any schema migration.
    """

    # Size & layout
    bedrooms: int | None = None
    bathrooms: int | None = None
    living_rooms: int | None = None
    sq_ft: float | None = None

    # Price
    price_per_month: float | None = None
    price_per_week: float | None = None

    # Outdoor
    garden: bool | None = None
    outdoor_space: bool | None = None  # garden, terrace, balcony, courtyard, patio

    # Other features
    parking: bool | None = None
    deposit: float | None = None
    epc_rating: str | None = None  # A-G

    # Location
    postcode: str | None = None
    street_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    # Classification
    property_type: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_POSTCODE_RE = re.compile(
    r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})\b",
    re.IGNORECASE,
)
_PARTIAL_POSTCODE_RE = re.compile(
    r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\b",
    re.IGNORECASE,
)

_OUTDOOR_KEYWORDS = {"garden", "terrace", "balcony", "courtyard", "patio", "roof terrace", "outdoor"}
_PARKING_KEYWORDS = {"parking", "garage", "car park", "car space"}


def _has_keyword(features: list[str], keywords: set[str]) -> bool:
    joined = " ".join(features).lower()
    return any(kw in joined for kw in keywords)


def _extract_postcode(text: str) -> str | None:
    m = _POSTCODE_RE.search(text)
    if m:
        return m.group(1).upper().replace(" ", " ")
    # Fall back to partial (e.g. "SE1")
    m = _PARTIAL_POSTCODE_RE.search(text)
    return m.group(1).upper() if m else None


def _street_name(display_address: str) -> str | None:
    """Best-effort: return the first comma-separated segment."""
    first = display_address.split(",")[0].strip()
    return first if first else None


def _count_rooms(features: list[str], keywords: list[str]) -> int | None:
    """Count features that mention a room type (e.g. 'reception', 'living room')."""
    joined = " ".join(f.lower() for f in features)
    for kw in keywords:
        if kw in joined:
            # Try to parse a number before the keyword, e.g. "Two reception rooms"
            m = re.search(rf"(\d+|one|two|three|four|five)\s+{kw}", joined)
            if m:
                word_map = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
                val = m.group(1)
                return word_map.get(val, int(val) if val.isdigit() else 1)
            return 1
    return None


def _rightmove_price_per_month(price_amount: int, frequency: str | None) -> float:
    if frequency in ("monthly", None):
        return float(price_amount)
    if frequency == "weekly":
        return round(price_amount * 52 / 12, 2)
    return float(price_amount)


def _rightmove_price_per_week(price_amount: int, frequency: str | None) -> float:
    if frequency == "weekly":
        return float(price_amount)
    if frequency in ("monthly", None):
        return round(price_amount * 12 / 52, 2)
    return float(price_amount)


# ---------------------------------------------------------------------------
# Extraction — Rightmove
# ---------------------------------------------------------------------------

def extract_from_rightmove(prop: RightmoveSummary) -> PropertyFeatures:
    """
    Extract features from a Rightmove PropertySummary or PropertyDetail.
    Detail-only fields (sq_ft, garden, etc.) are populated when a PropertyDetail
    is passed; they remain None for a summary-only object.
    """
    addr = prop.address.display_address
    key_features: list[str] = []
    if isinstance(prop, RightmoveDetail):
        key_features = prop.key_features

    garden = (True if _has_keyword(key_features, {"garden"}) else None) if key_features else None
    outdoor = (True if _has_keyword(key_features, _OUTDOOR_KEYWORDS) else None) if key_features else None

    return PropertyFeatures(
        bedrooms=prop.bedrooms,
        bathrooms=prop.bathrooms,
        living_rooms=_count_rooms(key_features, ["reception", "living room", "reception room"]),
        sq_ft=prop.size_sq_ft if isinstance(prop, RightmoveDetail) else None,
        price_per_month=_rightmove_price_per_month(prop.price.amount, prop.price.frequency),
        price_per_week=_rightmove_price_per_week(prop.price.amount, prop.price.frequency),
        garden=garden,
        outdoor_space=outdoor,
        parking=(True if _has_keyword(key_features, _PARKING_KEYWORDS) else None) if key_features else None,
        deposit=None,
        epc_rating=None,
        postcode=_extract_postcode(addr),
        street_name=_street_name(addr),
        latitude=prop.latitude if isinstance(prop, RightmoveDetail) else None,
        longitude=prop.longitude if isinstance(prop, RightmoveDetail) else None,
        property_type=prop.property_type,
    )


# ---------------------------------------------------------------------------
# Extraction — Urban Spaces
# ---------------------------------------------------------------------------

def extract_from_urbanspaces(prop: UrbanSpacesSummary) -> PropertyFeatures:
    """
    Extract features from an Urban Spaces PropertySummary or PropertyDetail.
    accommodation_summary is a rich feature list available even from the listings page.
    """
    summary = prop.accommodation_summary  # list[str]
    addr = prop.address.display_address

    garden = True if _has_keyword(summary, {"garden"}) else None
    outdoor = True if _has_keyword(summary, _OUTDOOR_KEYWORDS) else None

    bathrooms: int | None = None
    m = re.search(r"(\d+)\s+bathroom", " ".join(summary).lower())
    if m:
        bathrooms = int(m.group(1))

    return PropertyFeatures(
        bedrooms=prop.bedrooms,
        bathrooms=bathrooms,
        living_rooms=_count_rooms(summary, ["reception", "living room", "reception room"]),
        sq_ft=prop.sq_ft if isinstance(prop, UrbanSpacesDetail) else None,
        price_per_month=prop.price.monthly,
        price_per_week=prop.price.weekly,
        garden=garden,
        outdoor_space=outdoor,
        parking=True if _has_keyword(summary, _PARKING_KEYWORDS) else None,
        deposit=prop.deposit if isinstance(prop, UrbanSpacesDetail) else None,
        epc_rating=None,
        postcode=prop.address.postcode or _extract_postcode(addr),
        street_name=_street_name(addr),
        latitude=prop.latitude if isinstance(prop, UrbanSpacesDetail) else None,
        longitude=prop.longitude if isinstance(prop, UrbanSpacesDetail) else None,
        property_type=prop.property_type,
    )


# ---------------------------------------------------------------------------
# Stored record
# ---------------------------------------------------------------------------

@dataclass
class StoredFeatures:
    url_hash: str
    source: str
    user: str
    extracted_at: datetime
    features: PropertyFeatures

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "StoredFeatures":
        return cls(
            url_hash=row["url_hash"],
            source=row["source"],
            user=row["user"],
            extracted_at=datetime.fromisoformat(row["extracted_at"]),
            features=PropertyFeatures.model_validate_json(row["features"]),
        )


# ---------------------------------------------------------------------------
# Store mixin — add to any aiosqlite-backed store
# ---------------------------------------------------------------------------

class FeaturesStoreMixin:
    """
    Mixin for stores that hold an aiosqlite connection as self._conn.
    Provides feature upsert and query methods.
    """

    @property
    def _conn(self) -> aiosqlite.Connection:  # overridden by host class
        raise NotImplementedError

    async def upsert_features(
        self,
        url_hash: str,
        features: PropertyFeatures,
        *,
        source: str,
        user: str,
    ) -> None:
        """Insert or replace features for a property."""
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO property_features
                (url_hash, source, user, extracted_at, features)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                url_hash,
                source,
                user,
                datetime.now(UTC).isoformat(),
                features.model_dump_json(),
            ),
        )
        await self._conn.commit()

    async def upsert_features_bulk(
        self,
        rows: list[tuple[str, PropertyFeatures]],
        *,
        source: str,
        user: str,
    ) -> None:
        """Bulk upsert (url_hash, features) pairs."""
        now = datetime.now(UTC).isoformat()
        await self._conn.executemany(
            """
            INSERT OR REPLACE INTO property_features
                (url_hash, source, user, extracted_at, features)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (url_hash, source, user, now, feats.model_dump_json())
                for url_hash, feats in rows
            ],
        )
        await self._conn.commit()

    async def get_all_features(
        self,
        *,
        user: str | None = None,
    ) -> list[StoredFeatures]:
        if user:
            cursor = await self._conn.execute(
                "SELECT * FROM property_features WHERE user = ? ORDER BY extracted_at DESC",
                (user,),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM property_features ORDER BY extracted_at DESC",
            )
        return [StoredFeatures.from_row(row) for row in await cursor.fetchall()]
