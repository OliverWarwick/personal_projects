"""Hargreaves Lansdown CSV loader.

HL has no public retail API; users export ``account-summary`` and
``portfolio-summary`` CSVs from the web UI. This package parses those files
into typed records, combines per-tax-year transaction files into one ledger
per account, and reconciles replayed BUY/SELL activity against the holdings
snapshot.
"""

from src.clients.hl.loader import (
    HLAccount,
    discover_accounts,
    load_account,
)
from src.clients.hl.parser import (
    parse_account_summary,
    parse_portfolio_summary,
)
from src.clients.hl.reconcile import (
    ReconciliationResult,
    TickerDelta,
    reconcile_account,
)

__all__ = [
    "HLAccount",
    "ReconciliationResult",
    "TickerDelta",
    "discover_accounts",
    "load_account",
    "parse_account_summary",
    "parse_portfolio_summary",
    "reconcile_account",
]
