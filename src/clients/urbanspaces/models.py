from pydantic import BaseModel


class Address(BaseModel):
    display_address: str
    postcode: str | None = None


class Price(BaseModel):
    weekly: float
    monthly: float
    currency_code: str = "GBP"


class PropertySummary(BaseModel):
    """Property data parsed from the listings page."""

    record_id: str
    pcode: str
    bedrooms: int | None = None
    property_type: str | None = None
    address: Address
    price: Price
    status: str  # "Available" or "Under Offer"
    thumbnail_url: str | None = None
    accommodation_summary: list[str] = []
    url: str


class PropertyDetail(PropertySummary):
    """Extended data fetched from the individual property page."""

    description: str | None = None
    weekly_rent: float | None = None
    deposit: float | None = None
    council_tax_band: str | None = None
    sq_ft: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    image_urls: list[str] = []
    floorplan_url: str | None = None
    available_date: str | None = None


class SearchResults(BaseModel):
    total_results: int
    properties: list[PropertySummary]
