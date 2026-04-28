"""
Fetch full property details by scraping the property page.

Rightmove embeds a PAGE_MODEL JSON blob in a <script> tag which contains
all the property data — we extract that rather than parsing HTML.
"""

import json
import re

import httpx

from src.clients.rightmove.models import Address, Price, PropertyDetail

PROPERTY_URL = "https://www.rightmove.co.uk/properties/{property_id}"
_PAGE_MODEL_RE = re.compile(r"window\.PAGE_MODEL\s*=\s*(\{.*?\});", re.DOTALL)


def _extract_page_model(html: str) -> dict:
    match = _PAGE_MODEL_RE.search(html)
    if not match:
        raise ValueError("Could not find PAGE_MODEL in page source")
    return json.loads(match.group(1))


def _parse_detail(property_id: int, raw: dict) -> PropertyDetail:
    prop = raw.get("propertyData", {})
    price_info = prop.get("prices", {}).get("primaryPrice", "") or ""

    # Price can be a formatted string like "£2,500 pcm" — parse it out
    amount = 0
    frequency = None
    price_match = re.search(r"[\d,]+", price_info.replace(",", ""))
    if price_match:
        amount = int(price_match.group().replace(",", ""))
    if "pcm" in price_info.lower():
        frequency = "monthly"
    elif "pw" in price_info.lower():
        frequency = "weekly"

    images = [
        img.get("url", "")
        for img in prop.get("images", [])
        if img.get("url")
    ]
    floorplans = [
        fp.get("url", "")
        for fp in prop.get("floorplans", [])
        if fp.get("url")
    ]

    location = prop.get("location", {})
    agent = prop.get("customer", {})

    return PropertyDetail(
        id=property_id,
        bedrooms=prop.get("bedrooms"),
        bathrooms=prop.get("bathrooms"),
        property_type=prop.get("propertySubType"),
        address=Address(
            display_address=prop.get("address", {}).get("displayAddress", ""),
            country_code=prop.get("address", {}).get("countryCode"),
        ),
        price=Price(
            amount=amount,
            currency_code="GBP",
            frequency=frequency,
        ),
        url=PROPERTY_URL.format(property_id=property_id),
        description=prop.get("text", {}).get("description"),
        key_features=prop.get("keyFeatures", []),
        image_urls=images,
        floorplan_urls=floorplans,
        latitude=location.get("latitude"),
        longitude=location.get("longitude"),
        size_sq_ft=prop.get("sizings", [{}])[0].get("minimumSize") if prop.get("sizings") else None,
        agent_name=agent.get("brandPlusDisplayName"),
        agent_phone=agent.get("telephone"),
        raw=prop,
    )


async def fetch_property(
    client: httpx.AsyncClient,
    property_id: int,
) -> PropertyDetail:
    url = PROPERTY_URL.format(property_id=property_id)
    response = await client.get(url)
    response.raise_for_status()

    page_model = _extract_page_model(response.text)
    return _parse_detail(property_id, page_model)
