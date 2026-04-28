"""
Top-level RightmoveClient.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx

from src.clients.rightmove import locations, property, search
from src.clients.rightmove.models import (
    AddedToSite,
    DontShow,
    FurnishType,
    LetType,
    MustHave,
    PropertyDetail,
    PropertySummary,
    PropertyType,
    SearchResults,
    SortType,
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "application/json, text/html, */*",
    "Referer": "https://www.rightmove.co.uk/",
}

# Rightmove caps results at 42 pages of 24 = 1,008
_MAX_INDEX = 1008


class RightmoveClient:
    """
    Async client for Rightmove property search and detail fetching.

    Usage:
        async with RightmoveClient() as client:
            results = await client.search(
                location_identifier="REGION^96855",
                max_price=3000,
                max_bedrooms=2,
            )
    """

    def __init__(
        self,
        *,
        request_delay: float = 1.0,
        timeout: float = 30.0,
    ) -> None:
        self._delay = request_delay
        self._http = httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            http2=True,
        )

    async def __aenter__(self) -> "RightmoveClient":
        await self._warm_up()
        return self

    async def _warm_up(self) -> None:
        """
        Visit the Rightmove homepage to obtain session cookies that are
        required for search pages to return data rather than an error page.
        """
        await self._http.get(search.WARMUP_URL)

    async def __aexit__(self, *_: Any) -> None:
        await self._http.aclose()

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Location lookup
    # ------------------------------------------------------------------

    async def find_location(self, query: str) -> list[dict]:
        """Return location suggestions for a free-text query."""
        return await locations.search_location(query, self._http)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
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
        """Fetch a single page of search results."""
        results = await search.search(
            self._http,
            location_identifier=location_identifier,
            channel=channel,
            min_price=min_price,
            max_price=max_price,
            min_bedrooms=min_bedrooms,
            max_bedrooms=max_bedrooms,
            property_types=property_types,
            min_area_size=min_area_size,
            max_area_size=max_area_size,
            area_size_unit=area_size_unit,
            sort_type=sort_type,
            index=index,
            results_per_page=results_per_page,
            radius=radius,
            added_to_site=added_to_site,
            furnish_types=furnish_types,
            let_type=let_type,
            must_have=must_have,
            dont_show=dont_show,
            include_sstc=include_sstc,
            new_homes_only=new_homes_only,
        )
        await asyncio.sleep(self._delay)
        return results

    async def search_all(
        self,
        *,
        location_identifier: str,
        channel: str = "RENT",
        min_price: int | None = None,
        max_price: int | None = None,
        min_bedrooms: int | None = None,
        max_bedrooms: int | None = None,
        property_types: list[PropertyType] | None = None,
        min_area_size: int | None = None,
        max_area_size: int | None = None,
        area_size_unit: str = "sqft",
        sort_type: int | SortType = SortType.MOST_RECENT,
        radius: float | None = None,
        added_to_site: AddedToSite | None = None,
        furnish_types: list[FurnishType] | None = None,
        let_type: LetType | None = None,
        must_have: list[MustHave] | None = None,
        dont_show: list[DontShow] | None = None,
        include_sstc: bool | None = None,
        new_homes_only: bool | None = None,
        max_results: int = _MAX_INDEX,
    ) -> AsyncIterator[PropertySummary]:
        """
        Async generator that paginates through all results, yielding each property.
        Stops at Rightmove's hard cap (~1,008 results) or max_results.
        """
        results_per_page = 24
        index = 0
        yielded = 0

        while True:
            page = await self.search(
                location_identifier=location_identifier,
                channel=channel,
                min_price=min_price,
                max_price=max_price,
                min_bedrooms=min_bedrooms,
                max_bedrooms=max_bedrooms,
                property_types=property_types,
                min_area_size=min_area_size,
                max_area_size=max_area_size,
                area_size_unit=area_size_unit,
                sort_type=sort_type,
                index=index,
                results_per_page=results_per_page,
                radius=radius,
                added_to_site=added_to_site,
                furnish_types=furnish_types,
                let_type=let_type,
                must_have=must_have,
                dont_show=dont_show,
                include_sstc=include_sstc,
                new_homes_only=new_homes_only,
            )

            for prop in page.properties:
                yield prop
                yielded += 1
                if yielded >= max_results:
                    return

            if len(page.properties) < results_per_page:
                return  # last page

            index += results_per_page
            if index >= min(_MAX_INDEX, max_results):
                return

    # ------------------------------------------------------------------
    # Property detail
    # ------------------------------------------------------------------

    async def get_property(self, property_id: int) -> PropertyDetail:
        """Fetch full details for a single property."""
        detail = await property.fetch_property(self._http, property_id)
        await asyncio.sleep(self._delay)
        return detail
