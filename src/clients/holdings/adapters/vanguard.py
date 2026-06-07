"""Vanguard UK holdings adapter.

Vanguard publishes no downloadable constituent file, but the investor site
(``vanguardinvestor.co.uk``) is backed by a GraphQL gateway whose
``borHoldings`` query returns full constituents for a fund's ``portId``. The
gateway accepts ad-hoc queries (introspection is disabled, but named queries
over real fields work) and paginates via an opaque ``lastItemKey`` cursor.

The fund's ``portId`` (e.g. ``9694`` for VUAG, ``9677`` for VHYG) is stored
as the identity ``product_id``. Fetches run behind the holdings cache, so the
GraphQL endpoint is hit at most once per fund per cache-TTL.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import httpx

from src.clients.holdings.adapters.base import (
    HoldingsError,
    IssuerAdapter,
    renormalise_to_unit,
)
from src.clients.holdings.models import Constituent, FundHoldings

if TYPE_CHECKING:
    from src.clients.holdings.identity import FundIdentity

logger = logging.getLogger(__name__)

_GRAPHQL_URL = "https://www.vanguardinvestor.co.uk/gpx/graphql"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# Full constituent query (named, ad-hoc — the gateway rejects introspection
# but accepts queries over real fields). Paginates by lastItemKey; limit 1500
# covers most funds in one page, but FTSE All-World lines exceed it.
_HOLDINGS_QUERY = (
    "query FundsHoldingsQuery($p:[String!],$k:String){"
    " borHoldings(portIds:$p){"
    " holdings(limit:1500,lastItemKey:$k){"
    " lastItemKey"
    " items{ ticker issuerName securityLongDescription"
    " marketValuePercentage sedol1 securityType } } } }"
)
_MAX_PAGES = 20  # safety bound; ~1500 holdings/page covers any UCITS fund
_REQUEST_TIMEOUT_S = 40.0
_RETRIES = 5
# The gateway throttles bursts with a generic HTTP 500. Back off between
# retries, and pause briefly between pages, so a multi-page fund (e.g. the
# ~1900-line FTSE All-World High Dividend) fetches cleanly. Cheap in practice:
# each fund is fetched at most once per cache-TTL.
_BACKOFF_BASE_S = 3.0
_INTER_PAGE_S = 1.5


class VanguardAdapter(IssuerAdapter):
    """Fetch Vanguard UK fund holdings via the investor GraphQL gateway."""

    issuer_id = "vanguard"

    def __init__(self, *, timeout: float = _REQUEST_TIMEOUT_S) -> None:
        """Store the per-request HTTP timeout (seconds)."""
        self._timeout = timeout

    def fetch(self, identity: FundIdentity) -> FundHoldings:  # noqa: D102 (see base)
        port_id = identity.product_id
        if not port_id or not port_id.isdigit():
            raise HoldingsError(
                f"{identity.fund_key}: product_id must be a numeric Vanguard portId",
            )
        raw = self._fetch_all_items(port_id)
        constituents = renormalise_to_unit(
            [self._to_constituent(it) for it in raw if self._weight_of(it) is not None],
        )
        return FundHoldings(
            fund_key=identity.fund_key,
            as_of=datetime.now().date(),  # noqa: DTZ005 — feed has no as-of; informational
            constituents=constituents,
            source=self.issuer_id,
            source_url=_GRAPHQL_URL,
        )

    def _fetch_all_items(self, port_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        with httpx.Client(
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://www.vanguardinvestor.co.uk",
                "Referer": "https://www.vanguardinvestor.co.uk/",
            },
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            for page_no in range(_MAX_PAGES):
                if page_no:
                    time.sleep(_INTER_PAGE_S)
                page = self._post_page(client, port_id, cursor)
                items.extend(page["items"])
                cursor = page["lastItemKey"]
                if not cursor or not page["items"]:
                    break
        if not items:
            raise HoldingsError(f"Vanguard portId {port_id}: no holdings returned")
        return items

    def _post_page(
        self,
        client: httpx.Client,
        port_id: str,
        cursor: str | None,
    ) -> dict[str, Any]:
        body = {
            "operationName": "FundsHoldingsQuery",
            "query": _HOLDINGS_QUERY,
            "variables": {"p": [port_id], "k": cursor},
        }
        last_exc: Exception | None = None
        for attempt in range(_RETRIES):
            try:
                resp = client.post(_GRAPHQL_URL, json=body)
                if resp.status_code == 200:  # noqa: PLR2004
                    payload = resp.json()
                    if "data" in payload and payload["data"].get("borHoldings"):
                        holdings = payload["data"]["borHoldings"][0]["holdings"]
                        return {
                            "items": holdings.get("items") or [],
                            "lastItemKey": holdings.get("lastItemKey"),
                        }
                # The gateway returns a generic 500 under load; retry.
                last_exc = HoldingsError(
                    f"Vanguard GraphQL HTTP {resp.status_code}: {resp.text[:80]}",
                )
            except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                last_exc = exc
            if attempt < _RETRIES - 1:
                logger.debug(
                    "Vanguard page retry %d for portId %s", attempt + 1, port_id,
                )
                time.sleep(_BACKOFF_BASE_S * (attempt + 1))
        raise HoldingsError(f"Vanguard portId {port_id}: {last_exc}")

    @classmethod
    def _to_constituent(cls, item: dict[str, Any]) -> Constituent:
        weight = cls._weight_of(item)
        return Constituent(
            ticker=str(item.get("ticker") or "").strip(),
            name=str(item.get("issuerName") or item.get("securityLongDescription") or "").strip(),
            weight=weight if weight is not None else Decimal(0),
            asset_class=cls._asset_class(str(item.get("securityType") or "")),
        )

    @staticmethod
    def _asset_class(security_type: str) -> str:
        """Map a Vanguard ``securityType`` code to a canonical asset class.

        Vanguard codes equities as ``EQ.*`` and non-equity sleeve lines as
        ``CRNY`` (cash), ``CT.*`` (contracts: swaps, forex, spot) and ``DE.*``
        (derivatives). Translating to ``Cash``/``Derivative`` lets the generic
        roll-up bucket them instead of mis-treating them as equity underliers.
        """
        code = security_type.strip().upper()
        if code.startswith("EQ"):
            return "Equity"
        if code == "CRNY" or code.startswith(("CA", "CASH")):
            return "Cash"
        if code.startswith(("CT", "DE", "FX", "FU", "SW", "FW")):
            return "Derivative"
        return security_type.strip()

    @staticmethod
    def _weight_of(item: dict[str, Any]) -> Decimal | None:
        raw = item.get("marketValuePercentage")
        if raw is None:
            return None
        try:
            return Decimal(str(raw)) / Decimal(100)
        except (InvalidOperation, ValueError):
            return None
