"""Realized correlation matrix for the holdings in a portfolio.

Builds a pairwise correlation matrix from the daily returns of each
holding, computed from the on-disk parquet cache of Yahoo Finance close
prices (see :mod:`src.clients.marketdata.cache`). "Realized" here means the
correlation is estimated from observed historical returns over a lookback
window rather than implied or modelled.

Returns are computed in the portfolio's *base* currency, so FX moves are
baked into each series — this reflects the investor's actual realized risk.
Same-currency holdings therefore share the same FX leg, which slightly lifts
their pairwise correlation versus a local-currency estimate; that is the
intended interpretation for a single-base-currency portfolio.

The matrix uses log returns by default (time-additive, the standard basis
for realized covariance) and Pearson correlation over the overlapping
trading days of each pair.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from src.clients.marketdata.cache import CachedYFinanceClient
from src.clients.marketdata.yfinance_client import MarketDataError, ReturnKind

logger = logging.getLogger(__name__)

# Minimum overlapping daily-return observations required before a pair of
# instruments is considered to have a meaningful correlation. Below this the
# estimate is too noisy to trust, so the instrument is dropped entirely.
_DEFAULT_MIN_OVERLAP = 60


@dataclass(frozen=True, slots=True)
class Instrument:
    """A holding to include in the correlation matrix.

    Attributes:
        label: Display label (typically the portfolio ticker).
        yf_symbol: Resolved Yahoo Finance symbol to fetch prices for.
        currency: Instrument (listing) currency, e.g. ``"USD"`` or ``"GBp"``.
            Used to convert into the base currency before computing returns.

    """

    label: str
    yf_symbol: str
    currency: str


@dataclass(frozen=True, slots=True)
class CorrelationResult:
    """The computed correlation matrix and metadata about its inputs.

    Attributes:
        labels: Instrument labels in row/column order of ``matrix``.
        matrix: Square correlation matrix; ``matrix[i][j]`` is the
            correlation of ``labels[i]`` with ``labels[j]``. ``None`` marks
            a pair with too little overlap to estimate.
        observations: Number of return observations per included label.
        start: Earliest return date across all included series.
        end: Latest return date across all included series.
        dropped: Mapping of excluded label → human-readable reason.
        kind: Return kind used (``"simple"`` or ``"log"``).
        base_currency: Currency each series was converted into.

    """

    labels: list[str]
    matrix: list[list[float | None]]
    observations: dict[str, int]
    start: date | None
    end: date | None
    dropped: dict[str, str]
    kind: ReturnKind
    base_currency: str


def compute_correlation(
    instruments: list[Instrument],
    base_currency: str,
    *,
    period: str = "1y",
    kind: ReturnKind = "log",
    min_overlap: int = _DEFAULT_MIN_OVERLAP,
    client: CachedYFinanceClient | None = None,
) -> CorrelationResult:
    """Compute the realized correlation matrix for ``instruments``.

    Each instrument's base-currency close series is fetched from the cache
    (filling any gaps from upstream), converted to daily returns, aligned
    on common trading days, and correlated pairwise.

    Args:
        instruments: Holdings to include. Duplicate labels are de-duplicated,
            keeping the first occurrence.
        base_currency: Portfolio base currency; every series is converted
            into this before returns are taken.
        period: Lookback window relative to today (e.g. ``"6mo"``, ``"1y"``,
            ``"2y"``).
        kind: ``"log"`` for ``ln(P_t / P_{t-1})`` or ``"simple"`` for
            ``P_t / P_{t-1} - 1``.
        min_overlap: Drop any instrument with fewer than this many return
            observations.
        client: Override the market-data client (mainly for testing).

    Returns:
        A :class:`CorrelationResult`. The matrix uses pairwise-complete
        observations, so each cell reflects the days both legs traded.

    """
    yf_client = client or CachedYFinanceClient()
    today = datetime.now().date()  # noqa: DTZ005 — local "today" matches cache windowing
    start = _period_start(period, today)

    series_by_label: dict[str, pd.Series] = {}
    observations: dict[str, int] = {}
    dropped: dict[str, str] = {}
    seen: set[str] = set()

    for inst in instruments:
        if inst.label in seen:
            continue
        seen.add(inst.label)
        try:
            closes = yf_client.get_close_series(
                symbol=inst.yf_symbol,
                start=str(start),
                end=str(today + timedelta(days=2)),
                instrument_currency=inst.currency or base_currency,
                base_currency=base_currency,
            )
        except MarketDataError as exc:
            logger.info("no price data for %s (%s): %s", inst.label, inst.yf_symbol, exc)
            dropped[inst.label] = "no price data"
            continue
        idx: object = closes.index
        if getattr(idx, "tz", None) is not None:
            closes.index = idx.tz_localize(None)  # type: ignore[attr-defined]
        returns = _to_returns(closes, kind)
        if len(returns) < min_overlap:
            dropped[inst.label] = f"only {len(returns)} observations (<{min_overlap})"
            continue
        series_by_label[inst.label] = returns.rename(inst.label)
        observations[inst.label] = len(returns)

    if not series_by_label:
        return CorrelationResult(
            labels=[],
            matrix=[],
            observations={},
            start=None,
            end=None,
            dropped=dropped,
            kind=kind,
            base_currency=base_currency,
        )

    frame = pd.concat(series_by_label.values(), axis=1, join="outer")
    # ``min_periods`` enforces the overlap floor per *pair*; below it the cell
    # is NaN, which we surface as ``None`` rather than a spurious number.
    corr = frame.corr(method="pearson", min_periods=min_overlap)

    labels = list(series_by_label.keys())
    corr = corr.reindex(index=labels, columns=labels)
    matrix: list[list[float | None]] = [
        [None if pd.isna(v) else round(float(v), 4) for v in row] for row in corr.to_numpy()
    ]
    all_dates = frame.dropna(how="all").index
    return CorrelationResult(
        labels=labels,
        matrix=matrix,
        observations=observations,
        start=all_dates.min().date() if len(all_dates) else None,
        end=all_dates.max().date() if len(all_dates) else None,
        dropped=dropped,
        kind=kind,
        base_currency=base_currency,
    )


def _to_returns(closes: pd.Series, kind: ReturnKind) -> pd.Series:
    """Convert a close-price series into daily returns of ``kind``."""
    if kind == "simple":
        returns = closes.pct_change()
    elif kind == "log":
        returns = closes.apply(np.log).diff()
    else:  # pragma: no cover — guarded by the ReturnKind literal
        raise ValueError(f"unknown return kind: {kind!r}")
    return returns.dropna()


def _period_start(period: str, today: date) -> date:
    """Translate a relative ``period`` literal into an absolute start date."""
    days = {
        "1mo": 35,
        "3mo": 100,
        "6mo": 200,
        "1y": 380,
        "2y": 760,
        "5y": 1900,
    }.get(period, 380)
    return today - timedelta(days=days)
