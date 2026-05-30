"""Data models for AJ Bell CSV exports.

AJ Bell publishes two CSV shapes from the customer portal:

* ``portfolio-<account-id>-<account-name>.csv`` – holdings snapshot for one
  account, including a ``Cash <CCY>`` row.
* ``transactionhistory.csv`` – chronological transaction ledger covering
  every account the export was generated for. Rows do not carry an
  account identifier, so reconciliation pairs transactions to portfolios
  via the instrument description.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date
    from decimal import Decimal


class AJBellTxnKind(StrEnum):
    """Classified AJ Bell transaction type derived from the ``Transaction`` column.

    ``PURCHASE``/``SALE`` are the only kinds that change unit counts; the
    rest move cash or are accounting adjustments.
    """

    PURCHASE = "PURCHASE"
    SALE = "SALE"
    DISTRIBUTION = "DISTRIBUTION"
    EQUALISATION = "EQUALISATION"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class AJBellHolding:
    """One row from an AJ Bell portfolio export.

    Attributes:
        investment: Full investment name. For listed instruments AJ Bell
            appends ``(<EXCHANGE>:<TICKER>)``; the ticker is split into
            :attr:`ticker` so the rest is comparable to OEIC names that
            have no exchange suffix.
        ticker: Exchange-listed symbol when AJ Bell publishes one;
            ``None`` for OEIC funds.
        quantity: Units held. Fractional for OEICs.
        price_gbp: Quoted price in sterling.
        value_gbp: Sterling market value at the snapshot time.
        cost_gbp: Book cost in sterling.
        change_gbp: Unrealised gain or loss in sterling.

    """

    investment: str
    ticker: str | None
    quantity: Decimal
    price_gbp: Decimal
    value_gbp: Decimal
    cost_gbp: Decimal
    change_gbp: Decimal


@dataclass(frozen=True, slots=True)
class AJBellPortfolio:
    """Parsed contents of one ``portfolio-<id>-<name>.csv`` file.

    Attributes:
        account_id: AJ Bell account number (e.g. ``ABBXDDL``).
        account_name: Display name from the filename (e.g. ``Lifetime ISA``).
        snapshot_at: Timestamp from the latest snapshot row.
        stock_value_gbp: Sum of holding values, excluding cash.
        cash_gbp: Cash balance (the ``Cash <CCY>`` row's ``Value``).
        total_value_gbp: ``stock_value_gbp + cash_gbp``.
        holdings: One :class:`AJBellHolding` per non-cash row.

    """

    account_id: str
    account_name: str
    snapshot_at: date | None
    stock_value_gbp: Decimal
    cash_gbp: Decimal
    total_value_gbp: Decimal
    holdings: list[AJBellHolding]


@dataclass(frozen=True, slots=True)
class AJBellTransaction:
    """One row from an AJ Bell ``transactionhistory.csv``.

    Attributes:
        trade_date: Date of the trade or cash movement.
        kind: Classified transaction type.
        description: Cleaned investment name (matches
            :attr:`AJBellHolding.investment` once the exchange suffix has
            been stripped).
        quantity: Signed unit count for PURCHASE/SALE (positive on
            purchase, negative on sale); ``None`` for cash-only rows.
        price_gbp: Sterling price per unit on the trade; ``None`` for
            cash-only rows.
        amount_gbp: Signed sterling cash impact (negative when cash
            leaves the account, positive when it arrives).
        reference: Broker-side reference identifier.
        raw_transaction: Original ``Transaction`` cell, retained for
            debugging when classification falls through to ``OTHER``.

    """

    trade_date: date
    kind: AJBellTxnKind
    description: str
    quantity: Decimal | None
    price_gbp: Decimal | None
    amount_gbp: Decimal
    reference: str
    raw_transaction: str
