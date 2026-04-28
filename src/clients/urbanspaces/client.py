"""
Top-level UrbanSpacesClient.

Urban Spaces is a boutique agency with a small, fixed inventory (~12 listings).
There is no server-side filtering API, so all filtering is applied in Python
after fetching the listings page.
"""

import asyncio
from typing import Any

import httpx

from src.clients.urbanspaces import listings, property
from src.clients.urbanspaces.models import PropertyDetail, PropertySummary, SearchResults

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Referer": "https://www.urbanspaces.co.uk/",
}


class UrbanSpacesClient:
    """
    Async client for Urban Spaces property listings.

    Usage:
        async with UrbanSpacesClient() as client:
            results = await client.search(max_monthly_rent=3000, min_bedrooms=2)
    """

    def __init__(self, *, request_delay: float = 1.0, timeout: float = 30.0) -> None:
        self._delay = request_delay
        self._http = httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )

    async def __aenter__(self) -> "UrbanSpacesClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._http.aclose()

    async def close(self) -> None:
        await self._http.aclose()

    async def search(
        self,
        *,
        min_bedrooms: int | None = None,
        max_bedrooms: int | None = None,
        min_monthly_rent: int | None = None,
        max_monthly_rent: int | None = None,
        available_only: bool = False,
        property_type: str | None = None,
    ) -> SearchResults:
        """
        Fetch all listings and apply filters in Python.

        Args:
            min_bedrooms: Minimum bedroom count.
            max_bedrooms: Maximum bedroom count.
            min_monthly_rent: Minimum monthly rent (GBP).
            max_monthly_rent: Maximum monthly rent (GBP).
            available_only: If True, exclude "Under Offer" properties.
            property_type: Filter by type string, e.g. "Flat", "Mews house".
        """
        all_props = await listings.fetch_listings(self._http)
        await asyncio.sleep(self._delay)

        filtered = self._apply_filters(
            all_props,
            min_bedrooms=min_bedrooms,
            max_bedrooms=max_bedrooms,
            min_monthly_rent=min_monthly_rent,
            max_monthly_rent=max_monthly_rent,
            available_only=available_only,
            property_type=property_type,
        )

        return SearchResults(total_results=len(filtered), properties=filtered)

    async def get_property(self, url: str) -> PropertyDetail:
        """Fetch full details for a single property by its URL."""
        detail = await property.fetch_property(self._http, url)
        await asyncio.sleep(self._delay)
        return detail

    @staticmethod
    def _apply_filters(
        props: list[PropertySummary],
        *,
        min_bedrooms: int | None,
        max_bedrooms: int | None,
        min_monthly_rent: int | None,
        max_monthly_rent: int | None,
        available_only: bool,
        property_type: str | None,
    ) -> list[PropertySummary]:
        result = []
        for p in props:
            if available_only and p.status.lower() != "available":
                continue
            if min_bedrooms is not None and (p.bedrooms is None or p.bedrooms < min_bedrooms):
                continue
            if max_bedrooms is not None and (p.bedrooms is None or p.bedrooms > max_bedrooms):
                continue
            if min_monthly_rent is not None and p.price.monthly < min_monthly_rent:
                continue
            if max_monthly_rent is not None and p.price.monthly > max_monthly_rent:
                continue
            if property_type is not None and (
                p.property_type is None
                or property_type.lower() not in p.property_type.lower()
            ):
                continue
            result.append(p)
        return result
