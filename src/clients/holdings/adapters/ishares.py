"""iShares (BlackRock) holdings adapter.

iShares publishes a full daily constituent CSV per UCITS product via an
``.ajax`` endpoint on the UK product page. The file has two preamble rows
(an "as of" date and a blank line) followed by a header and one row per
holding. The UK CSV does **not** carry ISINs, so constituents are matched
downstream by ticker (and name as a fallback).

The endpoint 403s for non-browser user agents, so requests send a desktop
browser UA. Fetches are expected to run behind the holdings cache, so the
live dashboard never blocks on iShares.
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

import httpx
import pandas as pd

from src.clients.holdings.adapters.base import (
    HoldingsError,
    IssuerAdapter,
    renormalise_to_unit,
)
from src.clients.holdings.models import Constituent, FundHoldings

if TYPE_CHECKING:
    from src.clients.holdings.identity import FundIdentity

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_HEADER_MARKER = "Weight (%)"
_AS_OF_MARKER = "Fund Holdings as of"
_REQUEST_TIMEOUT_S = 30.0


class ISharesAdapter(IssuerAdapter):
    """Fetch and parse iShares UCITS holdings CSVs."""

    issuer_id = "ishares"

    def __init__(self, *, timeout: float = _REQUEST_TIMEOUT_S) -> None:
        """Store the per-request HTTP timeout (seconds)."""
        self._timeout = timeout

    def fetch(self, identity: FundIdentity) -> FundHoldings:  # noqa: D102 (see base)
        url = identity.holdings_url
        if not url:
            raise HoldingsError(f"{identity.fund_key}: no holdings_url in seed")
        text = self._download(url)
        as_of, constituents = self._parse(text)
        return FundHoldings(
            fund_key=identity.fund_key,
            as_of=as_of,
            constituents=constituents,
            source=self.issuer_id,
            source_url=url,
        )

    def _download(self, url: str) -> str:
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": _BROWSER_UA, "Accept": "text/csv,*/*"},
                timeout=self._timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HoldingsError(f"iShares fetch failed for {url}: {exc}") from exc
        return resp.text

    @staticmethod
    def _parse(text: str) -> tuple[date, tuple[Constituent, ...]]:
        lines = text.splitlines()
        header_idx: int | None = None
        as_of: date | None = None
        for i, line in enumerate(lines):
            stripped = line.lstrip("﻿")  # drop any UTF-8 BOM on the first row
            if as_of is None and stripped.startswith(_AS_OF_MARKER):
                as_of = ISharesAdapter._parse_as_of(stripped)
            if _HEADER_MARKER in line:
                header_idx = i
                break
        if header_idx is None:
            raise HoldingsError("iShares CSV: header row not found")

        frame = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])))
        required = {"Ticker", "Name", "Weight (%)", "Asset Class"}
        missing = required - set(frame.columns)
        if missing:
            raise HoldingsError(f"iShares CSV missing columns: {sorted(missing)}")

        constituents: list[Constituent] = []
        for _, row in frame.iterrows():
            weight = ISharesAdapter._to_weight(row["Weight (%)"])
            if weight is None:
                continue  # disclaimer / blank trailing rows
            ticker = ISharesAdapter._clean(row["Ticker"])
            constituents.append(
                Constituent(
                    ticker="" if ticker == "-" else ticker,
                    name=ISharesAdapter._clean(row["Name"]),
                    weight=weight,
                    asset_class=ISharesAdapter._clean(row.get("Asset Class", "")),
                    currency=ISharesAdapter._clean(row.get("Market Currency", "")),
                ),
            )
        normalised = renormalise_to_unit(constituents)
        if as_of is None:
            as_of = datetime.now().date()  # noqa: DTZ005 — informational only
        return as_of, normalised

    @staticmethod
    def _parse_as_of(line: str) -> date | None:
        # e.g. 'Fund Holdings as of,"04/Jun/2026"'
        raw = line.split(",", 1)[-1].strip().strip('"')
        for fmt in ("%d/%b/%Y", "%b %d, %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).date()  # noqa: DTZ007
            except ValueError:
                continue
        logger.warning("iShares: could not parse 'as of' date %r", raw)
        return None

    @staticmethod
    def _clean(value: object) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        return str(value).strip()

    @staticmethod
    def _to_weight(value: object) -> Decimal | None:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        try:
            return Decimal(str(value).replace(",", "").strip()) / Decimal(100)
        except (InvalidOperation, ValueError):
            return None
