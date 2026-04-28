"""
Parse all property listings from the Urban Spaces rent page.

Urban Spaces embeds a JSON array of property objects directly in a <script> tag.
We find it by locating the start of the array and using json.JSONDecoder.raw_decode
to parse from that position, which correctly handles nested objects.
"""

import json
import re

import httpx

from src.clients.urbanspaces.models import Address, Price, PropertySummary

LISTINGS_URL = "https://www.urbanspaces.co.uk/rent/spaces-to-rent"
BASE_URL = "https://www.urbanspaces.co.uk"
IMAGE_BASE = "https://www.urbanspaces.co.uk/"

_PRICE_RE = re.compile(r"[\d,]+\.?\d*")


def _parse_list_field(value: object) -> list[str]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return []


def _parse_price(raw: dict) -> Price:
    monthly = float(raw.get("monthly_rent", 0) or 0)
    price_str = raw.get("price", "0")
    try:
        weekly = float(str(price_str).replace(",", ""))
    except ValueError:
        weekly = 0.0
    return Price(weekly=weekly, monthly=monthly)


def _parse_summary(raw: dict) -> PropertySummary:
    images = raw.get("images", [])
    thumbnail = None
    if images:
        local = images[0].get("listing_image") or images[0].get("local_image")
        if local:
            thumbnail = IMAGE_BASE + local

    details_link = raw.get("details_link", "")
    url = f"{BASE_URL}/rent/{details_link}" if details_link else BASE_URL

    bedrooms_raw = raw.get("bedrooms")
    try:
        bedrooms = int(bedrooms_raw) if bedrooms_raw is not None else None
    except (ValueError, TypeError):
        bedrooms = None

    return PropertySummary(
        record_id=str(raw.get("record_id", "")),
        pcode=str(raw.get("pcode", "")),
        bedrooms=bedrooms,
        property_type=raw.get("type"),
        address=Address(
            display_address=raw.get("display_address", ""),
            postcode=raw.get("short_postcode"),
        ),
        price=_parse_price(raw),
        status=raw.get("status", "Unknown"),
        thumbnail_url=thumbnail,
        accommodation_summary=_parse_list_field(raw.get("accommodation_summary")),
        url=url,
    )


def _extract_properties(html: str) -> list[dict]:
    """
    Find the JSON array of property objects embedded in the page HTML.
    Locates the first occurrence of [{"record_id" and decodes from that position.
    """
    marker = '[{"record_id"'
    idx = html.find(marker)
    if idx == -1:
        return []
    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(html, idx)
    if isinstance(data, list):
        return data
    return []


async def fetch_listings(client: httpx.AsyncClient) -> list[PropertySummary]:
    response = await client.get(LISTINGS_URL)
    response.raise_for_status()
    raw_properties = _extract_properties(response.text)
    return [_parse_summary(p) for p in raw_properties]
