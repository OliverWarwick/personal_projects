"""On-disk parquet cache for fund holdings, mirroring the price cache.

Two cooperating classes, following ``src/clients/marketdata/cache.py``:

* :class:`ParquetHoldingsCache` — one parquet file per fund (keyed by the
  ``"{issuer}:{product_id}"`` fund key), holding the constituent table.
* :class:`CachedHoldingsClient` — dispatches to the registered issuer
  adapter on a cache miss, persists the result, and gates re-fetches with
  the same TTL/negative-cache policy as the price cache (constituents move
  slowly, so the "ok" TTL is generous).

Holdings change daily-to-monthly, so the dashboard reads from parquet on
every request and only re-hits the issuer when the cache is cold or stale.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from src.clients.holdings.adapters.base import HoldingsError, IssuerAdapter
from src.clients.holdings.models import (
    REASON_ADAPTER_ERROR,
    REASON_NO_ADAPTER,
    REASON_NO_SOURCE,
    REASON_STALE_NO_DATA,
    Constituent,
    DecompositionResult,
    FundHoldings,
)

if TYPE_CHECKING:
    from src.clients.holdings.identity import FundIdentity

logger = logging.getLogger(__name__)

# Trust a cached holdings file for this long before re-asking the issuer.
# Constituents drift slowly, so a day keeps the dashboard fast while still
# picking up rebalances promptly.
_OK_TTL_HOURS = 24
# Suppress retries of funds the issuer refused (bad URL, pulled product) so a
# broken source does not hammer the network on every page load.
_NEGATIVE_TTL_DAYS = 7


def _default_cache_dir() -> Path:
    """Return ``~/.cache/personal_project/holdings`` (mirrors the price cache)."""
    return Path.home() / ".cache" / "personal_project" / "holdings"


class ParquetHoldingsCache:
    """On-disk parquet cache of fund constituents, keyed by fund key.

    One parquet file per fund holds the constituent table plus the source
    metadata in the parquet's pandas attrs. Exposes ``load`` / ``save`` /
    ``clear`` primitives; TTL policy lives in :class:`CachedHoldingsClient`.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        """Initialise the cache, creating ``cache_dir`` if needed."""
        self.cache_dir = cache_dir or _default_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, fund_key: str) -> Path:
        safe = fund_key.replace("/", "_").replace(":", "_")
        return self.cache_dir / f"{safe}.parquet"

    def has(self, fund_key: str) -> bool:
        """Return whether a cache file exists for ``fund_key``."""
        return self._path(fund_key).exists()

    def load(self, fund_key: str) -> FundHoldings | None:
        """Load cached holdings for ``fund_key``, or ``None`` if absent."""
        path = self._path(fund_key)
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        attrs = df.attrs
        constituents = tuple(
            Constituent(
                ticker=str(r.ticker),
                name=str(r.name),
                weight=Decimal(str(r.weight)),
                asset_class=str(r.asset_class),
                isin=str(r.isin),
                currency=str(r.currency),
            )
            for r in df.itertuples(index=False)
        )
        as_of_raw = attrs.get("as_of", "")
        as_of = date.fromisoformat(as_of_raw) if as_of_raw else datetime.now(UTC).date()
        return FundHoldings(
            fund_key=fund_key,
            as_of=as_of,
            constituents=constituents,
            source=str(attrs.get("source", "")),
            source_url=str(attrs.get("source_url", "")),
            partial=bool(attrs.get("partial", False)),
        )

    def save(self, holdings: FundHoldings) -> None:
        """Persist ``holdings`` to a parquet file, overwriting any prior copy."""
        df = pd.DataFrame(
            {
                "ticker": [c.ticker for c in holdings.constituents],
                "name": [c.name for c in holdings.constituents],
                "weight": [str(c.weight) for c in holdings.constituents],
                "asset_class": [c.asset_class for c in holdings.constituents],
                "isin": [c.isin for c in holdings.constituents],
                "currency": [c.currency for c in holdings.constituents],
            },
        )
        df.attrs = {
            "as_of": holdings.as_of.isoformat(),
            "source": holdings.source,
            "source_url": holdings.source_url,
            "partial": holdings.partial,
        }
        df.to_parquet(self._path(holdings.fund_key))
        logger.info(
            "holdings cache save: %s (%d constituents) -> %s",
            holdings.fund_key,
            len(holdings.constituents),
            self._path(holdings.fund_key),
        )

    def clear(self, fund_key: str | None = None) -> int:
        """Remove cached files; all of them when ``fund_key`` is ``None``."""
        if fund_key is not None:
            path = self._path(fund_key)
            if path.exists():
                path.unlink()
                return 1
            return 0
        deleted = 0
        for path in self.cache_dir.glob("*.parquet"):
            path.unlink()
            deleted += 1
        return deleted


