"""On-disk parquet cache for Yahoo Finance close prices.

Provides two cooperating classes:

* :class:`ParquetPriceCache` — a thin storage layer that holds one
  parquet file per resolved Yahoo symbol, each containing a single
  ``Close`` column indexed by date. Exposes ``load`` / ``upsert`` /
  ``clear`` primitives and stays out of policy.
* :class:`CachedYFinanceClient` — wraps a :class:`YFinanceClient` and a
  :class:`ParquetPriceCache`. On each request it consults the cache,
  fetches only the missing head and/or tail segments, and merges the
  result back to disk.

The cache is keyed by the *resolved* Yahoo symbol, so callers that
reach the same symbol via different (ticker, exchange) inputs share one
cache entry. Pass ``refresh=True`` to bypass the cache after corporate
actions that re-adjust historical prices.

Logging is emitted at ``INFO`` for cache hits, misses, fetches, and
upserts so the read/write path is visible without a debugger; turn
``logging`` to ``DEBUG`` for full request payloads. Configure once at
the entry point, e.g.::

    logging.basicConfig(level=logging.INFO)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.clients.marketdata.yfinance_client import (
    MarketDataError,
    Period,
    ReturnKind,
    YFinanceClient,
)

logger = logging.getLogger(__name__)


_MIN_OBSERVATIONS_FOR_RETURNS = 2

# How long to trust the on-disk cache without re-asking Yahoo for newer dates.
# Within this window, a cached tail is considered authoritative.
_OK_TTL_HOURS = 6
# How long to suppress retries of symbols Yahoo refused. Long enough that we
# don't burn rate limits on bad tickers, short enough that a renamed/listed
# symbol heals within a month.
_NEGATIVE_TTL_DAYS = 30

# Process-local set of symbols Yahoo has refused; we skip refetching them for
# the lifetime of the process to avoid burning through rate limits. Cleared
# on restart, so a typo is recoverable.
_NEGATIVE_SYMBOL_CACHE: set[str] = set()

# Approximate calendar-day windows for each yfinance ``period`` literal.
# Used to translate a relative period into an absolute date range so the
# cache can be consulted on absolute dates. Slightly generous to absorb
# weekends and holidays; the exact bounds are then applied on the slice.
_PERIOD_TO_DAYS: dict[str, int] = {
    "1d": 4,
    "5d": 10,
    "1mo": 35,
    "3mo": 100,
    "6mo": 200,
    "1y": 380,
    "2y": 760,
    "5y": 1900,
    "10y": 3800,
    "ytd": 380,
    "max": 365 * 50,
}


def _default_cache_dir() -> Path:
    """Return the default on-disk cache directory.

    Resolves to ``~/.cache/personal_project/yfinance``, which fits the
    XDG Base Directory convention on macOS and Linux.

    Returns:
        The default cache directory path.

    """
    return Path.home() / ".cache" / "personal_project" / "yfinance"


def _to_date(value: str | date | datetime) -> date:
    """Coerce a date-like input into a :class:`datetime.date`.

    Args:
        value: A ``date``, ``datetime``, or ISO-8601 date string.

    Returns:
        The corresponding ``date``.

    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(value).date()


