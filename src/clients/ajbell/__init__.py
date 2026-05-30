"""AJ Bell CSV loader.

AJ Bell has no public retail API; users export portfolio snapshots and a
transaction history CSV from the web portal. This package parses those
files into typed records and reconciles replayed Purchase/Sale activity
against the holdings snapshot.
"""

from src.clients.ajbell.loader import (
    AJBellAccount,
    discover_accounts,
    load_accounts,
)
from src.clients.ajbell.parser import (
    parse_portfolio,
    parse_transaction_history,
)
from src.clients.ajbell.reconcile import (
    AJBellReconciliationResult,
    AJBellTickerDelta,
    reconcile_account,
)

__all__ = [
    "AJBellAccount",
    "AJBellReconciliationResult",
    "AJBellTickerDelta",
    "discover_accounts",
    "load_accounts",
    "parse_portfolio",
    "parse_transaction_history",
    "reconcile_account",
]
