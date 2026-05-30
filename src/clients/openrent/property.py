"""
Fetch full property details from an OpenRent property page.

The property page renders data as HTML tables and divs. HTML entities (&#xA3; for £)
mean we must extract text via parsel (which resolves entities) rather than raw HTML regex.
"""

import contextlib
import re

import httpx
from parsel import Selector

from src.clients.openrent.models import PropertyDetail

_ID_RE = re.compile(r"/(\d+)$")
_PRICE_RE = re.compile(r"£([\d,]+)")
_DEPOSIT_RE = re.compile(r"Deposit[^£]*£([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_BEDROOMS_RE = re.compile(r"(\d+)\s+bedroom", re.IGNORECASE)
_LAT_RE = re.compile(r"[\"']lat(?:itude)?[\"']\s*:\s*([-\d.]+)", re.IGNORECASE)
_LNG_RE = re.compile(r"[\"']l(?:ng|ongitude)[\"']\s*:\s*([-\d.]+)", re.IGNORECASE)


def _table_value(sel: Selector, label: str) -> str | None:
    """Return the text of the <td> immediately after a <td> containing label."""
    return sel.xpath(f"//td[normalize-space(.)='{label}']/following-sibling::td[1]/text()").get()


def _parse_detail(url: str, html: str) -> PropertyDetail:
    sel = Selector(text=html)

    # Resolved text for regex (entities like &#xA3; → £)
    body_text = " ".join(sel.css("body ::text").getall())

    # ID from URL
    id_m = _ID_RE.search(url.rstrip("/"))
    property_id = int(id_m.group(1)) if id_m else 0

    # Title — h1 first, fall back to h2
    title = (sel.css("h1::text").get() or sel.css("h2::text").get() or "").strip()

    # Price — "Rent PCM" table row → £2,425.00, or fall back to first £X,XXX in body
    price_pcm = 0
    rent_raw = _table_value(sel, "Rent PCM")
    if rent_raw:
        price_m = _PRICE_RE.search(rent_raw)
        if price_m:
            price_pcm = int(price_m.group(1).replace(",", ""))
    if not price_pcm:
        price_m = _PRICE_RE.search(body_text)
        if price_m:
            price_pcm = int(price_m.group(1).replace(",", ""))

    # Bedrooms from title first, then body
    bed_m = _BEDROOMS_RE.search(title) or _BEDROOMS_RE.search(body_text)
    bedrooms = int(bed_m.group(1)) if bed_m else None

    # Deposit — table row or regex fallback
    deposit = None
    deposit_raw = _table_value(sel, "Deposit") or _table_value(sel, "Deposit / Bond")
    if deposit_raw:
        dep_price_m = _PRICE_RE.search(deposit_raw)
        if dep_price_m:
            with contextlib.suppress(ValueError):
                deposit = float(dep_price_m.group(1).replace(",", ""))
    if deposit is None:
        dep_m = _DEPOSIT_RE.search(body_text)
        if dep_m:
            with contextlib.suppress(ValueError):
                deposit = float(dep_m.group(1).replace(",", ""))

    # Available date — table row
    available_date = _table_value(sel, "Available From") or _table_value(sel, "Available from")
    if available_date:
        available_date = available_date.strip()

    # Furnished — table row or body text scan
    furnished = _table_value(sel, "Furnishing") or _table_value(sel, "Furnished")
    if not furnished:
        for label in ("Part Furnished", "Part-Furnished", "Furnished", "Unfurnished"):
            if re.search(re.escape(label), body_text, re.IGNORECASE):
                furnished = label.replace("-", " ")
                break

    # Bills and pets (body text)
    bills_included = bool(re.search(r"bills?\s+included", body_text, re.IGNORECASE))
    pets_allowed = bool(
        re.search(r"pets?\s+(?:allowed|welcome|considered)", body_text, re.IGNORECASE)
    )

    # Description
    description = " ".join(sel.css("#descriptionText ::text").getall()).strip() or None

    # Images — deduplicated, excluding static map thumbnails
    image_urls: list[str] = []
    seen: set[str] = set()
    for img in sel.css("img[src*='imagescdn.openrent.co.uk/listings']"):
        src = img.attrib.get("src", "")
        if src.startswith("//"):
            src = "https:" + src
        if src and "staticMap" not in src and src not in seen:
            seen.add(src)
            image_urls.append(src)

    # Coordinates — embedded in inline scripts
    latitude = longitude = None
    lat_m = _LAT_RE.search(html)
    lng_m = _LNG_RE.search(html)
    if lat_m:
        with contextlib.suppress(ValueError):
            latitude = float(lat_m.group(1))
    if lng_m:
        with contextlib.suppress(ValueError):
            longitude = float(lng_m.group(1))

    return PropertyDetail(
        id=property_id,
        title=title,
        price_pcm=price_pcm,
        bedrooms=bedrooms,
        url=url,
        deposit=deposit,
        available_date=available_date,
        furnished=furnished,
        bills_included=bills_included,
        pets_allowed=pets_allowed,
        description=description,
        latitude=latitude,
        longitude=longitude,
        image_urls=image_urls,
    )


async def fetch_property(client: httpx.AsyncClient, url: str) -> PropertyDetail:
    response = await client.get(url)
    response.raise_for_status()
    return _parse_detail(url, response.text)
