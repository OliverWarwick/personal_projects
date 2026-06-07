"""Issuer-adapter abstraction for fetching published fund holdings.

Each issuer publishes constituent files differently, so one adapter per
issuer encapsulates the URL/format quirks behind a single
:meth:`IssuerAdapter.fetch` call returning normalised
:class:`~src.clients.holdings.models.FundHoldings`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import TYPE_CHECKING

from src.clients.holdings.models import Constituent

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.clients.holdings.identity import FundIdentity
    from src.clients.holdings.models import FundHoldings

# Published weights are percentages; the equity sleeve plus any small
# cash/derivative offsets should sum to ~100%. Outside this band the file is
# treated as unparseable rather than silently mis-weighting the portfolio.
_WEIGHT_SUM_LO = Decimal("0.95")
_WEIGHT_SUM_HI = Decimal("1.05")


class HoldingsError(Exception):
    """Raised when an adapter cannot obtain usable holdings for a fund."""


def renormalise_to_unit(
    constituents: Sequence[Constituent],
    *,
    lo: Decimal = _WEIGHT_SUM_LO,
    hi: Decimal = _WEIGHT_SUM_HI,
) -> tuple[Constituent, ...]:
    """Validate that weights sum to ~1.0 and scale them to sum exactly 1.0.

    Conserving fund value on roll-up requires the constituent weights to sum
    to one. Issuers publish weights that sum to ~100% (plus rounding and
    cash/derivative offsets); this rescales them and rejects files whose sum
    falls outside ``[lo, hi]`` as implausible.

    Args:
        constituents: Parsed constituents with raw fractional weights.
        lo: Lower bound on the acceptable pre-normalisation weight sum.
        hi: Upper bound on the acceptable pre-normalisation weight sum.

    Returns:
        Constituents with weights scaled to sum to exactly 1.0.

    Raises:
        HoldingsError: If there are no constituents or the weight sum is
            outside ``[lo, hi]``.

    """
    if not constituents:
        raise HoldingsError("no parseable holdings rows")
    weight_sum = sum((c.weight for c in constituents), Decimal(0))
    if not lo <= weight_sum <= hi:
        raise HoldingsError(
            f"weights implausible: sum={weight_sum:.4f} (expected ~1.0)",
        )
    scale = Decimal(1) / weight_sum
    return tuple(
        Constituent(
            ticker=c.ticker,
            name=c.name,
            weight=c.weight * scale,
            asset_class=c.asset_class,
            isin=c.isin,
            currency=c.currency,
        )
        for c in constituents
    )


class IssuerAdapter(ABC):
    """Base class for issuer holdings adapters.

    Subclasses set :attr:`issuer_id` to the value used in the fund-identity
    seed and implement :meth:`fetch`.
    """

    issuer_id: str = ""

    @abstractmethod
    def fetch(self, identity: FundIdentity) -> FundHoldings:
        """Fetch and parse the fund's published holdings.

        Args:
            identity: The resolved fund identity (carries the source URL).

        Returns:
            A normalised :class:`FundHoldings` snapshot.

        Raises:
            HoldingsError: If the holdings cannot be fetched or parsed, or
                the published weights are implausible.

        """
        raise NotImplementedError
