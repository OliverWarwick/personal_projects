"""Scrape current bid prices from Hargreaves Lansdown instrument pages.

HL has no public price API and reverse-resolving an HL ``code`` (EPIC or
SEDOL) to its canonical URL is unreliable, so URLs are recorded once in
``config/hl_instruments.yaml`` and fetched on demand here. Each result is
cached on disk for 24 hours so repeat lookups within a day are free and
HL is not hammered for instruments whose price barely moves (gilts, OEICs).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

import httpx
import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

# 24 hours — gilt/OEIC prices update once per business day, and HL itself
# serves stale prices outside market hours, so a shorter TTL would just
# generate extra requests for no fresher data.
DEFAULT_TTL_SECONDS = 24 * 60 * 60

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "personal_project/0 (+local)"
)

# HL renders the headline bid price as e.g.
#     <span class="bid price-divide" >450.96p</span>
# The trailing "p" denotes pence. Some pages show pounds (£) instead, e.g.
# for gilts; the parser handles both.
_BID_RE = re.compile(
    r'class="bid price-divide"[^>]*>\s*£?([0-9][0-9,]*\.?\d*)\s*(p?)\s*<',
    re.IGNORECASE,
)

# "Prices as at 22 May 2026" — the human-readable timestamp HL embeds
# beneath the price. We capture it for downstream display.
_AS_AT_RE = re.compile(r"Prices?\s+as\s+at\s+([0-9]{1,2}\s+[A-Za-z]+\s+\d{4})")


@dataclass(frozen=True, slots=True)
class HLPrice:
    """One scraped price quote.

    Attributes:
        code: HL instrument code as it appears in the snapshot CSV.
        price_pence: Bid price expressed in pence. Pages quoted in £ are
            converted to pence (× 100) so the value is comparable to the
            ``Price (pence)`` column in account-summary exports.
        as_at: Human-readable date string from the page (e.g.
            ``"22 May 2026"``); ``None`` if HL did not embed one.
        fetched_at: UTC timestamp when the price was scraped.
        url: Source URL that was fetched.

    """

    code: str
    price_pence: Decimal
    as_at: str | None
    fetched_at: datetime
    url: str


@dataclass(frozen=True, slots=True)
class _InstrumentEntry:
    """One row of ``config/hl_instruments.yaml``."""

    code: str
    name: str
    url: str | None


def _default_config_path() -> Path:
    """Return the bundled instrument map path."""
    return (
        Path(__file__).resolve().parents[2]
        / "config"
        / "hl_instruments.yaml"
    )


def _default_cache_dir() -> Path:
    """Return the on-disk cache directory.

    ``$HL_PRICE_CACHE_DIR`` overrides the default
    (``~/.cache/personal_project/hl_prices``). The directory is created
    lazily on first write.
    """
    raw = os.environ.get("HL_PRICE_CACHE_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".cache" / "personal_project" / "hl_prices"


def load_instrument_map(path: Path | None = None) -> dict[str, _InstrumentEntry]:
    """Load the ``code → InstrumentEntry`` map from YAML.

    Args:
        path: Override path to the instrument YAML. Defaults to the
            bundled ``config/hl_instruments.yaml``.

    Returns:
        Dict keyed by HL code. Entries with ``url: null`` are included so
        callers can report which instruments need to be populated.

    """
    target = path if path is not None else _default_config_path()
    raw = cast("dict[str, object]", yaml.safe_load(target.read_text()) or {})
    items = cast("list[dict[str, object]]", raw.get("instruments") or [])
    out: dict[str, _InstrumentEntry] = {}
    for item in items:
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        name = str(item.get("name") or code)
        url_raw = item.get("url")
        url = str(url_raw).strip() if isinstance(url_raw, str) and url_raw else None
        out[code] = _InstrumentEntry(code=code, name=name, url=url)
    return out


def _parse_price(html: str) -> Decimal | None:
    """Extract the bid price in pence from an HL instrument page."""
    match = _BID_RE.search(html)
    if match is None:
        return None
    value = Decimal(match.group(1).replace(",", ""))
    unit = match.group(2).lower()
    if not unit:
        # Page quoted in pounds (no trailing 'p'): convert to pence so the
        # caller always sees the same unit.
        value *= Decimal(100)
    return value


def _parse_as_at(html: str) -> str | None:
    """Return the ``Prices as at ...`` timestamp from the page, if present."""
    match = _AS_AT_RE.search(html)
    return match.group(1) if match else None


def _cache_path(cache_dir: Path, code: str) -> Path:
    """Return the cache file path for one instrument code."""
    return cache_dir / f"{code}.json"


def _read_cache(path: Path, ttl_seconds: int) -> HLPrice | None:
    """Return a cached price if it exists and is fresh, else ``None``."""
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > ttl_seconds:
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return HLPrice(
        code=str(data["code"]),
        price_pence=Decimal(str(data["price_pence"])),
        as_at=data.get("as_at"),
        fetched_at=datetime.fromisoformat(data["fetched_at"]),
        url=str(data["url"]),
    )


def _write_cache(path: Path, price: HLPrice) -> None:
    """Persist a price to the cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "code": price.code,
        "price_pence": str(price.price_pence),
        "as_at": price.as_at,
        "fetched_at": price.fetched_at.isoformat(),
        "url": price.url,
    }
    path.write_text(json.dumps(payload, indent=2))


