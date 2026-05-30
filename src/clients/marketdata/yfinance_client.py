"""Yahoo Finance market data client.

Wraps the ``yfinance`` library to provide a small, typed surface for
fetching historical OHLCV bars and computing simple/log return series for
a given security. The client is synchronous and stateless; it holds no
authentication and exists mostly to keep ``yfinance`` quirks (multi-index
columns when downloading multiple tickers, timezone-aware indices,
exchange suffix conventions) out of caller code.

Symbols may be specified two ways:

* As a fully-qualified Yahoo Finance ticker, e.g. ``"AAPL"`` or
  ``"VOD.L"``, via the ``symbol`` argument.
* As a plain ticker plus an ``exchange`` code (ISO 10383 MIC, common
  short code, or country code), in which case the client resolves the
  Yahoo suffix automatically.

Typical usage:
    client = YFinanceClient()
    aapl = client.get_daily_returns("AAPL", exchange="NASDAQ", period="1y")
    vod = client.get_daily_returns("VOD", exchange="LSE", period="1y")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd
import yfinance as yf  # pyright: ignore[reportMissingTypeStubs]

if TYPE_CHECKING:
    from datetime import date, datetime

logger = logging.getLogger(__name__)

_MIN_OBSERVATIONS_FOR_RETURNS = 2

Period = Literal[
    "1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"
]
Interval = Literal[
    "1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"
]
ReturnKind = Literal["simple", "log"]
PriceColumn = Literal["Open", "High", "Low", "Close", "Adj Close", "Volume"]


# Mapping from common exchange identifiers to the Yahoo Finance ticker
# suffix. Keys are upper-cased on lookup so callers can use any case.
# Sources include the published Yahoo Finance exchange list; we cover the
# venues most likely to appear in a personal portfolio. US exchanges
# (NYSE, NASDAQ, AMEX, ARCA, BATS) take no suffix.
_EXCHANGE_SUFFIX: dict[str, str] = {
    # United States — no suffix
    "US": "",
    "USA": "",
    "NYSE": "",
    "NASDAQ": "",
    "NASDAQGS": "",
    "NASDAQGM": "",
    "NASDAQCM": "",
    "AMEX": "",
    "NYSEARCA": "",
    "ARCA": "",
    "BATS": "",
    "XNYS": "",
    "XNAS": "",
    "XASE": "",
    # United Kingdom
    "LSE": ".L",
    "LON": ".L",
    "XLON": ".L",
    "GB": ".L",
    "UK": ".L",
    "IL": ".IL",  # London International Order Book
    # Ireland
    "ISE": ".IR",
    "XDUB": ".IR",
    # Germany
    "XETRA": ".DE",
    "XETR": ".DE",
    "DE": ".DE",
    "GER": ".DE",
    "FRA": ".F",
    "XFRA": ".F",
    "BER": ".BE",
    "MUN": ".MU",
    "HAM": ".HM",
    "STU": ".SG",
    "DUS": ".DU",
    # France
    "EPA": ".PA",
    "XPAR": ".PA",
    "FR": ".PA",
    # Netherlands
    "AMS": ".AS",
    "XAMS": ".AS",
    "NL": ".AS",
    # Belgium
    "BRU": ".BR",
    "XBRU": ".BR",
    "BE": ".BR",
    # Spain
    "BME": ".MC",
    "XMAD": ".MC",
    "ES": ".MC",
    # Italy
    "MIL": ".MI",
    "XMIL": ".MI",
    "IT": ".MI",
    # Portugal
    "LIS": ".LS",
    "PT": ".LS",
    # Switzerland
    "SIX": ".SW",
    "SWX": ".SW",
    "XSWX": ".SW",
    "CH": ".SW",
    # Nordics
    "STO": ".ST",
    "XSTO": ".ST",
    "SE": ".ST",
    "HEL": ".HE",
    "XHEL": ".HE",
    "FI": ".HE",
    "CPH": ".CO",
    "XCSE": ".CO",
    "DK": ".CO",
    "OSL": ".OL",
    "XOSL": ".OL",
    "NO": ".OL",
    "ICE": ".IC",
    "IS": ".IC",
    # Austria
    "WBO": ".VI",
    "XWBO": ".VI",
    "AT": ".VI",
    # Czech, Hungary, Russia
    "PRG": ".PR",
    "CZ": ".PR",
    "BUD": ".BD",
    "HU": ".BD",
    "MCX": ".ME",
    "RU": ".ME",
    # Greece
    "ATH": ".AT",
    "GR": ".AT",
    # Turkey
    "BIST": ".IS",
    "TR": ".IS",
    # Israel
    "TASE": ".TA",
    "TLV": ".TA",
    # South Africa
    "JSE": ".JO",
    "XJSE": ".JO",
    "ZA": ".JO",
    # Canada
    "TSX": ".TO",
    "XTSE": ".TO",
    "TSXV": ".V",
    "XTSX": ".V",
    "CSE": ".CN",
    "NEO": ".NE",
    "CA": ".TO",
    # Mexico
    "BMV": ".MX",
    "XMEX": ".MX",
    "MX": ".MX",
    # Brazil
    "BVMF": ".SA",
    "B3": ".SA",
    "BR": ".SA",
    # Argentina, Chile
    "BCBA": ".BA",
    "AR": ".BA",
    "SGO": ".SN",
    "CL": ".SN",
    # Australia & New Zealand
    "ASX": ".AX",
    "XASX": ".AX",
    "AU": ".AX",
    "NZX": ".NZ",
    "NZ": ".NZ",
    # Asia — Japan
    "TSE": ".T",
    "TYO": ".T",
    "XTKS": ".T",
    "JP": ".T",
    # Asia — Hong Kong & China
    "HKEX": ".HK",
    "HKG": ".HK",
    "XHKG": ".HK",
    "HK": ".HK",
    "SSE": ".SS",
    "XSHG": ".SS",
    "SZSE": ".SZ",
    "XSHE": ".SZ",
    "CN": ".SS",
    # Asia — Korea
    "KRX": ".KS",
    "KOSPI": ".KS",
    "KOSDAQ": ".KQ",
    "KR": ".KS",
    # Asia — Taiwan
    "TWSE": ".TW",
    "XTAI": ".TW",
    "TPEX": ".TWO",
    "TW": ".TW",
    # Asia — Singapore, Malaysia, Thailand, Indonesia, Philippines, Vietnam
    "SGX": ".SI",
    "XSES": ".SI",
    "SG": ".SI",
    "KLSE": ".KL",
    "XKLS": ".KL",
    "MY": ".KL",
    "SET": ".BK",
    "XBKK": ".BK",
    "TH": ".BK",
    "IDX": ".JK",
    "XIDX": ".JK",
    "ID": ".JK",
    "PSE": ".PS",
    "PH": ".PS",
    "HSX": ".VN",
    "VN": ".VN",
    # Asia — India
    "NSE": ".NS",
    "XNSE": ".NS",
    "BSE": ".BO",
    "XBOM": ".BO",
    "IN": ".NS",
    # Middle East
    "TADAWUL": ".SR",
    "XSAU": ".SR",
    "SA": ".SR",
    "DFM": ".AE",
    "AE": ".AE",
    "QSE": ".QA",
    "QA": ".QA",
    "EGX": ".CA",
    "EG": ".CA",
    # Currency / crypto helpers
    "FX": "=X",
    "CRYPTO": "-USD",
}


class MarketDataError(RuntimeError):
    """Raised when market data cannot be fetched or is empty."""


def resolve_symbol(ticker: str, exchange: str | None = None) -> str:
    """Build a Yahoo Finance ticker from a plain ticker plus exchange.

    Args:
        ticker: The local ticker symbol as quoted on the exchange. May
            already include a Yahoo suffix (e.g. ``"VOD.L"``); in that
            case it is returned unchanged regardless of ``exchange``.
        exchange: Optional exchange identifier. Accepts common short
            codes (``"LSE"``, ``"NASDAQ"``, ``"TSE"``), ISO 10383 MIC
            codes (``"XLON"``, ``"XNAS"``), or two-letter country codes
            (``"GB"``, ``"DE"``). Case-insensitive. ``None`` or US
            exchanges yield no suffix.

    Returns:
        A symbol suitable for passing to ``yfinance``.

    Raises:
        MarketDataError: If ``exchange`` is non-empty but unrecognised.

    """
    if not ticker:
        raise MarketDataError("ticker must be a non-empty string")
    if "." in ticker or "=" in ticker or "-" in ticker:
        return ticker
    if exchange is None or exchange == "":
        return ticker
    key = exchange.strip().upper()
    if key not in _EXCHANGE_SUFFIX:
        raise MarketDataError(
            f"unknown exchange {exchange!r}; "
            f"add it to _EXCHANGE_SUFFIX or pass a fully-qualified symbol",
        )
    suffix = _EXCHANGE_SUFFIX[key]
    return f"{ticker}{suffix}"


class YFinanceClient:
    """Synchronous Yahoo Finance market data client.

    Provides typed accessors for historical OHLCV bars and derived return
    series. All methods accept either a fully-qualified Yahoo symbol via
    ``symbol`` or a plain ``ticker`` plus ``exchange`` (resolved via
    :func:`resolve_symbol`). Methods raise :class:`MarketDataError` when
    the upstream response is empty so callers can rely on a non-empty
    series on success.
    """

    def __init__(self, *, auto_adjust: bool = True) -> None:
        """Initialise the client.

        Args:
            auto_adjust: When ``True`` (the default), prices are adjusted
                for splits and dividends, so the ``Close`` column reflects
                total return. Set to ``False`` to receive raw closes plus
                a separate ``Adj Close`` column.

        """
        self.auto_adjust = auto_adjust

    @staticmethod
    def resolve_args(symbol: str | None, ticker: str | None, exchange: str | None) -> str:
        """Return the Yahoo symbol from either ``symbol`` or ``ticker``+``exchange``.

        Args:
            symbol: Fully-qualified Yahoo Finance ticker, or ``None``.
            ticker: Plain local ticker, used when ``symbol`` is ``None``.
            exchange: Exchange identifier, used with ``ticker``.

        Returns:
            The resolved Yahoo symbol.

        Raises:
            MarketDataError: If neither ``symbol`` nor ``ticker`` is given,
                or if both are given.

        """
        if symbol and ticker:
            raise MarketDataError(
                "pass either symbol or ticker, not both",
            )
        if symbol:
            return symbol
        if not ticker:
            raise MarketDataError("must supply symbol or ticker")
        return resolve_symbol(ticker, exchange)

    def get_history(
        self,
        symbol: str | None = None,
        *,
        ticker: str | None = None,
        exchange: str | None = None,
        period: Period | None = "1y",
        start: str | date | datetime | None = None,
        end: str | date | datetime | None = None,
        interval: Interval = "1d",
        instrument_currency: str | None = None,
        base_currency: str | None = None,
    ) -> pd.DataFrame:
        """Fetch historical OHLCV bars for a single security.

        Either ``period`` or an explicit ``start``/``end`` range must be
        supplied. When both are given, the explicit range wins and
        ``period`` is ignored, matching ``yfinance`` semantics.

        Args:
            symbol: Fully-qualified Yahoo Finance ticker (e.g. ``"VOD.L"``).
            ticker: Plain local ticker, used with ``exchange`` when
                ``symbol`` is omitted (e.g. ``ticker="VOD", exchange="LSE"``).
            exchange: Exchange identifier; see :func:`resolve_symbol`.
            period: Lookback window relative to today.
            start: Inclusive start date for an explicit range.
            end: Exclusive end date for an explicit range.
            interval: Bar interval. Defaults to daily.
            instrument_currency: ISO code of the currency the instrument
                trades in. Required when ``base_currency`` is set and
                differs from it; ignored otherwise.
            base_currency: When supplied and different from
                ``instrument_currency``, OHLC price columns are converted
                into this currency by multiplying by a daily
                ``{instrument_currency}{base_currency}=X`` series fetched
                from Yahoo Finance, forward-filled to cover non-overlapping
                trading days. ``Volume`` is left untouched. ``None`` (the
                default) returns the local-currency series.

        Returns:
            A :class:`pandas.DataFrame` indexed by timestamp with columns
            ``Open``, ``High``, ``Low``, ``Close``, ``Volume`` (and
            ``Adj Close`` when ``auto_adjust`` is ``False``).

        Raises:
            MarketDataError: If the response is empty, or if currency
                conversion was requested but the FX series is empty.
            ValueError: If ``base_currency`` is set without
                ``instrument_currency``.

        """
        resolved = self.resolve_args(symbol, ticker, exchange)
        yf_ticker = yf.Ticker(resolved)
        kwargs: dict[str, object] = {
            "interval": interval,
            "auto_adjust": self.auto_adjust,
        }
        if start is not None or end is not None:
            kwargs["start"] = start
            kwargs["end"] = end
        else:
            kwargs["period"] = period

        logger.debug("Fetching %s history with %s", resolved, kwargs)
        df = yf_ticker.history(**kwargs)  # pyright: ignore[reportUnknownMemberType]
        if df.empty:
            raise MarketDataError(
                f"no market data returned for {resolved!r} with {kwargs}",
            )
        if base_currency is not None:
            if instrument_currency is None:
                raise ValueError(
                    "instrument_currency is required when base_currency is set",
                )
            df = self._convert_to_base(df, instrument_currency, base_currency)
        return df

    def _convert_to_base(
        self,
        df: pd.DataFrame,
        instrument_currency: str,
        base_currency: str,
    ) -> pd.DataFrame:
        """Convert OHLC columns of ``df`` into ``base_currency``.

        Fetches a daily ``{instrument_currency}{base_currency}=X`` series
        spanning the index of ``df`` and multiplies the price columns by
        the forward-filled FX rate. ``Volume`` (and any other non-price
        columns) is preserved unchanged.

        Args:
            df: Frame returned by :meth:`get_history` in instrument currency.
            instrument_currency: ISO code of the instrument's quote currency.
            base_currency: ISO code of the desired reporting currency.

        Returns:
            A new frame with price columns expressed in ``base_currency``.
            Returned unchanged when the two currencies match (case-insensitive).

        Raises:
            MarketDataError: If Yahoo Finance returns no FX data.

        """
        # ``GBp`` is Yahoo's notation for sterling pence — divide by 100 and
        # treat as GBP so the FX step works with normal ISO codes.
        if instrument_currency == "GBp":
            price_cols = [c for c in ("Open", "High", "Low", "Close") if c in df.columns]
            df = df.copy()
            df[price_cols] = df[price_cols].astype(float) / 100.0
            instrument_currency = "GBP"
        if instrument_currency.upper() == base_currency.upper():
            return df
        fx_symbol = f"{instrument_currency.upper()}{base_currency.upper()}=X"
        index = df.index
        start = index.min()
        end = index.max() + pd.Timedelta(days=1)
        fx = yf.Ticker(fx_symbol).history(  # pyright: ignore[reportUnknownMemberType]
            start=start,
            end=end,
            interval="1d",
            auto_adjust=self.auto_adjust,
        )
        if fx.empty:
            raise MarketDataError(
                f"no FX data returned for {fx_symbol!r} between {start} and {end}",
            )
        rate: pd.Series = fx["Close"]
        rate_idx: Any = rate.index
        if getattr(rate_idx, "tz", None) is not None:
            rate.index = rate_idx.tz_localize(None)
        target_idx: Any = index
        target_index = (
            target_idx.tz_localize(None) if getattr(target_idx, "tz", None) is not None else index
        )
        aligned: pd.Series = rate.reindex(target_index, method="ffill").bfill()
        aligned.index = index
        out = df.copy()
        price_cols = [c for c in ("Open", "High", "Low", "Close", "Adj Close") if c in out.columns]
        for col in price_cols:
            out[col] = out[col] * aligned
        return out

    def get_close_series(
        self,
        symbol: str | None = None,
        *,
        ticker: str | None = None,
        exchange: str | None = None,
        period: Period | None = "1y",
        start: str | date | datetime | None = None,
        end: str | date | datetime | None = None,
        interval: Interval = "1d",
        column: PriceColumn = "Close",
        instrument_currency: str | None = None,
        base_currency: str | None = None,
    ) -> pd.Series:
        """Fetch a single price column as a :class:`pandas.Series`.

        Args:
            symbol: Fully-qualified Yahoo Finance ticker.
            ticker: Plain ticker; used with ``exchange`` when ``symbol``
                is omitted.
            exchange: Exchange identifier; see :func:`resolve_symbol`.
            period: Lookback window; ignored when ``start``/``end`` are set.
            start: Inclusive range start.
            end: Exclusive range end.
            interval: Bar interval.
            column: Which OHLCV column to return. Defaults to ``"Close"``,
                which is split- and dividend-adjusted when
                ``auto_adjust`` is ``True``.
            instrument_currency: ISO code of the instrument's quote currency.
                Required with ``base_currency``.
            base_currency: When supplied, prices are converted into this
                currency via the corresponding ``=X`` FX series.

        Returns:
            A series of prices indexed by timestamp, named after the
            resolved Yahoo symbol.

        Raises:
            MarketDataError: If the response is empty or lacks ``column``.

        """
        df = self.get_history(
            symbol=symbol,
            ticker=ticker,
            exchange=exchange,
            period=period,
            start=start,
            end=end,
            interval=interval,
            instrument_currency=instrument_currency,
            base_currency=base_currency,
        )
        if column not in df.columns:
            raise MarketDataError(
                f"column {column!r} not present in response",
            )
        resolved = self.resolve_args(symbol, ticker, exchange)
        return df[column].rename(resolved)

    def get_daily_returns(
        self,
        symbol: str | None = None,
        *,
        ticker: str | None = None,
        exchange: str | None = None,
        period: Period | None = "1y",
        start: str | date | datetime | None = None,
        end: str | date | datetime | None = None,
        kind: ReturnKind = "simple",
        instrument_currency: str | None = None,
        base_currency: str | None = None,
    ) -> pd.Series:
        """Compute a daily return series for a single security.

        Returns are derived from the (auto-adjusted, by default) ``Close``
        column, so dividends and splits are reflected in the series.

        Args:
            symbol: Fully-qualified Yahoo Finance ticker.
            ticker: Plain ticker; used with ``exchange`` when ``symbol``
                is omitted.
            exchange: Exchange identifier; see :func:`resolve_symbol`.
            period: Lookback window; ignored when ``start``/``end`` are set.
            start: Inclusive range start.
            end: Exclusive range end.
            kind: ``"simple"`` for ``P_t / P_{t-1} - 1``; ``"log"`` for
                ``ln(P_t / P_{t-1})``. Log returns are additive over time
                and convenient for cumulative sums.
            instrument_currency: ISO code of the instrument's quote currency.
                Required with ``base_currency``.
            base_currency: When supplied, returns are computed from prices
                pre-converted into this currency, capturing the combined
                instrument + FX move.

        Returns:
            A series of returns indexed by timestamp, named after the
            resolved Yahoo symbol. The first observation is dropped (no
            prior close to diff against).

        Raises:
            MarketDataError: If the response is empty or contains fewer
                than two observations.
            ValueError: If ``kind`` is not ``"simple"`` or ``"log"``.

        """
        prices = self.get_close_series(
            symbol=symbol,
            ticker=ticker,
            exchange=exchange,
            period=period,
            start=start,
            end=end,
            interval="1d",
            instrument_currency=instrument_currency,
            base_currency=base_currency,
        )
        if len(prices) < _MIN_OBSERVATIONS_FOR_RETURNS:
            raise MarketDataError(
                f"need at least {_MIN_OBSERVATIONS_FOR_RETURNS} closes to compute "
                f"returns, got {len(prices)}",
            )
        if kind == "simple":
            returns = prices.pct_change()
        elif kind == "log":
            returns = prices.apply(np.log).diff()
        else:
            raise ValueError(f"unknown return kind: {kind!r}")
        resolved = self.resolve_args(symbol, ticker, exchange)
        return returns.dropna().rename(resolved)
