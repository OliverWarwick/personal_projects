"""
Parse OpenRent search results.

OpenRent renders property cards as <a href="/property-to-rent/{city}/{slug}/{id}"> anchors.
Alongside the HTML, the page embeds parallel JS arrays (e.g. var bedrooms = [...]) which
are more reliable for numeric fields than scraping card text.
"""

import re

import httpx
from parsel import Selector

from src.clients.openrent.models import PropertySummary, SearchResults

BASE_URL = "https://www.openrent.co.uk"
SEARCH_URL = "https://www.openrent.co.uk/properties-to-rent/"

_HREF_RE = re.compile(r"^/property-to-rent/[^/]+/[^/]+/(\d+)$")
_PRICE_RE = re.compile(r"£([\d,]+)")
_TOTAL_RE = re.compile(r"([\d,]+)\s+propert", re.IGNORECASE)


def _extract_js_int_array(html: str, name: str) -> list[int]:
    """Extract a JS array like `var bedrooms = [1, 2, ...]` from the page source."""
    pattern = re.compile(rf"var\s+{re.escape(name)}\s*=\s*\[([\d,\s]+)\]")
    m = pattern.search(html)
    if not m:
        return []
    return [int(x.strip()) for x in m.group(1).split(",") if x.strip().isdigit()]


def _parse_cards(sel: Selector, page_text: str) -> list[dict]:
    """
    Extract property cards from the search results page.

    Each card is an <a> linking to /property-to-rent/{city}/{slug}/{id}.
    The image alt attribute carries the clean property title.
    Prices use HTML entities (&#xA3; for £) so we search the resolved page_text.
    """
    cards = []
    seen_ids: set[int] = set()

    for anchor in sel.css("a[href]"):
        href = anchor.attrib.get("href", "")
        m = _HREF_RE.match(href)
        if not m:
            continue
        property_id = int(m.group(1))
        if property_id in seen_ids:
            continue
        seen_ids.add(property_id)

        # img alt is the cleanest source of title (e.g. "1 Bed Flat, Shad Thames, SE1")
        title = anchor.css("img::attr(alt)").get("").strip()

        img_src = anchor.css("img::attr(src)").get()
        thumbnail = None
        if img_src:
            thumbnail = ("https:" + img_src) if img_src.startswith("//") else img_src

        # Price from resolved anchor text (entities decoded)
        anchor_text = " ".join(anchor.css("::text").getall())
        price_m = _PRICE_RE.search(anchor_text)
        price_pcm = int(price_m.group(1).replace(",", "")) if price_m else 0

        cards.append(
            {
                "id": property_id,
                "href": href,
                "thumbnail": thumbnail,
                "title": title,
                "price_pcm": price_pcm,
            }
        )

    return cards


async def search(
    client: httpx.AsyncClient,
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
    Fetch one page of OpenRent search results.

    Args:
        term: Free-text location, e.g. "Shad Thames, Bermondsey, Southwark".
        min_price / max_price: Monthly rent bounds in GBP.
        min_bedrooms / max_bedrooms: Bedroom count bounds.
        accept_dss: Include DSS/housing-benefit properties.
        include_bills: Only show properties with bills included.
        skip: Pagination offset (multiples of 20).
    """
    params: dict = {"term": term, "isLive": "1", "skip": skip}
    if min_price is not None:
        params["prices_min"] = min_price
    if max_price is not None:
        params["prices_max"] = max_price
    if min_bedrooms is not None:
        params["bedrooms_min"] = min_bedrooms
    if max_bedrooms is not None:
        params["bedrooms_max"] = max_bedrooms
    if accept_dss is not None:
        params["acceptDSS"] = "true" if accept_dss else "false"
    if include_bills is not None:
        params["includeBills"] = "true" if include_bills else "false"

    response = await client.get(SEARCH_URL, params=params)
    response.raise_for_status()
    html = response.text

    sel = Selector(text=html)
    # Resolve HTML entities for regex (&#xA3; → £, &amp; → &, etc.)
    page_text = " ".join(sel.css("body ::text").getall())

    total_m = _TOTAL_RE.search(page_text)
    total = int(total_m.group(1).replace(",", "")) if total_m else 0

    # Use the parallel JS array for bedrooms — more reliable than card text
    bedrooms_list = _extract_js_int_array(html, "bedrooms")

    cards = _parse_cards(sel, page_text)

    properties = [
        PropertySummary(
            id=card["id"],
            title=card["title"],
            price_pcm=card["price_pcm"],
            bedrooms=bedrooms_list[i] if i < len(bedrooms_list) else None,
            url=BASE_URL + card["href"],
            thumbnail_url=card["thumbnail"],
        )
        for i, card in enumerate(cards)
    ]

    return SearchResults(
        term=term,
        total_results=total,
        page=skip // 20,
        properties=properties,
    )