def fetch_price(
    code: str,
    *,
    instruments: dict[str, _InstrumentEntry] | None = None,
    cache_dir: Path | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    force_refresh: bool = False,
    client: httpx.Client | None = None,
) -> HLPrice:
    """Return the current bid price (in pence) for one HL instrument.

    Args:
        code: HL instrument code (EPIC for listed shares/ETFs, SEDOL-like
            alphanumeric for funds/gilts) — the same value seen in the
            ``Code`` column of an HL account-summary CSV.
        instruments: Optional pre-loaded instrument map.
        cache_dir: Override for the on-disk cache directory.
        ttl_seconds: Cache freshness window. Defaults to 24 hours.
        force_refresh: When true, bypass the cache and re-fetch.
        client: Optional injected HTTP client (useful for tests).

    Returns:
        Populated :class:`HLPrice`.

    Raises:
        KeyError: When ``code`` is not present in the instrument map.
        RuntimeError: When the entry has no URL or the page does not
            contain a recognisable price block.
        httpx.HTTPError: For network/HTTP failures.

    """
    inst_map = instruments if instruments is not None else load_instrument_map()
    entry = inst_map.get(code)
    if entry is None:
        raise KeyError(f"HL code not in instrument map: {code}")
    if entry.url is None:
        raise RuntimeError(
            f"HL code {code} ({entry.name}) has no URL configured in "
            "config/hl_instruments.yaml — add one before fetching prices.",
        )

    cache_root = cache_dir if cache_dir is not None else _default_cache_dir()
    path = _cache_path(cache_root, code)
    if not force_refresh:
        cached = _read_cache(path, ttl_seconds)
        if cached is not None:
            logger.debug("HL price for %s served from cache (%s)", code, path)
            return cached

    owns_client = client is None
    http = client if client is not None else httpx.Client(
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
        timeout=20.0,
        follow_redirects=True,
    )
    try:
        response = http.get(entry.url)
        response.raise_for_status()
        html = response.text
    finally:
        if owns_client:
            http.close()

    price_pence = _parse_price(html)
    if price_pence is None:
        raise RuntimeError(
            f"Could not find a bid price on HL page for {code}: {entry.url}",
        )

    price = HLPrice(
        code=code,
        price_pence=price_pence,
        as_at=_parse_as_at(html),
        fetched_at=datetime.now(UTC),
        url=entry.url,
    )
    _write_cache(path, price)
    return price


def fetch_prices(
    codes: Iterable[str],
    *,
    force_refresh: bool = False,
    cache_dir: Path | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, HLPrice | Exception]:
    """Fetch many HL prices, returning per-code success or the raised error.

    Failures (missing URL, network error) are returned in the result map
    rather than raised so callers can render a per-instrument table without
    aborting on the first bad code.
    """
    instruments = load_instrument_map()
    out: dict[str, HLPrice | Exception] = {}
    with httpx.Client(
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
        timeout=20.0,
        follow_redirects=True,
    ) as client:
        for code in codes:
            try:
                out[code] = fetch_price(
                    code,
                    instruments=instruments,
                    cache_dir=cache_dir,
                    ttl_seconds=ttl_seconds,
                    force_refresh=force_refresh,
                    client=client,
                )
            except Exception as exc:  # noqa: BLE001  # surfaced in the result map
                out[code] = exc
    return out
