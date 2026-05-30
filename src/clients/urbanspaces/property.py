"""
Fetch full property details from an individual Urban Spaces property page.

The page embeds a property-specific JSON object in a <script> tag with
a pattern we can locate via json.JSONDecoder.raw_decode.
"""

import json
import re

import httpx

from src.clients.urbanspaces.models import Address, Price, PropertyDetail

BASE_URL = "https://www.urbanspaces.co.uk"
IMAGE_BASE = "https://www.urbanspaces.co.uk/"

_PRICE_NUM_RE = re.compile(r"[\d,]+\.?\d*")


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


def _extract_block(html: str, marker: str) -> dict | None:
    """Locate a JSON object starting with `marker` and decode it."""
    idx = html.find(marker)
    if idx == -1:
        return None
    # Walk back to the opening brace
    brace_idx = html.rfind("{", 0, idx)
    if brace_idx == -1:
        return None
    decoder = json.JSONDecoder()
    try:
        data, _ = decoder.raw_decode(html, brace_idx)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return None


def _parse_price_str(text: str) -> float:
    m = _PRICE_NUM_RE.search(text.replace(",", ""))
    return float(m.group()) if m else 0.0


def _parse_detail(url: str, raw: dict) -> PropertyDetail:
    weekly = _parse_price_str(str(raw.get("weekly_rent", raw.get("price", "0"))))
    monthly_raw = raw.get("monthly_rent", 0)
    try:
        monthly = float(str(monthly_raw).replace(",", ""))
    except (ValueError, TypeError):
        monthly = 0.0

    images_raw = raw.get("images", [])
    image_urls = [
        IMAGE_BASE + img["local_image"]
        for img in images_raw
        if img.get("local_image") and img.get("type") == "Image"
    ]
    floorplan_urls = [
        IMAGE_BASE + img["local_image"]
        for img in images_raw
        if img.get("local_image") and img.get("type") == "Floorplan"
    ]

    bedrooms_raw = raw.get("bedrooms")
    try:
        bedrooms = int(bedrooms_raw) if bedrooms_raw is not None else None
    except (ValueError, TypeError):
        bedrooms = None

    sq_ft_raw = raw.get("square_footage") or raw.get("sqft") or raw.get("size")
    try:
        sq_ft = int(str(sq_ft_raw).replace(",", "")) if sq_ft_raw else None
    except (ValueError, TypeError):
        sq_ft = None

    deposit_raw = raw.get("deposit")
    try:
        deposit = float(str(deposit_raw).replace(",", "")) if deposit_raw else None
    except (ValueError, TypeError):
        deposit = None

    details_link = raw.get("details_link", "")

    return PropertyDetail(
        record_id=str(raw.get("record_id", "")),
        pcode=str(raw.get("pcode", "")),
        bedrooms=bedrooms,
        property_type=raw.get("type"),
        address=Address(
            display_address=raw.get("display_address", ""),
            postcode=raw.get("short_postcode"),
        ),
        price=Price(weekly=weekly, monthly=monthly),
        status=raw.get("status", "Unknown"),
        accommodation_summary=_parse_list_field(raw.get("accommodation_summary")),
        url=url if url else f"{BASE_URL}/rent/{details_link}",
        description=raw.get("short_description") or raw.get("description"),
        weekly_rent=weekly,
        deposit=deposit,
        council_tax_band=raw.get("council_tax_band"),
        sq_ft=sq_ft,
        latitude=raw.get("latitude"),
        longitude=raw.get("longitude"),
        image_urls=image_urls,
        floorplan_url=floorplan_urls[0] if floorplan_urls else None,
        available_date=raw.get("live_date") or raw.get("let_available_date"),
    )


async def fetch_property(client: httpx.AsyncClient, url: str) -> PropertyDetail:
    response = await client.get(url)
    response.raise_for_status()
    html = response.text

    # The detail page embeds a single property object — find it by the pcode field
    raw = _extract_block(html, '"pcode"')
    if raw is None:
        # Fallback: try record_id marker
        raw = _extract_block(html, '"record_id"')
    if raw is None:
        raise ValueError(f"Could not find property data in page: {url}")

    return _parse_detail(url, raw)
