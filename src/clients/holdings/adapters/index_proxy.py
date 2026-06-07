"""Index-substitution adapter for funds with no issuer-direct holdings feed.

Some funds track a well-known index but publish no usable constituent file
(e.g. the Fidelity MSCI World Index OEIC discloses only top-10). When an
*approximate* look-through is acceptable, we decompose such a fund using
another issuer's full holdings for the **same index** — e.g. iShares Core
MSCI World — as a proxy.

The proxy's holdings are fetched and parsed by an existing adapter (the seed
entry's ``holdings_url`` points at, say, an iShares CSV), then re-tagged with
``source="index_proxy"`` so every downstream consumer can flag the result as
approximate rather than passing it off as the fund's real holdings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.clients.holdings.adapters.base import IssuerAdapter
from src.clients.holdings.models import FundHoldings

if TYPE_CHECKING:
    from src.clients.holdings.identity import FundIdentity

PROXY_SOURCE = "index_proxy"


class IndexProxyAdapter(IssuerAdapter):
    """Decompose a fund via a proxy tracker of the same index.

    Delegates the actual fetch/parse to ``fetcher`` (an adapter that
    understands the proxy source's format — typically the iShares CSV
    adapter, since the seed points its ``holdings_url`` at an iShares file),
    then re-tags the snapshot as proxy-sourced.
    """

    issuer_id = "index_proxy"

    def __init__(self, fetcher: IssuerAdapter) -> None:
        """Wrap the adapter that knows how to read the proxy source format."""
        self._fetcher = fetcher

    def fetch(self, identity: FundIdentity) -> FundHoldings:  # noqa: D102 (see base)
        inner = self._fetcher.fetch(identity)
        return FundHoldings(
            fund_key=identity.fund_key,
            as_of=inner.as_of,
            constituents=inner.constituents,
            source=PROXY_SOURCE,
            source_url=inner.source_url,
        )
