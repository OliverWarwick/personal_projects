"""
Search Rightmove via the internal /api/_search JSON endpoint.
"""

import httpx

from .models import Address, Price, PropertySummary, SearchResults

SEARCH_URL = "https://www.rightmove.co.uk/api/_search"


def _parse_property(raw: dict) -> PropertySummary:
    price_info = raw.get("price", {})
    return PropertySummary(
        id=raw["id"],
        bedrooms=raw.get("bedrooms"),
        bathrooms=raw.get("bathrooms"),
        property_type=raw.get("propertySubType") or raw.get("propertyTypeFullDescription"),
        address=Address(
            display_address=raw.get("displayAddress", ""),
            country_code=raw.get("countryCode"),
        ),
        price=Price(
            amount=price_info.get("amount", 0),
            currency_code=price_info.get("currencyCode", "GBP"),
            frequency=price_info.get("frequency"),
        ),
        url=f"https://www.rightmove.co.uk/properties/{raw['id']}",
        thumbnail_url=(raw.get("propertyImages", {}) or {})
            .get("mainImageSrc"),
        summary=raw.get("summary"),
        let_available_date=raw.get("letAvailableDate"),
        added_or_reduced=raw.get("addedOrReduced"),
    )


async def search(
    client: httpx.AsyncClient,
    *,
    location_identifier: str,
    channel: str = "RENT",
    min_price: int | None = None,
    max_price: int | None = None,
    min_bedrooms: int | None = None,
    max_bedrooms: int | None = None,
    index: int = 0,
    results_per_page: int = 24,
    sort_type: int = 6,
    radius: float | None = None,
) -> SearchResults:
    """
    Perform a property search.

    Args:
        location_identifier: Rightmove location code, e.g. "REGION^87498".
        channel: "RENT" or "BUY".
        index: Pagination offset (multiples of results_per_page).
        sort_type: 6 = most recent, 2 = highest price, 1 = lowest price.
    """
    params: dict = {
        "locationIdentifier": location_identifier,
        "channel": channel,
        "index": index,
        "numberOfPropertiesPerPage": results_per_page,
        "sortType": sort_type,
        "viewType": "LIST",
        "areaSizeUnit": "sqft",
        "currencyCode": "GBP",
    }
    if min_price is not None:
        params["minPrice"] = min_price
    if max_price is not None:
        params["maxPrice"] = max_price
    if min_bedrooms is not None:
        params["minBedrooms"] = min_bedrooms
    if max_bedrooms is not None:
        params["maxBedrooms"] = max_bedrooms
    if radius is not None:
        params["radius"] = radius

    response = await client.get(SEARCH_URL, params=params)
    response.raise_for_status()
    data = response.json()

    properties = [_parse_property(p) for p in data.get("properties", [])]

    return SearchResults(
        location=location_identifier,
        total_results=data.get("resultCount", len(properties)),
        page=index // results_per_page,
        results_per_page=results_per_page,
        properties=properties,
    )
