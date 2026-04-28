from pydantic import BaseModel


class PropertySummary(BaseModel):
    """Lightweight listing returned from search results."""

    id: int
    title: str
    price_pcm: int  # monthly rent in GBP
    bedrooms: int | None = None
    url: str
    thumbnail_url: str | None = None


class PropertyDetail(PropertySummary):
    """Full property detail fetched from the property page."""

    description: str | None = None
    deposit: float | None = None
    available_date: str | None = None
    furnished: str | None = None  # e.g. "Furnished", "Unfurnished", "Part Furnished"
    bills_included: bool = False
    pets_allowed: bool = False
    latitude: float | None = None
    longitude: float | None = None
    image_urls: list[str] = []


class SearchResults(BaseModel):
    term: str
    total_results: int
    page: int
    properties: list[PropertySummary]
