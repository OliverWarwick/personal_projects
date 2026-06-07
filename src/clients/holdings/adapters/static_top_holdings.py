"""Hand-seeded top-holdings adapter for funds with no machine-readable feed.

A few funds (actively-managed OEICs, closed-end investment trusts) publish
their top-10 only in factsheet PDFs/HTML, with no clean API and no Yahoo
fund data. For these, the top holdings are typed directly into the seed
(``top_holdings`` + ``as_of``) and this adapter turns them into a *partial*
:class:`FundHoldings`, exactly like the Yahoo-backed top-10 path.

Because the data is manual it goes stale; the ``as_of`` date is carried
through so the UI can show how old it is and prompt a periodic refresh.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from src.clients.holdings.adapters.base import HoldingsError, IssuerAdapter
from src.clients.holdings.models import Constituent, FundHoldings

if TYPE_CHECKING:
    from src.clients.holdings.identity import FundIdentity

STATIC_TOP_HOLDINGS_SOURCE = "static_top_holdings"


class StaticTopHoldingsAdapter(IssuerAdapter):
    """Build a partial decomposition from hand-seeded top holdings."""

    issuer_id = "static_top_holdings"

    def fetch(self, identity: FundIdentity) -> FundHoldings:  # noqa: D102 (see base)
        if not identity.top_holdings:
            raise HoldingsError(f"{identity.fund_key}: no top_holdings in seed")
        constituents: list[Constituent] = []
        for ticker, name, weight_pct in identity.top_holdings:
            weight = self._to_fraction(weight_pct)
            if weight is None or weight <= 0:
                continue
            constituents.append(
                Constituent(
                    ticker=ticker.strip(),
                    name=(name or ticker).strip(),
                    weight=weight,
                    asset_class="Equity",
                ),
            )
        if not constituents:
            raise HoldingsError(f"{identity.fund_key}: no usable seeded holdings")
        return FundHoldings(
            fund_key=identity.fund_key,
            as_of=self._parse_as_of(identity.as_of),
            constituents=tuple(constituents),
            source=STATIC_TOP_HOLDINGS_SOURCE,
            source_url="seed:fund_identities.yaml",
            partial=True,
        )

    @staticmethod
    def _to_fraction(weight_pct: str) -> Decimal | None:
        try:
            return Decimal(str(weight_pct).strip().rstrip("%")) / Decimal(100)
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _parse_as_of(raw: str) -> date:
        if raw:
            try:
                return date.fromisoformat(raw)
            except ValueError:
                pass
        return datetime.now().date()  # noqa: DTZ005 — fallback only; informational