class CachedHoldingsClient:
    """Holdings client backed by the parquet cache + issuer adapters.

    On :meth:`get_holdings` it consults the cache, honouring the OK TTL and
    a negative-cache window, and only dispatches to the issuer adapter when
    the cache is cold or stale. Always returns a :class:`DecompositionResult`
    so the caller can keep an undecomposable fund in primary space with a
    recorded reason rather than handling exceptions.
    """

    _METADATA_FILE = "_metadata.json"

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        adapters: list[IssuerAdapter] | None = None,
    ) -> None:
        """Wire the parquet cache to the given issuer adapters."""
        self.cache = ParquetHoldingsCache(cache_dir=cache_dir)
        self._adapters: dict[str, IssuerAdapter] = {a.issuer_id: a for a in (adapters or [])}

    # -- metadata (mirrors CachedYFinanceClient) -----------------------

    def _meta_path(self) -> Path:
        return self.cache.cache_dir / self._METADATA_FILE

    def _load_meta(self) -> dict[str, dict[str, str]]:
        path = self._meta_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("could not read holdings metadata at %s; ignoring", path)
            return {}

    def _save_meta(self, meta: dict[str, dict[str, str]]) -> None:
        self._meta_path().write_text(json.dumps(meta, indent=2, sort_keys=True))

    def _record_attempt(self, fund_key: str, status: str, reason: str = "") -> None:
        meta = self._load_meta()
        meta[fund_key] = {
            "status": status,
            "reason": reason,
            "last_attempt_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self._save_meta(meta)

    def _attempt_age(self, fund_key: str) -> tuple[str | None, timedelta | None]:
        entry = self._load_meta().get(fund_key)
        if not entry:
            return None, None
        ts = datetime.fromisoformat(entry["last_attempt_at"])
        return entry.get("status"), datetime.now(UTC) - ts

    # -- public API ----------------------------------------------------

    def get_holdings(  # noqa: PLR0911
        self,
        identity: FundIdentity,
        *,
        refresh: bool = False,
    ) -> DecompositionResult:
        """Return a decomposition result for ``identity``.

        Serves from the parquet cache within the OK TTL; otherwise fetches
        via the issuer adapter and persists the result. A negative-cached
        fund (recent failure) is left primary without re-fetching.

        Args:
            identity: Resolved fund identity.
            refresh: Force a re-fetch, bypassing both TTLs.

        """
        fund_key = identity.fund_key
        adapter = self._adapters.get(identity.issuer)
        if adapter is None:
            reason = REASON_NO_SOURCE if identity.exhausted else REASON_NO_ADAPTER
            return DecompositionResult(decomposed=False, reason=reason)

        status, age = self._attempt_age(fund_key)
        cached = None if refresh else self.cache.load(fund_key)

        if (
            not refresh
            and status == "empty"
            and age is not None
            and age < timedelta(days=_NEGATIVE_TTL_DAYS)
        ):
            if cached is not None:
                return DecompositionResult(decomposed=True, holdings=cached)
            return DecompositionResult(decomposed=False, reason=REASON_STALE_NO_DATA)

        fresh_ok = status == "ok" and age is not None and age < timedelta(hours=_OK_TTL_HOURS)
        if cached is not None and fresh_ok and not refresh:
            return DecompositionResult(decomposed=True, holdings=cached)

        try:
            holdings = adapter.fetch(identity)
        except HoldingsError as exc:
            logger.warning("holdings fetch failed for %s: %s", fund_key, exc)
            self._record_attempt(fund_key, "empty", reason=str(exc))
            if cached is not None:
                return DecompositionResult(decomposed=True, holdings=cached)
            return DecompositionResult(
                decomposed=False,
                reason=REASON_ADAPTER_ERROR,
                diagnostics={"error": str(exc)},
            )
        self.cache.save(holdings)
        self._record_attempt(fund_key, "ok")
        return DecompositionResult(decomposed=True, holdings=holdings)