class ParquetPriceCache:
    """On-disk parquet cache of daily close prices, keyed by Yahoo symbol.

    One parquet file per resolved Yahoo symbol holds a single ``Close``
    column indexed by date. The cache exposes only primitive operations
    (``load`` / ``upsert`` / ``clear``); higher-level concerns such as
    gap detection and TTLs live in :class:`CachedYFinanceClient`.

    Attributes:
        cache_dir: Directory holding the parquet files.

    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        """Initialise the cache, creating the directory if needed.

        Args:
            cache_dir: Override the default cache directory. When
                ``None``, falls back to ``~/.cache/personal_project/yfinance``.

        """
        self.cache_dir = cache_dir or _default_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("ParquetPriceCache initialised at %s", self.cache_dir)

    def _path(self, symbol: str) -> Path:
        """Return the parquet path for *symbol*.

        Forward slashes (which appear in some Yahoo symbols) are
        replaced with underscores to keep the filename POSIX-safe.

        Args:
            symbol: Resolved Yahoo Finance symbol.

        Returns:
            The parquet file path.

        """
        safe = symbol.replace("/", "_")
        return self.cache_dir / f"{safe}.parquet"

    def has(self, symbol: str) -> bool:
        """Return whether a cache file exists for *symbol*.

        Args:
            symbol: Resolved Yahoo Finance symbol.

        Returns:
            ``True`` if a parquet file is present, ``False`` otherwise.

        """
        return self._path(symbol).exists()

    def load(self, symbol: str) -> pd.Series | None:
        """Load the cached close series for *symbol*.

        Args:
            symbol: Resolved Yahoo Finance symbol.

        Returns:
            A close-price series indexed by date, named after the
            symbol. ``None`` if no cache file exists for the symbol.

        """
        path = self._path(symbol)
        if not path.exists():
            logger.debug("cache miss (no file): %s", symbol)
            return None
        df = pd.read_parquet(path)
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        # Strip any tz so downstream slicing with tz-naive pd.Timestamp
        # bounds (and DataFrame joins against tz-naive callers) does not
        # raise ``Cannot compare tz-naive and tz-aware datetime-like
        # objects``. yfinance returns tz-aware indices for some symbols
        # (e.g. crypto, FX) which would otherwise leak through.
        idx: pd.DatetimeIndex = df.index
        if idx.tz is not None:
            df.index = idx.tz_localize(None)
        series = df["Close"].sort_index().rename(symbol)
        logger.debug(
            "cache load: %s -> %d rows (%s..%s) from %s",
            symbol,
            len(series),
            series.index.min().date() if len(series) else None,
            series.index.max().date() if len(series) else None,
            path,
        )
        return series

    def upsert(self, symbol: str, series: pd.Series) -> pd.Series:
        """Merge *series* into the cache for *symbol*, returning the union.

        Rows in ``series`` overwrite existing cache rows at the same
        date, so this also functions as a fix-up path after a corporate
        action re-adjusts prices.

        Args:
            symbol: Resolved Yahoo Finance symbol.
            series: A close-price series to merge in.

        Returns:
            The merged close-price series after the upsert.

        """
        existing = self.load(symbol)
        added: int
        if existing is None:
            merged = series.copy()
            added = len(merged)
        else:
            combined = pd.concat([existing, series])
            merged = combined[~combined.index.duplicated(keep="last")].sort_index()
            added = len(merged) - len(existing)
        path = self._path(symbol)
        merged.to_frame(name="Close").to_parquet(path)
        logger.info(
            "cache upsert: %s +%d rows (total %d) -> %s",
            symbol,
            added,
            len(merged),
            path,
        )
        return merged.rename(symbol)

    def clear(self, symbol: str | None = None) -> int:
        """Remove cached files.

        Args:
            symbol: Resolved symbol whose cache should be removed. When
                ``None``, removes every parquet file in ``cache_dir``.

        Returns:
            The number of files deleted.

        """
        if symbol is not None:
            path = self._path(symbol)
            if path.exists():
                path.unlink()
                logger.info("cache cleared: %s", symbol)
                return 1
            return 0
        deleted = 0
        for path in self.cache_dir.glob("*.parquet"):
            path.unlink()
            deleted += 1
        logger.info("cache cleared: %d files in %s", deleted, self.cache_dir)
        return deleted


class CachedYFinanceClient:
    """Yahoo Finance client backed by an on-disk parquet cache.

    Wraps :class:`YFinanceClient` and :class:`ParquetPriceCache`. On
    each call, the requested date range is intersected with the cache;
    only the missing head and/or tail segments are fetched from
    upstream, then merged back. Returns are computed from cached prices
    without any further network call.

    The cache is keyed by the resolved Yahoo symbol, so requests that
    arrive with different ``(ticker, exchange)`` combinations but
    resolve to the same symbol share one entry. Pass ``refresh=True``
    to bypass the cache and re-fetch the full range, useful after a
    split or dividend re-adjusts historical prices.

    Attributes:
        cache: The underlying :class:`ParquetPriceCache`.
        client: The underlying :class:`YFinanceClient` for upstream calls.

    """

    _METADATA_FILE = "_metadata.json"

    def _meta_path(self) -> Path:
        return self.cache.cache_dir / self._METADATA_FILE

    def _load_meta(self) -> dict[str, dict[str, str]]:
        path = self._meta_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("could not read cache metadata at %s; ignoring", path)
            return {}

    def _save_meta(self, meta: dict[str, dict[str, str]]) -> None:
        self._meta_path().write_text(json.dumps(meta, indent=2, sort_keys=True))

    def _record_attempt(self, symbol: str, status: str) -> None:
        meta = self._load_meta()
        meta[symbol] = {
            "status": status,
            "last_attempt_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self._save_meta(meta)

    def _attempt_age(self, symbol: str) -> tuple[str | None, timedelta | None]:
        entry = self._load_meta().get(symbol)
        if not entry:
            return None, None
        ts = datetime.fromisoformat(entry["last_attempt_at"])
        return entry.get("status"), datetime.now(UTC) - ts

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        client: YFinanceClient | None = None,
    ) -> None:
        """Initialise the cached client.

        Args:
            cache_dir: Override the default cache directory.
            client: An existing :class:`YFinanceClient` to delegate
                upstream fetches to. When ``None``, a default client
                with ``auto_adjust=True`` is constructed.

        """
        self.cache = ParquetPriceCache(cache_dir=cache_dir)
        self.client = client or YFinanceClient()

    @staticmethod
    def _missing_segments(
        cached: pd.Series | None,
        req_start: date,
        req_end: date,
        resolved: str,
    ) -> list[tuple[date, date]]:
        """Return the date segments that need to be fetched from upstream.

        Args:
            cached: Existing cached series, or ``None``.
            req_start: Inclusive requested start date.
            req_end: Inclusive requested end date.
            resolved: Resolved Yahoo symbol, used only for log messages.

        Returns:
            A list of ``(start, end)`` segments to fetch. Empty when the
            cache fully covers the request.

        """
        if cached is None or cached.empty:
            logger.info(
                "cache miss for %s: fetching full range %s..%s",
                resolved, req_start, req_end,
            )
            return [(req_start, req_end)]

        cache_start = cached.index.min().date()
        cache_end = cached.index.max().date()
        segments: list[tuple[date, date]] = []
        if req_start < cache_start:
            head_end = cache_start - timedelta(days=1)
            logger.info(
                "cache partial hit for %s (%s..%s); fetching head %s..%s",
                resolved, cache_start, cache_end, req_start, head_end,
            )
            segments.append((req_start, head_end))
        if req_end > cache_end:
            tail_start = cache_end + timedelta(days=1)
            logger.info(
                "cache partial hit for %s (%s..%s); fetching tail %s..%s",
                resolved, cache_start, cache_end, tail_start, req_end,
            )
            segments.append((tail_start, req_end))
        if not segments:
            logger.info(
                "cache full hit for %s: serving %s..%s from disk (cache spans %s..%s)",
                resolved, req_start, req_end, cache_start, cache_end,
            )
        return segments

    def _normalise_range(
        self,
        period: Period | None,
        start: str | date | datetime | None,
        end: str | date | datetime | None,
    ) -> tuple[date, date]:
        """Translate yfinance ``period`` / ``start`` / ``end`` into absolute dates.

        Args:
            period: Yfinance period literal, or ``None``.
            start: Optional explicit start. Wins over ``period``.
            end: Optional explicit end. Wins over ``period``.

        Returns:
            A ``(start, end)`` tuple of inclusive dates.

        """
        today = datetime.now().date()  # noqa: DTZ005 — local "today" is fine for cache windowing
        if start is not None or end is not None:
            req_start = (
                _to_date(start) if start is not None else today - timedelta(days=365)
            )
            req_end = _to_date(end) if end is not None else today
        elif period == "ytd":
            req_start = date(today.year, 1, 1)
            req_end = today
        elif period == "max":
            req_start = date(1970, 1, 1)
            req_end = today
        else:
            days = _PERIOD_TO_DAYS.get(period or "1y", 380)
            req_start = today - timedelta(days=days)
            req_end = today
        return req_start, req_end

    def get_close_series(
        self,
        symbol: str | None = None,
        *,
        ticker: str | None = None,
        exchange: str | None = None,
        period: Period | None = "1y",
        start: str | date | datetime | None = None,
        end: str | date | datetime | None = None,
        instrument_currency: str | None = None,
        base_currency: str | None = None,
        refresh: bool = False,
    ) -> pd.Series:
        """Return a cached close-price series for the requested range.

        Args:
            symbol: Fully-qualified Yahoo Finance ticker.
            ticker: Plain ticker; used with ``exchange`` when ``symbol``
                is omitted.
            exchange: Exchange identifier; see :func:`resolve_symbol`.
            period: Lookback window relative to today; ignored when
                ``start``/``end`` are set.
            start: Inclusive start date.
            end: Inclusive end date.
            refresh: When ``True``, ignore the cache and re-fetch the
                full requested range.

        Returns:
            A close-price series indexed by date, named after the
            resolved Yahoo symbol.

        Raises:
            MarketDataError: If neither cache nor upstream supply data
                for the requested range.

        """
        resolved = YFinanceClient.resolve_args(symbol, ticker, exchange)
        req_start, req_end = self._normalise_range(period, start, end)

        prior_status, prior_age = self._attempt_age(resolved)
        if (
            not refresh
            and prior_status == "empty"
            and prior_age is not None
            and prior_age < timedelta(days=_NEGATIVE_TTL_DAYS)
        ):
            _NEGATIVE_SYMBOL_CACHE.add(resolved)
            raise MarketDataError(
                f"{resolved!r} previously returned no data "
                f"({prior_age} ago); skipping refetch",
            )
        if resolved in _NEGATIVE_SYMBOL_CACHE and not refresh:
            raise MarketDataError(
                f"{resolved!r} previously returned no data; skipping refetch",
            )

        if refresh:
            logger.info("refresh=True: bypassing cache for %s", resolved)
            cached: pd.Series | None = None
        else:
            cached = self.cache.load(resolved)

        to_fetch = self._missing_segments(cached, req_start, req_end, resolved)
        # Trust the existing parquet within the OK TTL: skip tail-only fetches
        # if we attempted recently. Head-fetches (older history) still run so
        # broader lookbacks fill in correctly.
        if (
            not refresh
            and prior_status == "ok"
            and prior_age is not None
            and prior_age < timedelta(hours=_OK_TTL_HOURS)
            and cached is not None
        ):
            cache_end = cached.index.max().date()
            to_fetch = [(s, e) for (s, e) in to_fetch if e <= cache_end]

        any_fetch_succeeded = False
        for fetch_start, fetch_end in to_fetch:
            if fetch_start > fetch_end:
                continue
            try:
                fresh = self.client.get_close_series(
                    symbol=resolved,
                    start=fetch_start,
                    end=fetch_end + timedelta(days=1),
                )
                # Normalise to tz-naive before caching so on-disk parquet
                # stays consistent regardless of the symbol's source tz.
                fresh_idx: pd.DatetimeIndex = fresh.index  # type: ignore[assignment]
                if fresh_idx.tz is not None:
                    fresh.index = fresh_idx.tz_localize(None)
                self.cache.upsert(resolved, fresh)
                any_fetch_succeeded = True
            except MarketDataError as exc:
                logger.warning(
                    "upstream returned no data for %s (%s..%s): %s",
                    resolved, fetch_start, fetch_end, exc,
                )
                if cached is None or cached.empty:
                    _NEGATIVE_SYMBOL_CACHE.add(resolved)
                    self._record_attempt(resolved, "empty")
                    raise
        if any_fetch_succeeded or (cached is not None and not cached.empty):
            self._record_attempt(resolved, "ok")

        full = self.cache.load(resolved)
        if full is None or full.empty:
            raise MarketDataError(f"no data available for {resolved!r}")
        sliced = full.loc[pd.Timestamp(req_start) : pd.Timestamp(req_end)]
        if sliced.empty:
            raise MarketDataError(
                f"cache and upstream returned no data for {resolved!r} "
                f"between {req_start} and {req_end}",
            )
        logger.debug(
            "served %s: %d rows (%s..%s)",
            resolved,
            len(sliced),
            sliced.index.min().date(),
            sliced.index.max().date(),
        )
        # LSE pence-quoted instruments ('GBp') need a /100 conversion to land
        # in 'GBP' base. Case-folded equality would skip the FX path entirely
        # and inflate values 100×; handle that pence→pound step explicitly
        # before the FX-pair lookup.
        if (
            instrument_currency == "GBp"
            and base_currency is not None
            and base_currency.upper() == "GBP"
        ):
            sliced = sliced / 100.0
            return sliced.rename(resolved)
        if (
            base_currency is not None
            and instrument_currency is not None
            and base_currency.upper() != instrument_currency.upper()
        ):
            fx_symbol = f"{instrument_currency.upper()}{base_currency.upper()}=X"
            fx_series = self.get_close_series(
                symbol=fx_symbol,
                start=req_start,
                end=req_end,
                refresh=refresh,
            )
            fx_idx: object = fx_series.index
            if getattr(fx_idx, "tz", None) is not None:
                fx_series.index = fx_idx.tz_localize(None)  # type: ignore[attr-defined]
            aligned = fx_series.reindex(sliced.index, method="ffill").bfill()
            sliced = sliced * aligned
        return sliced.rename(resolved)

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
        refresh: bool = False,
    ) -> pd.Series:
        """Compute daily returns from cached close prices.

        Args:
            symbol: Fully-qualified Yahoo Finance ticker.
            ticker: Plain ticker; used with ``exchange``.
            exchange: Exchange identifier.
            period: Lookback window relative to today.
            start: Inclusive start date.
            end: Inclusive end date.
            kind: ``"simple"`` for ``P_t / P_{t-1} - 1``; ``"log"`` for
                ``ln(P_t / P_{t-1})``.
            refresh: Force cache bypass.

        Returns:
            A daily returns series indexed by date.

        Raises:
            MarketDataError: If too few observations are available.
            ValueError: If ``kind`` is not ``"simple"`` or ``"log"``.

        """
        prices = self.get_close_series(
            symbol=symbol,
            ticker=ticker,
            exchange=exchange,
            period=period,
            start=start,
            end=end,
            refresh=refresh,
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
        resolved = YFinanceClient.resolve_args(symbol, ticker, exchange)
        return returns.dropna().rename(resolved)
