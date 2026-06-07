"""Re-express a primary-instrument portfolio in underlier space.

Given the aggregate :class:`~src.apps.finances_dashboard.run.PositionRow`
list, each fund/ETF is decomposed into its constituents (first level only)
and rolled up — value-weighted — across every fund and direct holding into
a single underlier view. A security held directly *and* inside one or more
funds collapses into one row whose value is the sum of all contributions.

The hard rule: every input position yields at least one output row. A fund
we cannot decompose (no identity, no adapter, fetch failed) is emitted as
itself — a *residual* row — with the reason recorded, so nothing is dropped
or silently zero-weighted. Conservation holds: the sum of underlier values
equals the sum of primary position values.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from src.clients.holdings import build_default_client, load_fund_identities
from src.clients.holdings.adapters.index_proxy import PROXY_SOURCE
from src.clients.holdings.identity import resolve_identity
from src.clients.holdings.models import (
    REASON_NO_IDENTITY,
    REASON_PARTIAL_REMAINDER,
    REASON_PASSTHROUGH,
)

if TYPE_CHECKING:
    from src.apps.finances_dashboard.run import PositionRow
    from src.clients.holdings.cache import CachedHoldingsClient
    from src.clients.holdings.identity import FundIdentity

# Synthetic roll-up key for cash / derivative constituent lines so their
# (often small, sometimes negative) weight is preserved rather than dropped.
_CASH_DERIV_KEY = "__CASH_DERIV__"
_CASH_DERIV_TOKENS = ("CASH", "DERIVATIVE", "FORWARD", "FUTURE", "SWAP")
# Tokens that mark a primary position as fund-like, so that when it has no
# identity mapping we flag it ``no_identity`` (worth an adapter) rather than
# treating it as an already-atomic single security.
_FUND_TOKENS = ("ETF", "UCITS", "OEIC", "ICVC", "INDEX", "FUND")
_FUND_SEC_TYPES = {"ETF", "FUND"}


@dataclass(frozen=True, slots=True)
class UnderlierRow:
    """One rolled-up line in the underlier view.

    Attributes:
        key: Roll-up match key (ISIN, normalised ticker, or normalised name).
        name: Display name.
        isin: Underlier ISIN when known; ``""`` otherwise.
        value_base: Aggregated market value in base currency.
        weight_pct: Share of the decomposed portfolio (0-100).
        match_quality: How the row was keyed: ``isin`` / ``ticker`` /
            ``name`` / ``passthrough`` (direct single security) /
            ``residual`` (undecomposable fund) / ``cash``.
        contributors: ``(source_label, value_base)`` pairs that fed this row,
            e.g. the funds (and direct holding) contributing a security.
        is_residual: Whether this row is an undecomposed primary instrument.
        reason: Reason code when ``is_residual`` (empty otherwise).
        from_proxy: Whether any of this row's value came from an index-proxy
            decomposition (approximate, not the fund's real holdings).
        from_partial: Whether any of this row's value came from a top-10
            partial decomposition.
        ticker: Display ticker symbol of the underlier (e.g. ``GOOGL``).
        value_exact: Value sourced from *replicated* holdings — full fund
            holdings plus directly-held positions (the parts we hold for real).
        value_approx: Value sourced from *estimated* holdings — index proxies
            and disclosed top-10s (the parts we infer, not hold exactly).

    """

    key: str
    name: str
    isin: str
    value_base: Decimal
    weight_pct: Decimal
    match_quality: str
    contributors: tuple[tuple[str, Decimal], ...]
    is_residual: bool
    reason: str
    from_proxy: bool = False
    from_partial: bool = False
    ticker: str = ""
    value_exact: Decimal = Decimal(0)
    value_approx: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class ProvenanceNote:
    """Per-fund record of whether (and how) it was decomposed.

    Drives the coverage summary above the underlier table. Only emitted for
    fund-like positions — single securities are not counted as funds.
    """

    ticker: str
    name: str
    value_base: Decimal
    decomposed: bool
    reason: str
    source: str = ""
    proxy: bool = False
    proxy_note: str = ""
    partial: bool = False
    coverage: Decimal = Decimal(0)
    as_of: str = ""


@dataclass
class _Accum:
    """Mutable accumulator for one roll-up key."""

    name: str
    isin: str
    value: Decimal
    match_quality: str
    is_residual: bool
    reason: str
    contributors: dict[str, Decimal]
    ticker: str = ""
    value_exact: Decimal = Decimal(0)
    value_approx: Decimal = Decimal(0)
    has_proxy: bool = False
    has_partial: bool = False


def _norm_ticker(ticker: str) -> str:
    """Normalise a ticker for matching: uppercase, strip non-alphanumerics.

    Folds ``"BRK B"`` and ``"BRKB"`` (and ``"BRK.B"``) onto one key so a
    directly-held line matches the same company inside a fund.
    """
    return "".join(ch for ch in ticker.upper() if ch.isalnum())


def _norm_name(name: str) -> str:
    """Normalise a security name as a last-resort match key."""
    drop = {
        "INC",
        "PLC",
        "CORP",
        "CORPORATION",
        "LTD",
        "LIMITED",
        "CO",
        "THE",
        "NV",
        "SA",
        "AG",
        "CLASS",
    }
    tokens = [
        t
        for t in "".join(c if c.isalnum() or c == " " else " " for c in name.upper()).split()
        if t not in drop
    ]
    return " ".join(tokens)


def _is_cash_or_derivative(asset_class: str) -> bool:
    upper = asset_class.upper()
    return any(tok in upper for tok in _CASH_DERIV_TOKENS)


def _looks_like_fund(position: PositionRow) -> bool:
    if position.sec_type.upper() in _FUND_SEC_TYPES:
        return True
    haystack = f"{position.description} {position.ticker}".upper()
    return any(tok in haystack for tok in _FUND_TOKENS)


def _constituent_key(isin: str, ticker: str, name: str) -> tuple[str, str]:
    """Return ``(key, match_quality)`` for a constituent / direct holding.

    Ticker is the *primary* key, not ISIN: our fund-holdings sources
    (iShares/Vanguard CSVs, Yahoo, hand-seeded) publish tickers but rarely
    ISINs, so a directly-held stock (which does carry an ISIN) only merges
    with the same name inside a fund when both are keyed on ticker. ISIN is a
    fallback for the few rows that have no ticker (e.g. some cash lines).
    """
    nt = _norm_ticker(ticker)
    if nt:
        return nt, "ticker"
    if isin:
        return isin.upper(), "isin"
    return f"name:{_norm_name(name)}", "name"


def decompose_to_underliers(  # noqa: PLR0915
    positions: list[PositionRow],
    client: CachedHoldingsClient,
    identities: dict[str, FundIdentity],
) -> tuple[list[UnderlierRow], list[ProvenanceNote]]:
    """Roll a primary-instrument portfolio up into underlier space.

    Args:
        positions: Aggregate position rows (cross-account sums already done).
        client: Holdings client used to fetch fund constituents.
        identities: Alias -> :class:`FundIdentity` lookup.

    Returns:
        ``(underliers, notes)`` — the rolled-up underlier rows sorted by
        descending value, and per-fund provenance notes for the summary.

    """
    accums: dict[str, _Accum] = {}
    notes: list[ProvenanceNote] = []

    def _add(
        key: str,
        *,
        name: str,
        isin: str,
        value: Decimal,
        match_quality: str,
        source_label: str,
        display_ticker: str = "",
        is_residual: bool = False,
        reason: str = "",
        proxy: bool = False,
        partial: bool = False,
    ) -> None:
        # Split the contribution: replicated (real holdings — full fund holdings
        # or direct positions) vs estimated (proxy / top-10). Residual buckets
        # are neither (we could not look through them).
        exact = value if not is_residual and not (proxy or partial) else Decimal(0)
        approx = value if not is_residual and (proxy or partial) else Decimal(0)
        acc = accums.get(key)
        if acc is None:
            accums[key] = _Accum(
                name=name,
                isin=isin,
                value=value,
                match_quality=match_quality,
                is_residual=is_residual,
                reason=reason,
                contributors={source_label: value},
                ticker=display_ticker,
                value_exact=exact,
                value_approx=approx,
                has_proxy=proxy,
                has_partial=partial,
            )
            return
        acc.value += value
        acc.value_exact += exact
        acc.value_approx += approx
        acc.contributors[source_label] = acc.contributors.get(source_label, Decimal(0)) + value
        if not acc.isin and isin:
            acc.isin = isin
        if not acc.ticker and display_ticker:
            acc.ticker = display_ticker
        acc.has_proxy = acc.has_proxy or proxy
        acc.has_partial = acc.has_partial or partial

    for p in positions:
        identity = resolve_identity(identities, p.isin, p.ticker)
        result = client.get_holdings(identity) if identity is not None else None

        if (
            identity is not None
            and result is not None
            and result.decomposed
            and result.holdings is not None
        ):
            holdings = result.holdings
            is_proxy = holdings.source == PROXY_SOURCE
            is_partial = holdings.partial
            covered = Decimal(0)
            for c in holdings.constituents:
                value = p.value_base * c.weight
                covered += c.weight
                if _is_cash_or_derivative(c.asset_class):
                    _add(
                        _CASH_DERIV_KEY,
                        name="Cash & derivatives",
                        isin="",
                        value=value,
                        match_quality="cash",
                        source_label=p.ticker,
                        proxy=is_proxy,
                        partial=is_partial,
                    )
                    continue
                key, mq = _constituent_key(c.isin, c.ticker, c.name)
                _add(
                    key,
                    name=c.name or c.ticker,
                    isin=c.isin,
                    value=value,
                    match_quality=mq,
                    source_label=p.ticker,
                    display_ticker=c.ticker,
                    proxy=is_proxy,
                    partial=is_partial,
                )
            # Partial sources (top-10) cover only part of the fund; route the
            # undisclosed remainder to a residual bucket keyed to the fund so
            # value is conserved and the gap is visible rather than hidden.
            if is_partial and covered < 1:
                remainder = p.value_base * (Decimal(1) - covered)
                rkey, _ = _constituent_key(p.isin, p.ticker, p.description)
                _add(
                    f"remainder:{rkey}",
                    name=f"{p.description or p.ticker} — undisclosed holdings",
                    isin="",
                    value=remainder,
                    match_quality="residual",
                    source_label=p.ticker,
                    is_residual=True,
                    reason=REASON_PARTIAL_REMAINDER,
                )
            notes.append(
                ProvenanceNote(
                    ticker=p.ticker,
                    name=identity.name or p.description,
                    value_base=p.value_base,
                    decomposed=True,
                    reason="",
                    source=holdings.source,
                    proxy=is_proxy,
                    proxy_note=identity.proxy_note,
                    partial=is_partial,
                    coverage=covered,
                    as_of=holdings.as_of.isoformat() if is_partial else "",
                ),
            )
            continue

        # Not decomposed — keep this position in primary space as its own row.
        fund_like = identity is not None or _looks_like_fund(p)
        if result is not None:
            reason = result.reason
        elif fund_like:
            reason = REASON_NO_IDENTITY
        else:
            reason = REASON_PASSTHROUGH
        key, mq = _constituent_key(p.isin, p.ticker, p.description)
        _add(
            key,
            name=p.description or p.ticker,
            isin=p.isin,
            value=p.value_base,
            match_quality="residual" if fund_like else "passthrough",
            source_label=p.ticker,
            display_ticker=p.ticker,
            is_residual=fund_like,
            reason=reason if fund_like else "",
        )
        if fund_like:
            notes.append(
                ProvenanceNote(
                    ticker=p.ticker,
                    name=p.description or p.ticker,
                    value_base=p.value_base,
                    decomposed=False,
                    reason=reason,
                ),
            )

    total = sum((a.value for a in accums.values()), Decimal(0))
    rows = [
        UnderlierRow(
            key=key,
            name=acc.name,
            isin=acc.isin,
            value_base=acc.value,
            weight_pct=(acc.value / total * Decimal(100)) if total else Decimal(0),
            match_quality=acc.match_quality,
            contributors=tuple(
                sorted(acc.contributors.items(), key=lambda kv: kv[1], reverse=True),
            ),
            is_residual=acc.is_residual,
            reason=acc.reason,
            from_proxy=acc.has_proxy,
            from_partial=acc.has_partial,
            ticker=acc.ticker,
            value_exact=acc.value_exact,
            value_approx=acc.value_approx,
        )
        for key, acc in accums.items()
    ]
    rows.sort(key=lambda r: r.value_base, reverse=True)
    return rows, notes


def decompose_portfolio(
    portfolio: list[PositionRow],
    *,
    client: CachedHoldingsClient | None = None,
    identities: dict[str, FundIdentity] | None = None,
) -> tuple[list[UnderlierRow], list[ProvenanceNote]]:
    """Decompose a whole portfolio into underlier exposures, ranked by exposure.

    This is the single entry point: hand it a portfolio (the list of open
    positions, per-account or already aggregated) and it rolls same-ticker
    holdings together, looks every fund through to its constituents, merges a
    security held directly and via funds into one line, and returns the
    underlier rows **sorted by descending exposure** plus per-fund provenance.

    A default holdings client and identity seed are built when not supplied;
    the web app passes long-lived shared instances so the on-disk cache is
    reused across requests.

    Args:
        portfolio: Open positions to decompose.
        client: Optional shared holdings client (built if omitted).
        identities: Optional shared identity seed (loaded if omitted).

    Returns:
        ``(exposures, notes)`` — underlier rows ranked by exposure, and the
        per-fund provenance notes describing how each fund was decomposed.

    """
    # Local import avoids a module-level cycle (run.py imports this module);
    # reusing its ticker-aggregation keeps a single source of truth.
    from src.apps.finances_dashboard.run import (  # noqa: PLC0415
        _aggregate_position_rows,  # pyright: ignore[reportPrivateUsage]
    )

    holdings_client = client or build_default_client()
    fund_identities = identities if identities is not None else load_fund_identities()
    aggregated = _aggregate_position_rows(portfolio)  # pyright: ignore[reportPrivateUsage]
    return decompose_to_underliers(aggregated, holdings_client, fund_identities)
