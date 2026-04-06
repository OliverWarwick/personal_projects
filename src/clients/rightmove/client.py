"""
Top-level RightmoveClient.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx

from . import locations, property, search
from .models import PropertyDetail, PropertySummary, SearchResults

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

# Rightmove caps results at 42 pages of 24 = 1,008; we use 24 per page
_MAX_INDEX = 1008


class RightmoveClient:
    """
    Async client for Rightmove property search and detail fetching.

    Usage:
        async with RightmoveClient() as client:
            results = await client.search(
                location_identifier="REGION^87498",
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
        return self

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
        min_price: int | None = None,
        max_price: int | None = None,
        min_bedrooms: int | None = None,
        max_bedrooms: int | None = None,
        index: int = 0,
        results_per_page: int = 24,
        sort_type: int = 6,
        radius: float | None = None,
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
            index=index,
            results_per_page=results_per_page,
            sort_type=sort_type,
            radius=radius,
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
        sort_type: int = 6,
        radius: float | None = None,
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
                index=index,
                results_per_page=results_per_page,
                sort_type=sort_type,
                radius=radius,
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
