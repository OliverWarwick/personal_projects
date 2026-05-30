"""Reconcile replayed AJ Bell transactions against a portfolio snapshot.

PURCHASE and SALE rows are the only ones that move units. Distributions on
accumulation funds appear with ``Quantity=0`` (they are paid in cash on AJ
Bell's platform) so they do not contribute. Equalisation is an accounting
adjustment with no unit effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from src.data.ajbell_models import AJBellTxnKind

if TYPE_CHECKING:
    from src.clients.ajbell.loader import AJBellAccount
    from src.data.ajbell_models import (
        AJBellHolding,
        AJBellTransaction,
    )


@dataclass(frozen=True, slots=True)
class AJBellTickerDelta:
    """Per-instrument comparison between replayed and snapshot unit counts.

    Attributes:
        investment: Cleaned investment name from the snapshot.
        ticker: Exchange ticker when AJ Bell publishes one; ``None`` for
            OEIC funds.
        replayed_units: Sum of signed PURCHASE/SALE quantities.
        snapshot_units: Units reported in the portfolio CSV.
        delta: ``snapshot_units - replayed_units``.

    """

    investment: str
    ticker: str | None
    replayed_units: Decimal
    snapshot_units: Decimal
    delta: Decimal

    @property
    def ok(self) -> bool:
        """Whether the replay matches the snapshot within a tiny tolerance."""
        return abs(self.delta) < Decimal("0.001")


@dataclass(frozen=True, slots=True)
class AJBellReconciliationResult:
    """Outcome of reconciling one AJ Bell account.

    Attributes:
        account_id: AJ Bell account number.
        account_name: Human-readable account label.
        per_ticker: One :class:`AJBellTickerDelta` per holding.
        unmatched_trade_descriptions: Investment names seen in
            PURCHASE/SALE rows that do not correspond to any current
            holding — typically fully-sold positions.

    """

    account_id: str
    account_name: str
    per_ticker: list[AJBellTickerDelta]
    unmatched_trade_descriptions: list[str]

    @property
    def ok(self) -> bool:
        """True when every current holding reconciles."""
        return all(td.ok for td in self.per_ticker)


def _replayed_units(
    transactions: list[AJBellTransaction],
) -> dict[str, Decimal]:
    """Sum signed PURCHASE/SALE quantities, keyed by case-folded description."""
    totals: dict[str, Decimal] = {}
    for txn in transactions:
        if txn.kind not in {AJBellTxnKind.PURCHASE, AJBellTxnKind.SALE}:
            continue
        if txn.quantity is None:
            continue
        key = txn.description.casefold()
        totals[key] = totals.get(key, Decimal(0)) + txn.quantity
    return totals


def reconcile_account(account: AJBellAccount) -> AJBellReconciliationResult:
    """Reconcile one AJ Bell account's transactions against its snapshot.

    Args:
        account: Loaded account with both snapshot and per-account
            transaction slice.

    Returns:
        Per-instrument deltas plus the list of trade-only descriptions
        that did not match any current holding.

    """
    replayed = _replayed_units(account.transactions)
    deltas: list[AJBellTickerDelta] = []
    matched_keys: set[str] = set()
    for h in _holdings(account):
        key = h.investment.casefold()
        replayed_units = replayed.get(key, Decimal(0))
        matched_keys.add(key)
        deltas.append(
            AJBellTickerDelta(
                investment=h.investment,
                ticker=h.ticker,
                replayed_units=replayed_units,
                snapshot_units=h.quantity,
                delta=h.quantity - replayed_units,
            ),
        )
    # Unmatched names: look at the broker-wide ledger so closed positions
    # surface even if their fund is no longer in this account's holdings.
    all_replayed = _replayed_units(account.all_transactions)
    unmatched = sorted(k for k in all_replayed if k not in matched_keys)
    return AJBellReconciliationResult(
        account_id=account.portfolio.account_id,
        account_name=account.portfolio.account_name,
        per_ticker=deltas,
        unmatched_trade_descriptions=unmatched,
    )


def _holdings(account: AJBellAccount) -> list[AJBellHolding]:
    """Return holdings from the account snapshot (small helper for typing)."""
    return account.portfolio.holdings
