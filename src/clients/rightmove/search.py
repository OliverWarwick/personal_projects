"""
Search Rightmove by scraping the HTML search page.

Rightmove embeds all search results inside a <script id="__NEXT_DATA__"> tag
as a JSON blob. We extract that rather than using the now-blocked /api/_search
JSON endpoint.

A session warm-up request to the homepage is required to obtain cookies that
Rightmove validates on search pages; the client handles this once per session.
"""

import json
import re

import httpx

from src.clients.rightmove.models import (
    AddedToSite,
    Address,
    DontShow,
    FurnishType,
    LetType,
    MustHave,
    Price,
    PropertySummary,
    PropertyType,
    SearchResults,
    SortType,
)

# Channel → search page URL
SEARCH_URLS: dict[str, str] = {
    "RENT": "https://www.rightmove.co.uk/property-to-rent/find.html",
    "BUY": "https://www.rightmove.co.uk/property-for-sale/find.html",
}
WARMUP_URL = "https://www.rightmove.co.uk/"

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
    re.DOTALL,
)


def _extract_next_data(html: str) -> dict:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("Could not find __NEXT_DATA__ in page source")
    return json.loads(m.group(1))


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
        thumbnail_url=(raw.get("propertyImages", {}) or {}).get("mainImageSrc"),
        summary=raw.get("summary"),
        let_available_date=raw.get("letAvailableDate"),
        added_or_reduced=raw.get("addedOrReduced"),
    )


async def search(
    client: httpx.AsyncClient,
    *,
    location_identifier: str,
    channel: str = "RENT",
    # Price
    min_price: int | None = None,
    max_price: int | None = None,
    # Bedrooms
    min_bedrooms: int | None = None,
    max_bedrooms: int | None = None,
    # Property type
    property_types: list[PropertyType] | None = None,
    # Floor area
    min_area_size: int | None = None,
    max_area_size: int | None = None,
    area_size_unit: str = "sqft",
    # Sorting & pagination
    sort_type: int | SortType = SortType.MOST_RECENT,
    index: int = 0,
    results_per_page: int = 24,
    # Location radius
    radius: float | None = None,
    # Recency filter
    added_to_site: AddedToSite | None = None,
    # RENT-only filters
    furnish_types: list[FurnishType] | None = None,
    let_type: LetType | None = None,
    # Feature filters
    must_have: list[MustHave] | None = None,
    dont_show: list[DontShow] | None = None,
    # BUY-only filters
    include_sstc: bool | None = None,
    new_homes_only: bool | None = None,
) -> SearchResults:
    """
    Perform a property search.

    Args:
        location_identifier: Rightmove location code, e.g. "REGION^96855".
        channel: "RENT" or "BUY".
        property_types: Filter by one or more property types.
        furnish_types: Furnished/unfurnished filter — RENT only.
        let_type: Long-term or short-term let — RENT only.
        must_have: Only show properties with these features.
        dont_show: Exclude properties with these categories.
        added_to_site: Only show properties added within this time window.
        include_sstc: Include sold-subject-to-contract — BUY only.
        new_homes_only: Restrict to new-build properties.
        sort_type: 6 = most recent, 1 = lowest price, 2 = highest price.
        index: Pagination offset (multiples of results_per_page).
    """
    search_url = SEARCH_URLS.get(channel, SEARCH_URLS["RENT"])

    params: dict = {
        "locationIdentifier": location_identifier,
        "index": index,
        "numberOfPropertiesPerPage": results_per_page,
        "sortType": int(sort_type),
        "viewType": "LIST",
        "areaSizeUnit": area_size_unit,
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
    if property_types:
        params["propertyTypes"] = ",".join(property_types)
    if min_area_size is not None:
        params["minAreaSize"] = min_area_size
    if max_area_size is not None:
        params["maxAreaSize"] = max_area_size
    if added_to_site is not None:
        params["addedToSite"] = added_to_site.value
    if furnish_types:
        params["furnishTypes"] = ",".join(furnish_types)
    if let_type is not None:
        params["letType"] = let_type.value
    if must_have:
        params["mustHave"] = ",".join(must_have)
    if dont_show:
        params["dontShow"] = ",".join(dont_show)
    if include_sstc is not None:
        params["includeSSTC"] = str(include_sstc).lower()
    if new_homes_only:
        params["newHomes"] = "true"

    response = await client.get(search_url, params=params)
    response.raise_for_status()

    next_data = _extract_next_data(response.text)
    search_results = next_data["props"]["pageProps"]["searchResults"]

    properties = [_parse_property(p) for p in search_results.get("properties", [])]

    return SearchResults(
        location=location_identifier,
        total_results=search_results.get("resultCount", len(properties)),
        page=index // results_per_page,
        results_per_page=results_per_page,
        properties=properties,
    )
