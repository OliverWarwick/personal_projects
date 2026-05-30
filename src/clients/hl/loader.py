"""Discover and load Hargreaves Lansdown exports from a local directory.

The expected layout is ``$HL_DATA_DIR/{ISA,SIPP,SHARES}/`` containing one
``account-summary-*.csv`` snapshot and one or more ``portfolio-summary-*.csv``
ledgers (HL splits these per tax year).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from src.clients.hl.parser import (
    parse_account_summary,
    parse_portfolio_summary,
)
from src.data.hl_models import HLAccountKind

if TYPE_CHECKING:
    from src.data.hl_models import HLAccountSummary, HLTransaction

logger = logging.getLogger(__name__)

_FOLDER_BY_KIND: dict[HLAccountKind, str] = {
    HLAccountKind.ISA: "ISA",
    HLAccountKind.SIPP: "SIPP",
    HLAccountKind.SHARES: "SHARES",
}


@dataclass(frozen=True, slots=True)
class HLAccount:
    """One fully-loaded HL account: snapshot + combined transaction ledger.

    Attributes:
        kind: Which account this is.
        summary: Parsed ``account-summary`` snapshot.
        transactions: Combined ledger from every ``portfolio-summary`` file,
            sorted oldest-to-newest and deduplicated on ``reference`` so
            overlapping per-tax-year exports do not double-count trades.

    """

    kind: HLAccountKind
    summary: HLAccountSummary
    transactions: list[HLTransaction]


def hl_data_dir() -> Path:
    """Resolve the HL data directory from ``HL_DATA_DIR``.

    Returns:
        Expanded :class:`~pathlib.Path` pointing at the HL exports root.

    Raises:
        FileNotFoundError: If the env var is unset and no default exists.

    """
    raw = os.environ.get("HL_DATA_DIR") or "~/dataprod/portfolio/hl"
    path = Path(raw).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"HL_DATA_DIR points to a non-existent path: {path}",
        )
    return path


def _find_account_summary(folder: Path) -> Path:
    """Return the single ``account-summary-*.csv`` file inside ``folder``."""
    candidates = sorted(folder.glob("account-summary*.csv"))
    if not candidates:
        raise FileNotFoundError(f"No account-summary*.csv in {folder}")
    if len(candidates) > 1:
        logger.warning(
            "multiple account-summary files in %s, using %s",
            folder,
            candidates[-1].name,
        )
    return candidates[-1]


def _find_portfolio_summaries(folder: Path) -> list[Path]:
    """Return every ``portfolio-summary-*.csv`` file inside ``folder``."""
    return sorted(folder.glob("portfolio-summary*.csv"))


def _combine_transactions(paths: list[Path]) -> list[HLTransaction]:
    """Parse and merge multiple portfolio-summary files for one account.

    Deduplicates on ``reference`` (HL reuses references across the overlapping
    "current" and per-tax-year exports). Cash rows whose reference is a
    generic label like ``INTEREST`` are deduped on the full
    ``(trade_date, reference, value_gbp)`` tuple instead, since the reference
    alone is not unique.
    """
    seen_trade_refs: set[str] = set()
    seen_cash_keys: set[tuple[str, str, str]] = set()
    combined: list[HLTransaction] = []
    for path in paths:
        for txn in parse_portfolio_summary(path):
            ref = txn.reference
            is_trade = ref[:1] in {"B", "S"} and ref[1:].isdigit()
            if is_trade:
                if ref in seen_trade_refs:
                    continue
                seen_trade_refs.add(ref)
            else:
                key = (txn.trade_date.isoformat(), ref, str(txn.value_gbp))
                if key in seen_cash_keys:
                    continue
                seen_cash_keys.add(key)
            combined.append(txn)
    combined.sort(key=lambda t: (t.trade_date, t.reference))
    return combined


def load_account(root: Path, kind: HLAccountKind) -> HLAccount:
    """Load one HL account from ``root/<KIND>/``.

    Args:
        root: Root HL data directory (typically ``$HL_DATA_DIR``).
        kind: Which account to load.

    Returns:
        Fully populated :class:`HLAccount`.

    """
    folder = root / _FOLDER_BY_KIND[kind]
    if not folder.exists():
        raise FileNotFoundError(f"Missing HL account folder: {folder}")
    summary = parse_account_summary(_find_account_summary(folder), kind)
    transactions = _combine_transactions(_find_portfolio_summaries(folder))
    return HLAccount(kind=kind, summary=summary, transactions=transactions)


def discover_accounts(root: Path | None = None) -> list[HLAccount]:
    """Load all HL accounts present under ``root`` (defaulting to ``$HL_DATA_DIR``).

    Missing per-account folders are skipped with a warning rather than raised
    so users can run with a partial export.
    """
    base = root if root is not None else hl_data_dir()
    out: list[HLAccount] = []
    for kind in HLAccountKind:
        folder = base / _FOLDER_BY_KIND[kind]
        if not folder.exists():
            logger.warning("HL account folder %s not found, skipping", folder)
            continue
        out.append(load_account(base, kind))
    return out
