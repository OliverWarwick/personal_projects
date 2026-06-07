"""Data models for fund look-through decomposition.

A fund's published holdings file is parsed into a tuple of
:class:`Constituent` rows wrapped in a :class:`FundHoldings` snapshot.
The decomposition layer turns a held fund position into either a
``FundHoldings`` (decomposed) or a recorded reason it could not be
(:class:`DecompositionResult`), so every primary instrument is
accounted for — decomposed or explicitly left in primary space.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date
    from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Constituent:
    """One underlying line within a fund's published holdings.

    Attributes:
        ticker: Issuer-reported ticker (often the local-exchange symbol);
            ``""`` or ``"-"`` when the issuer omits it (cash, forwards).
        name: Issuer security name; the fallback match key when no ticker.
        weight: Fraction of fund NAV in ``[−1, 1]`` (derivatives may be
            negative). Renormalised so the equity sleeve sums to ~1.0.
        asset_class: Issuer asset-class label, e.g. ``"Equity"``,
            ``"Cash and/or Derivatives"`` — used to bucket non-equity rows.
        isin: Constituent ISIN when the issuer publishes it; ``""`` for
            sources that omit it (the iShares UK CSV does).
        currency: Market currency of the constituent line, informational.

    """

    ticker: str
    name: str
    weight: Decimal
    asset_class: str = ""
    isin: str = ""
    currency: str = ""


@dataclass(frozen=True, slots=True)
class FundHoldings:
    """A point-in-time snapshot of a fund's constituents.

    Attributes:
        fund_key: Canonical ``"{issuer}:{product_id}"`` key.
        as_of: Publication date parsed from the source file.
        constituents: Parsed, renormalised constituent rows.
        source: Adapter id that produced this snapshot, e.g. ``"ishares"``.
        source_url: The URL the data was fetched from.

    """

    fund_key: str
    as_of: date
    constituents: tuple[Constituent, ...]
    source: str
    source_url: str = ""
    # When True, ``constituents`` cover only part of the fund (e.g. a top-10
    # disclosure): their weights sum to < 1 and the uncovered remainder is the
    # fund's undisclosed holdings. Full-holdings sources leave this False.
    partial: bool = False


# Reason codes recorded when a position is *not* decomposed and therefore
# remains in primary space. ``passthrough_single_security`` is intentional
# (a single stock/bond is already its own underlier), not a failure.
REASON_NO_IDENTITY = "no_identity"
REASON_NO_ADAPTER = "no_adapter"
REASON_NO_SOURCE = "no_source"
REASON_ADAPTER_ERROR = "adapter_error"
REASON_STALE_NO_DATA = "stale_no_data"
REASON_WEIGHTS_IMPLAUSIBLE = "weights_implausible"
REASON_PASSTHROUGH = "passthrough_single_security"
REASON_PARTIAL_REMAINDER = "partial_remainder"

_REASON_TEXT: dict[str, str] = {
    REASON_NO_IDENTITY: "no fund-identity mapping; source unknown",
    REASON_NO_ADAPTER: "issuer has no holdings adapter yet",
    REASON_NO_SOURCE: "no full-holdings source exists (sources exhausted)",
    REASON_ADAPTER_ERROR: "holdings fetch/parse failed",
    REASON_STALE_NO_DATA: "source returned no data (negative-cached)",
    REASON_WEIGHTS_IMPLAUSIBLE: "published weights did not sum to ~100%",
    REASON_PASSTHROUGH: "single security — already an underlier",
    REASON_PARTIAL_REMAINDER: "fund holdings beyond the disclosed top-10",
}


def reason_text(reason: str) -> str:
    """Return a human-readable explanation for a reason code."""
    return _REASON_TEXT.get(reason, reason)


@dataclass(frozen=True, slots=True)
class DecompositionResult:
    """Outcome of attempting to decompose one fund position.

    Exactly one of ``holdings`` / ``reason`` is meaningful: when
    ``decomposed`` is ``True`` the ``holdings`` are populated; otherwise
    ``reason`` carries a code from this module explaining why the position
    stays in primary space.

    Attributes:
        decomposed: Whether a usable holdings breakdown was obtained.
        holdings: The breakdown when ``decomposed``; ``None`` otherwise.
        reason: A reason code (``""`` when decomposed).

    """

    decomposed: bool
    holdings: FundHoldings | None = None
    reason: str = ""
    diagnostics: dict[str, str] = field(default_factory=dict[str, str])
