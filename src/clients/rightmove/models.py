from typing import Any
from pydantic import BaseModel, HttpUrl


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
