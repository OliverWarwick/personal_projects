"""
Top-level OpenRentClient.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx

from src.clients.openrent import property, search
from src.clients.openrent.models import PropertyDetail, PropertySummary, SearchResults

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Referer": "https://www.openrent.co.uk/",
}

_PAGE_SIZE = 20


class OpenRentClient:
    """
    Async client for OpenRent property search and detail fetching.

    Usage:
        async with OpenRentClient() as client:
            results = await client.search(
                term="Shad Thames, Bermondsey, Southwark",
                max_price=2500,
                max_bedrooms=2,
            )
    """

    def __init__(self, *, request_delay: float = 1.5, timeout: float = 30.0) -> None:
        self._delay = request_delay
        self._http = httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            http2=True,
        )

    async def __aenter__(self) -> "OpenRentClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._http.aclose()

    async def close(self) -> None:
        await self._http.aclose()

    async def search(
        self,
        *,
        term: str,
        min_price: int | None = None,
        max_price: int | None = None,
        min_bedrooms: int | None = None,
        max_bedrooms: int | None = None,
        accept_dss: bool | None = None,
        include_bills: bool | None = None,
        skip: int = 0,
    ) -> SearchResults:
        """
        Fetch one page of search results (up to 20 properties).

        Args:
            term: Free-text location, e.g. "Shad Thames, Bermondsey, Southwark".
            min_price / max_price: Monthly rent bounds in GBP.
            min_bedrooms / max_bedrooms: Bedroom count bounds.
            accept_dss: Include DSS / housing-benefit properties.
            include_bills: Only show properties with bills included.
            skip: Pagination offset — pass multiples of 20 to page through results.
        """
        results = await search.search(
            self._http,
            term=term,
            min_price=min_price,
            max_price=max_price,
            min_bedrooms=min_bedrooms,
            max_bedrooms=max_bedrooms,
            accept_dss=accept_dss,
            include_bills=include_bills,
            skip=skip,
        )
        await asyncio.sleep(self._delay)
        return results

    async def search_all(
        self,
        *,
        term: str,
        min_price: int | None = None,
        max_price: int | None = None,
        min_bedrooms: int | None = None,
        max_bedrooms: int | None = None,
        accept_dss: bool | None = None,
        include_bills: bool | None = None,
        max_results: int = 500,
    ) -> AsyncIterator[PropertySummary]:
        """
        Async generator that paginates through all results, yielding each property.

        Stops when the last page returns fewer than 20 results or max_results is reached.
        """
        skip = 0
        yielded = 0

        while True:
            page = await self.search(
                term=term,
                min_price=min_price,
                max_price=max_price,
                min_bedrooms=min_bedrooms,
                max_bedrooms=max_bedrooms,
                accept_dss=accept_dss,
                include_bills=include_bills,
                skip=skip,
            )

            for prop in page.properties:
                yield prop
                yielded += 1
                if yielded >= max_results:
                    return

            if len(page.properties) < _PAGE_SIZE:
                return  # last page

            skip += _PAGE_SIZE

    async def get_property(self, url: str) -> PropertyDetail:
        """
        Fetch full details for a single property.

        Args:
            url: Full OpenRent property URL, e.g. the one returned in PropertySummary.url.
        """
        detail = await property.fetch_property(self._http, url)
        await asyncio.sleep(self._delay)
        return detail
