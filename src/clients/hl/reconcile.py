"""Reconcile replayed HL transactions against the holdings snapshot.

Buys and sells are the only ledger rows that move units (dividends are paid
in cash for this user). Summing signed BUY/SELL quantities per stock should
therefore match the units reported in the ``account-summary`` snapshot.
A non-zero delta usually points at a share split, transfer-in or corporate
action that's not represented as a regular trade row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from src.data.hl_models import HLTxnKind

if TYPE_CHECKING:
    from src.clients.hl.loader import HLAccount
    from src.data.hl_models import HLHolding, HLTransaction

# HL appends annotations like " *1", " *R", " *2 *4" to the snapshot stock
# name but not to the transaction description.
_ANNOTATION_RE = re.compile(r"\s*\*[A-Za-z0-9]+(?:\s+\*[A-Za-z0-9]+)*\s*$")


def normalise_name(name: str) -> str:
    """Strip HL annotation suffixes and lower-case for matching."""
    stripped = _ANNOTATION_RE.sub("", name).strip()
    return stripped.casefold()


@dataclass(frozen=True, slots=True)
class TickerDelta:
    """Per-stock comparison between replayed and snapshot unit counts.

    Attributes:
        code: HL stock code from the snapshot.
        stock: Snapshot stock name (with annotations stripped).
        replayed_units: Sum of signed BUY/SELL quantities for this stock.
        snapshot_units: Units reported in the ``account-summary`` file.
        delta: ``snapshot_units - replayed_units``. Non-zero means the
            ledger does not fully explain the current position.

    """

    code: str
    stock: str
    replayed_units: Decimal
    snapshot_units: Decimal
    delta: Decimal

    @property
    def ok(self) -> bool:
        """Whether the replay matches the snapshot within a tiny tolerance."""
        return abs(self.delta) < Decimal("0.001")


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Outcome of reconciling one HL account.

    Attributes:
        kind_name: Name of the account kind, for display.
        per_ticker: One :class:`TickerDelta` per holding in the snapshot.
        unmatched_trade_names: Stock names that appear in BUY/SELL rows but
            do not correspond to any current holding (typically positions
            that have been fully sold).

    """

    kind_name: str
    per_ticker: list[TickerDelta]
    unmatched_trade_names: list[str]

    @property
    def ok(self) -> bool:
        """True when every current holding reconciles."""
        return all(td.ok for td in self.per_ticker)


def _replayed_units_by_name(transactions: list[HLTransaction]) -> dict[str, Decimal]:
    """Sum signed BUY/SELL quantities, keyed by normalised stock name."""
    totals: dict[str, Decimal] = {}
    for txn in transactions:
        if txn.kind not in {HLTxnKind.BUY, HLTxnKind.SELL}:
            continue
        if txn.stock_name is None or txn.quantity is None:
            continue
        key = normalise_name(txn.stock_name)
        totals[key] = totals.get(key, Decimal(0)) + txn.quantity
    return totals


def reconcile_account(account: HLAccount) -> ReconciliationResult:
    """Reconcile one HL account's transactions against its snapshot.

    Args:
        account: Loaded account with both snapshot and combined ledger.

    Returns:
        Per-ticker deltas plus the list of trade-only stock names that did
        not match any current holding.

    """
    replayed = _replayed_units_by_name(account.transactions)
    deltas: list[TickerDelta] = []
    matched_keys: set[str] = set()
    for h in _holdings(account):
        key = normalise_name(h.stock)
        replayed_units = replayed.get(key, Decimal(0))
        matched_keys.add(key)
        deltas.append(
            TickerDelta(
                code=h.code,
                stock=_ANNOTATION_RE.sub("", h.stock).strip(),
                replayed_units=replayed_units,
                snapshot_units=h.units,
                delta=h.units - replayed_units,
            ),
        )
    unmatched = sorted(k for k in replayed if k not in matched_keys)
    return ReconciliationResult(
        kind_name=account.kind.value,
        per_ticker=deltas,
        unmatched_trade_names=unmatched,
    )


def _holdings(account: HLAccount) -> list[HLHolding]:
    """Return holdings from the account snapshot (small helper for typing)."""
    return account.summary.holdings
