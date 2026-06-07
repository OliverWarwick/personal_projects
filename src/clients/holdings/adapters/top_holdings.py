"""Top-10 partial-decomposition adapter backed by Yahoo Finance.

Some funds publish no full constituent file but do disclose their top-10
holdings (and Yahoo surfaces these with weights). For such a fund we
decompose the disclosed portion and leave the rest as an explicit
"undisclosed remainder" — a *partial* :class:`FundHoldings` whose weights
sum to less than one.

Reuses the ``yfinance`` dependency already used for prices. Fetches run
behind the holdings cache, so Yahoo is hit at most once per fund per TTL.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

import pandas as pd

from src.clients.holdings.adapters.base import HoldingsError, IssuerAdapter
from src.clients.holdings.models import Constituent, FundHoldings

if TYPE_CHECKING:
    from src.clients.holdings.identity import FundIdentity

logger = logging.getLogger(__name__)

TOP_HOLDINGS_SOURCE = "top_holdings"
# Reject as implausible if the disclosed weights somehow exceed this (a full
# file mis-tagged as partial, or bad data) — a real top-10 sums well below 1.
_MAX_DISCLOSED = Decimal("1.02")


class TopHoldingsAdapter(IssuerAdapter):
    """Fetch a fund's disclosed top holdings via Yahoo Finance."""

    issuer_id = "top_holdings"

    def fetch(self, identity: FundIdentity) -> FundHoldings:  # noqa: D102 (see base)
        symbol = identity.yahoo_symbol
        if not symbol:
            raise HoldingsError(f"{identity.fund_key}: no yahoo_symbol in seed")
        frame = self._fetch_top_holdings(symbol)
        constituents: list[Constituent] = []
        disclosed = Decimal(0)
        for sym, row in frame.iterrows():
            weight = self._to_weight(row.get("Holding Percent"))
            if weight is None or weight <= 0:
                continue
            disclosed += weight
            name = row.get("Name")
            constituents.append(
                Constituent(
                    ticker=str(sym).strip(),
                    name=str(name if name is not None else sym).strip(),
                    weight=weight,
                    asset_class="Equity",
                ),
            )
        if not constituents:
            raise HoldingsError(f"{symbol}: no top-holdings rows from Yahoo")
        if disclosed > _MAX_DISCLOSED:
            raise HoldingsError(
                f"{symbol}: disclosed weights sum to {disclosed:.3f} (>1)",
            )
        return FundHoldings(
            fund_key=identity.fund_key,
            as_of=datetime.now().date(),  # noqa: DTZ005 — Yahoo gives no as-of
            constituents=tuple(constituents),
            source=TOP_HOLDINGS_SOURCE,
            source_url=f"yfinance:{symbol}",
            partial=True,
        )

    @staticmethod
    def _fetch_top_holdings(symbol: str) -> pd.DataFrame:
        # yfinance is a heavy optional import kept local; it ships no type stubs.
        import yfinance as yf  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

        try:
            raw = yf.Ticker(symbol).funds_data.top_holdings  # pyright: ignore[reportUnknownMemberType]
            frame = pd.DataFrame(raw)
        except Exception as exc:
            raise HoldingsError(f"Yahoo top-holdings failed for {symbol}: {exc}") from exc
        if frame.empty or "Holding Percent" not in frame.columns:
            raise HoldingsError(f"{symbol}: Yahoo returned no top-holdings table")
        return frame

    @staticmethod
    def _to_weight(value: object) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
