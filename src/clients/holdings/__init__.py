"""Fund look-through decomposition: holdings sources, cache, and models."""

from __future__ import annotations

from src.clients.holdings.adapters.base import HoldingsError, IssuerAdapter
from src.clients.holdings.adapters.index_proxy import IndexProxyAdapter
from src.clients.holdings.adapters.ishares import ISharesAdapter
from src.clients.holdings.adapters.static_top_holdings import StaticTopHoldingsAdapter
from src.clients.holdings.adapters.top_holdings import TopHoldingsAdapter
from src.clients.holdings.adapters.vanguard import VanguardAdapter
from src.clients.holdings.cache import CachedHoldingsClient, ParquetHoldingsCache
from src.clients.holdings.identity import (
    FundIdentity,
    load_fund_identities,
    resolve_identity,
)
from src.clients.holdings.models import (
    Constituent,
    DecompositionResult,
    FundHoldings,
    reason_text,
)


def build_default_client() -> CachedHoldingsClient:
    """Return a holdings client wired with all available issuer adapters."""
    return CachedHoldingsClient(
        adapters=[
            ISharesAdapter(),
            VanguardAdapter(),
            # Index substitution reads a proxy tracker's CSV via the iShares parser.
            IndexProxyAdapter(ISharesAdapter()),
            # Top-10 partial decomposition for funds with no full-holdings feed.
            TopHoldingsAdapter(),
            # Hand-seeded top-10 for funds with no machine-readable feed at all.
            StaticTopHoldingsAdapter(),
        ],
    )


__all__ = [
    "CachedHoldingsClient",
    "Constituent",
    "DecompositionResult",
    "FundHoldings",
    "FundIdentity",
    "HoldingsError",
    "ISharesAdapter",
    "IndexProxyAdapter",
    "IssuerAdapter",
    "ParquetHoldingsCache",
    "StaticTopHoldingsAdapter",
    "TopHoldingsAdapter",
    "VanguardAdapter",
    "build_default_client",
    "load_fund_identities",
    "reason_text",
    "resolve_identity",
]
