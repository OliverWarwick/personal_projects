"""Portfolio risk metrics derived from yfinance return histories.

At present this module exposes a single function, :func:`compute_var`, which
computes one-period Value-at-Risk (VaR) for a :class:`Portfolio` by historical
simulation: it weights each position by its base-currency market value, pulls
historical adjusted closes from yfinance, builds a portfolio return series,
and reads the chosen quantile off the empirical distribution.

Historical simulation makes no distributional assumption — it just replays
the realised returns of the lookback window with current weights — which is
appropriate for an equity portfolio where the volatility regime is roughly
stable. For longer horizons or fatter-tailed instruments, a parametric or
Monte Carlo method would be better, but neither is implemented here.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pandas as pd
import yfinance as yf  # pyright: ignore[reportMissingTypeStubs]

if TYPE_CHECKING:
    from src.apps.ibkr_sync.portfolio import Portfolio

logger = logging.getLogger(__name__)

_LOOKBACK_BUFFER_DAYS = 7


@dataclass(frozen=True, slots=True)
class PortfolioVaR:
    """Value-at-Risk and expected shortfall for a portfolio.

    Loss values are reported as positive numbers: ``var_amount`` of ``5000``
    means "we expect not to lose more than 5,000 base-currency units over the
    horizon, with the configured confidence". ``var_pct`` is the same quantity
    expressed as a fraction of the portfolio value used in the calculation.

    Attributes:
        as_of: Time the calculation was performed.
        confidence_level: Confidence used for the VaR (e.g., ``0.95``).
        lookback_days: Calendar days of history requested from yfinance.
        horizon_days: VaR horizon in trading days. Daily VaR is scaled by
            ``sqrt(horizon_days)`` (square-root-of-time rule).
        method: Calculation method; currently always ``"historical"``.
        portfolio_value: Sum of base-currency market values for the positions
            that contributed to the calculation (i.e., excluding any with
            missing yfinance data).
        weights: Mapping from IBKR ticker to its share of
            :attr:`portfolio_value`. Sums to 1.
        var_pct: VaR as a fraction of :attr:`portfolio_value`, positive.
        var_amount: VaR in the portfolio's base currency, positive.
        expected_shortfall_pct: Average loss conditional on the loss
            exceeding VaR, as a fraction of :attr:`portfolio_value`. ``None``
            if the tail is empty (very short histories).
        expected_shortfall_amount: Same in base-currency units, ``None`` when
            ``expected_shortfall_pct`` is ``None``.
        observations: Number of daily portfolio-return observations the VaR
            was estimated from.
        missing_tickers: IBKR tickers that were dropped because yfinance
            returned no usable price history for them.

    """

    as_of: datetime
    confidence_level: float
    lookback_days: int
    horizon_days: int
    method: str
    portfolio_value: Decimal
    weights: dict[str, Decimal]
    var_pct: Decimal
    var_amount: Decimal
    expected_shortfall_pct: Decimal | None
    expected_shortfall_amount: Decimal | None
    observations: int
    missing_tickers: list[str]


def compute_var(
    portfolio: Portfolio,
    *,
    confidence_level: float = 0.95,
    lookback_days: int = 365,
    horizon_days: int = 1,
    ticker_map: dict[str, str] | None = None,
) -> PortfolioVaR:
    """Compute historical VaR for ``portfolio``.

    Builds weights from each position's ``market_value_base`` (so the result
    is in the account's base currency), translates IBKR symbols to yfinance
    symbols via ``ticker_map`` when supplied, downloads adjusted close prices
    over a window of ``lookback_days`` calendar days, and returns the quantile
    loss at ``1 - confidence_level`` of the resulting portfolio return series.

    Args:
        portfolio: A :class:`Portfolio` (typically from
            :func:`get_current_portfolio`). Positions without a
            ``market_value_base`` are excluded.
        confidence_level: Confidence for the VaR, in the open interval
            ``(0, 1)``. Defaults to ``0.95``.
        lookback_days: Calendar-day window of price history to pull. Defaults
            to ``365``. Weekends and holidays reduce the number of return
            observations actually used.
        horizon_days: Horizon in trading days; the daily VaR is scaled by
            ``sqrt(horizon_days)``. Defaults to ``1``.
        ticker_map: Optional mapping from IBKR symbol to yfinance symbol, for
            the common case where IBKR's ``VWRP`` is yfinance's ``VWRP.L``.
            Tickers not present in the mapping are passed through unchanged.

    Returns:
        A :class:`PortfolioVaR` summarising the loss estimate, the weights
        used, and any tickers that yfinance had no data for.

    Raises:
        ValueError: If ``confidence_level`` is not in ``(0, 1)``; if
            ``lookback_days`` or ``horizon_days`` is non-positive; if no
            position has a base-currency market value; if yfinance returns
            no usable data for any ticker; or if the resulting return series
            has no overlapping observations.

    """
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be in the open interval (0, 1)")
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")

    raw_weights = _aggregate_weights(portfolio)
    if not raw_weights:
        raise ValueError(
            "Portfolio has no positions with base-currency market value"
        )
    total_value = sum(raw_weights.values(), Decimal(0))
    if total_value == 0:
        raise ValueError("Total base-currency market value of positions is zero")

    yf_by_ibkr = {
        ibkr: (ticker_map or {}).get(ibkr, ibkr) for ibkr in raw_weights
    }

    end = datetime.now(UTC)
    start = end - timedelta(days=lookback_days + _LOOKBACK_BUFFER_DAYS)
    prices = _fetch_close_prices(sorted(set(yf_by_ibkr.values())), start, end)
    if prices.empty:
        raise ValueError(
            f"yfinance returned no price data for tickers: "
            f"{sorted(set(yf_by_ibkr.values()))}"
        )

    returns = prices.pct_change().dropna(how="all")
    available_yf = {
        col for col in returns.columns if returns[col].notna().sum() > 0
    }
    available_ibkr = [
        ibkr for ibkr, yft in yf_by_ibkr.items() if yft in available_yf
    ]
    missing_ibkr = [ibkr for ibkr in raw_weights if ibkr not in available_ibkr]

    if not available_ibkr:
        raise ValueError(
            f"No yfinance return data for any portfolio ticker; missing: {missing_ibkr}"
        )

    available_value = sum((raw_weights[t] for t in available_ibkr), Decimal(0))
    weights = {t: raw_weights[t] / available_value for t in available_ibkr}

    weight_series = pd.Series(
        {yf_by_ibkr[t]: float(weights[t]) for t in available_ibkr},
        dtype=float,
    )
    aligned_returns = returns[list(weight_series.index)]
    portfolio_returns = (aligned_returns * weight_series).sum(axis=1, skipna=False).dropna()
    if portfolio_returns.empty:
        raise ValueError(
            "No overlapping return observations across portfolio tickers"
        )

    alpha = 1.0 - confidence_level
    threshold_1d = float(portfolio_returns.quantile(alpha))
    var_pct_1d = -threshold_1d

    tail = portfolio_returns[portfolio_returns <= threshold_1d]
    es_pct_1d: float | None = -float(tail.mean()) if not tail.empty else None

    scale = math.sqrt(horizon_days)
    var_pct = var_pct_1d * scale
    es_pct = es_pct_1d * scale if es_pct_1d is not None else None

    var_decimal = Decimal(str(var_pct))
    es_decimal = Decimal(str(es_pct)) if es_pct is not None else None

    logger.info(
        "Portfolio VaR computed: confidence=%.3f horizon=%dd value=%s var=%s missing=%s",
        confidence_level,
        horizon_days,
        available_value,
        var_decimal,
        missing_ibkr,
    )

    return PortfolioVaR(
        as_of=end,
        confidence_level=confidence_level,
        lookback_days=lookback_days,
        horizon_days=horizon_days,
        method="historical",
        portfolio_value=available_value,
        weights=weights,
        var_pct=var_decimal,
        var_amount=available_value * var_decimal,
        expected_shortfall_pct=es_decimal,
        expected_shortfall_amount=(
            available_value * es_decimal if es_decimal is not None else None
        ),
        observations=len(portfolio_returns),
        missing_tickers=missing_ibkr,
    )


def _aggregate_weights(portfolio: Portfolio) -> dict[str, Decimal]:
    """Sum base-currency market values per ticker.

    Positions without a ticker or without a ``market_value_base`` are
    skipped. Multiple lots of the same ticker are summed.

    Args:
        portfolio: A :class:`Portfolio`.

    Returns:
        Mapping from ticker to total base-currency market value. May be empty.

    """
    out: dict[str, Decimal] = {}
    for pos in portfolio.positions:
        if not pos.ticker or pos.market_value_base is None:
            continue
        out[pos.ticker] = out.get(pos.ticker, Decimal(0)) + pos.market_value_base
    return out


def _fetch_close_prices(
    tickers: list[str],
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Download adjusted close prices from yfinance.

    Returns a DataFrame indexed by date with one column per ticker. yfinance
    returns slightly different shapes for single- vs multi-ticker requests;
    this helper normalises both into the same layout.

    Args:
        tickers: yfinance ticker symbols.
        start: Inclusive start of the history window.
        end: Exclusive end of the history window.

    Returns:
        A DataFrame of adjusted close prices, or empty when yfinance has no
        data for any ticker.

    """
    if not tickers:
        return pd.DataFrame()

    download: Any = yf.download(  # pyright: ignore[reportUnknownMemberType]
        tickers,
        start=start,
        end=end,
        progress=False,
        auto_adjust=True,
        group_by="column",
    )
    if download is None or not isinstance(download, pd.DataFrame) or download.empty:
        return pd.DataFrame()

    df: pd.DataFrame = download
    if isinstance(df.columns, pd.MultiIndex):
        levels: Any = df.columns.get_level_values(0)
        if "Close" not in levels:
            return pd.DataFrame()
        close: Any = df["Close"]
        return close if isinstance(close, pd.DataFrame) else close.to_frame()

    if "Close" in df.columns and len(tickers) == 1:
        return df[["Close"]].rename(columns={"Close": tickers[0]})
    return df
