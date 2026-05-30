from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SortType(IntEnum):
    LOWEST_PRICE = 1
    HIGHEST_PRICE = 2
    MOST_RECENT = 6


class PropertyType(StrEnum):
    FLAT = "flat"
    DETACHED = "detached"
    SEMI_DETACHED = "semi-detached"
    TERRACED = "terraced"
    BUNGALOW = "bungalow"
    MOBILE_HOME = "mobile-home"
    PARK_HOME = "park-home"
    SHARED_ACCOMMODATION = "shared-accommodation"


class FurnishType(StrEnum):
    """Lettings (RENT channel) only."""
    FURNISHED = "furnished"
    PART_FURNISHED = "partFurnished"
    UNFURNISHED = "unfurnished"


class LetType(StrEnum):
    """Lettings (RENT channel) only."""
    LONG_TERM = "longTerm"
    SHORT_TERM = "shortTerm"


class AddedToSite(StrEnum):
    LAST_24_HOURS = "last24Hours"
    LAST_3_DAYS = "last3Days"
    LAST_7_DAYS = "last7Days"
    LAST_14_DAYS = "last14Days"
    LAST_MONTH = "lastMonth"


class MustHave(StrEnum):
    GARDEN = "garden"
    PARKING = "parking"
    NEW_HOME = "newHome"
    RETIREMENT = "retirement"


class DontShow(StrEnum):
    NEW_HOME = "newHome"
    RETIREMENT = "retirement"
    SHARED_OWNERSHIP = "sharedOwnership"
    AUCTION = "auction"


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class Location(BaseModel):
    identifier: str  # e.g. "REGION^87498"
    display_name: str


class Address(BaseModel):
    display_address: str
    country_code: str | None = None


class Price(BaseModel):
    amount: int
    currency_code: str
    frequency: str | None = None  # e.g. "monthly", "weekly"


class PropertySummary(BaseModel):
    """Lightweight listing returned from search results."""

    id: int
    bedrooms: int | None = None
    bathrooms: int | None = None
    property_type: str | None = None
    address: Address
    price: Price
    url: str
    thumbnail_url: str | None = None
    summary: str | None = None
    let_available_date: str | None = None
    added_or_reduced: str | None = None

    @property
    def rightmove_url(self) -> str:
        return f"https://www.rightmove.co.uk/properties/{self.id}"


class PropertyDetail(PropertySummary):
    """Full property detail fetched from the property page."""

    description: str | None = None
    key_features: list[str] = []
    image_urls: list[str] = []
    floorplan_urls: list[str] = []
    latitude: float | None = None
    longitude: float | None = None
    size_sq_ft: float | None = None
    agent_name: str | None = None
    agent_phone: str | None = None
    raw: dict[str, Any] = {}


class SearchResults(BaseModel):
    location: str
    total_results: int
    page: int
    results_per_page: int
    properties: list[PropertySummary]
