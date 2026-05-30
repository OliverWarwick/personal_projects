"""Discover and load AJ Bell exports from a local directory.

The expected layout is ``$AJBELL_DATA_DIR/`` containing one or more
``portfolio-<id>-<name>.csv`` snapshots plus a single
``transactionhistory.csv`` shared across every account in the export.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from src.clients.ajbell.parser import (
    parse_portfolio,
    parse_transaction_history,
)

if TYPE_CHECKING:
    from src.data.ajbell_models import (
        AJBellPortfolio,
        AJBellTransaction,
    )

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AJBellAccount:
    """One AJ Bell account: snapshot + transactions relevant to it.

    Attributes:
        portfolio: Parsed ``portfolio-*.csv`` snapshot.
        transactions: Transactions whose description matches one of the
            account's holdings. The raw ledger is broker-wide, so this
            slice is what reconciliation should use.
        all_transactions: The full ledger as parsed, for callers that
            want to see cross-account context or unmatched rows.

    """

    portfolio: AJBellPortfolio
    transactions: list[AJBellTransaction]
    all_transactions: list[AJBellTransaction]


def ajbell_data_dir() -> Path:
    """Resolve the AJ Bell data directory from ``AJBELL_DATA_DIR``.

    Returns:
        Expanded :class:`~pathlib.Path` pointing at the export root.

    Raises:
        FileNotFoundError: If the env var is unset and no default exists.

    """
    raw = os.environ.get("AJBELL_DATA_DIR") or "~/dataprod/portfolio/ajbell"
    path = Path(raw).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"AJBELL_DATA_DIR points to a non-existent path: {path}",
        )
    return path


def _find_portfolios(root: Path) -> list[Path]:
    """Return every ``portfolio-*.csv`` file in ``root``."""
    return sorted(root.glob("portfolio-*.csv"))


def _find_transaction_history(root: Path) -> Path:
    """Return the single ``transactionhistory.csv`` in ``root``."""
    candidates = sorted(root.glob("transactionhistory*.csv"))
    if not candidates:
        raise FileNotFoundError(f"No transactionhistory*.csv in {root}")
    if len(candidates) > 1:
        logger.warning(
            "multiple transactionhistory files in %s, using %s",
            root,
            candidates[-1].name,
        )
    return candidates[-1]


def load_accounts(root: Path | None = None) -> list[AJBellAccount]:
    """Load every AJ Bell account present under ``root``.

    Args:
        root: Override for the data directory. Defaults to
            ``$AJBELL_DATA_DIR``.

    Returns:
        One :class:`AJBellAccount` per ``portfolio-*.csv`` file. The
        broker-wide transaction history is sliced per account by
        matching each transaction's description against the account's
        holdings (case-insensitive, exact match on the cleaned
        investment name).

    """
    base = root if root is not None else ajbell_data_dir()
    portfolios = [parse_portfolio(p) for p in _find_portfolios(base)]
    if not portfolios:
        logger.warning("no portfolio-*.csv files in %s", base)
        return []
    history = parse_transaction_history(_find_transaction_history(base))

    accounts: list[AJBellAccount] = []
    for portfolio in portfolios:
        holding_names = {h.investment.casefold() for h in portfolio.holdings}
        slice_ = [
            t for t in history if t.description.casefold() in holding_names
        ]
        accounts.append(
            AJBellAccount(
                portfolio=portfolio,
                transactions=slice_,
                all_transactions=history,
            ),
        )
    return accounts


def discover_accounts(root: Path | None = None) -> list[AJBellAccount]:
    """Public alias for :func:`load_accounts` mirroring the HL loader."""
    return load_accounts(root)
