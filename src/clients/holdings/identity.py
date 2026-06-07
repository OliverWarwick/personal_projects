"""Fund-identity seed: maps broker codes / ISINs to issuer holdings sources.

HL and AJ Bell exports carry no ISIN — only broker codes like ``VUAG`` or
``BYX5P48`` — so the broker code is the primary lookup key, with ISIN as an
additional alias when a broker (IBKR) supplies one. The seed lives in
``src/config/fund_identities.yaml`` and is hand-maintained: each new fund we
learn to decompose adds one entry plus an issuer adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

DEFAULT_IDENTITIES_PATH = Path(__file__).resolve().parents[2] / "config" / "fund_identities.yaml"


@dataclass(frozen=True, slots=True)
class FundIdentity:
    """Everything needed to fetch one fund's published holdings.

    Attributes:
        issuer: Adapter id, e.g. ``"ishares"`` / ``"vanguard"``.
        product_id: Issuer-specific product id (iShares numeric productId).
        name: Display name of the fund.
        holdings_url: Fully-qualified URL of the holdings file. Stored
            verbatim rather than reconstructed from a template so each
            issuer's quirks stay in data, not code.
        ticker: Fund ticker, informational / used in some filenames.
        aliases: Broker codes and/or ISINs that resolve to this fund.
        exhausted: When set, marks a fund for which we have *searched* for a
            full-holdings source and found none (e.g. an active OEIC that
            discloses only top-10). The text is surfaced as the reason it
            stays primary, distinguishing "we looked, nothing exists" from
            "no adapter built yet".
        proxy_note: For ``issuer: index_proxy`` funds, a human description of
            the proxy used (e.g. "iShares Core MSCI World") shown in the UI so
            the substitution is explicit.
        yahoo_symbol: For ``issuer: top_holdings`` funds, the Yahoo Finance
            symbol (e.g. ``FCBR.L``) whose published top-10 holdings provide a
            partial decomposition.
        as_of: For ``issuer: static_top_holdings`` funds, the disclosure date
            of the hand-seeded top holdings (ISO string), surfaced so the user
            sees how stale the manual data is.
        top_holdings: For ``issuer: static_top_holdings`` funds, the hand-typed
            top holdings as ``(ticker, name, weight_percent)`` triples.

    """

    issuer: str
    product_id: str
    name: str
    holdings_url: str = ""
    ticker: str = ""
    aliases: tuple[str, ...] = ()
    exhausted: str = ""
    proxy_note: str = ""
    yahoo_symbol: str = ""
    as_of: str = ""
    top_holdings: tuple[tuple[str, str, str], ...] = ()

    @property
    def fund_key(self) -> str:
        """Return the canonical ``"{issuer}:{product_id}"`` cache key."""
        return f"{self.issuer}:{self.product_id}"


def _norm(code: str) -> str:
    """Normalise an alias/lookup key: uppercase, strip surrounding space."""
    return code.strip().upper()


def load_fund_identities(
    path: Path | None = None,
) -> dict[str, FundIdentity]:
    """Load the seed and return a lookup keyed by every alias.

    Each fund's ``ticker`` and all ``aliases`` are registered (normalised)
    so a held position can resolve by ISIN or by broker code. Returns an
    empty mapping when the seed file is absent so the dashboard degrades
    gracefully (every fund simply stays primary).

    Args:
        path: Override the default seed location.

    Returns:
        Mapping of normalised alias -> :class:`FundIdentity`.

    """
    seed_path = path or DEFAULT_IDENTITIES_PATH
    if not seed_path.exists():
        return {}
    raw = cast("dict[str, Any]", yaml.safe_load(seed_path.read_text()) or {})
    funds_raw = cast("list[dict[str, Any]]", raw.get("funds") or [])
    lookup: dict[str, FundIdentity] = {}
    for f in funds_raw:
        aliases_raw = cast("list[Any]", f.get("aliases") or [])
        aliases = tuple(str(a) for a in aliases_raw if a)
        th_raw = cast("list[dict[str, Any]]", f.get("top_holdings") or [])
        top_holdings = tuple(
            (str(h.get("ticker") or ""), str(h.get("name") or ""), str(h.get("weight") or ""))
            for h in th_raw
        )
        identity = FundIdentity(
            issuer=str(f.get("issuer") or ""),
            product_id=str(f.get("product_id") or ""),
            name=str(f.get("name") or ""),
            holdings_url=str(f.get("holdings_url") or ""),
            ticker=str(f.get("ticker") or ""),
            aliases=aliases,
            exhausted=str(f.get("exhausted") or ""),
            proxy_note=str(f.get("proxy_note") or ""),
            yahoo_symbol=str(f.get("yahoo_symbol") or ""),
            as_of=str(f.get("as_of") or ""),
            top_holdings=top_holdings,
        )
        keys = {*aliases, identity.ticker}
        for key in keys:
            if key:
                lookup[_norm(key)] = identity
    return lookup


def resolve_identity(
    lookup: dict[str, FundIdentity],
    *candidates: str,
) -> FundIdentity | None:
    """Return the first identity matching any non-empty candidate key.

    Args:
        lookup: Mapping from :func:`load_fund_identities`.
        candidates: Lookup keys to try in order (e.g. ISIN then ticker).

    """
    for cand in candidates:
        if cand:
            hit = lookup.get(_norm(cand))
            if hit is not None:
                return hit
    return None
