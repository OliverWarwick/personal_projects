"""Data models for Hargreaves Lansdown CSV exports.

Mirrors the two CSV shapes published by the HL web UI:

* ``account-summary-*.csv`` – a point-in-time snapshot of holdings, cash and
  total value for one account.
* ``portfolio-summary-*.csv`` – the chronological cash and trade ledger for
  one account, optionally split per tax year.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date
    from decimal import Decimal


class HLAccountKind(StrEnum):
    """Which kind of HL account a folder represents."""

    ISA = "ISA"
    SIPP = "SIPP"
    SHARES = "SHARES"


class HLTxnKind(StrEnum):
    """Classified transaction type derived from the HL ``Reference`` column.

    ``BUY``/``SELL`` are the only kinds that change unit counts; all others
    are cash-only movements.
    """

    BUY = "BUY"
    SELL = "SELL"
    REDEMPTION = "REDEMPTION"
    INTEREST = "INTEREST"
    MANAGE_FEE = "MANAGE_FEE"
    DEPOSIT = "DEPOSIT"
    TRANSFER = "TRANSFER"
    DIVIDEND = "DIVIDEND"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class HLHolding:
    """One row from an HL ``account-summary`` holdings table.

    Attributes:
        code: HL stock code (ticker for listed shares, SEDOL-like alphanumeric
            for funds, e.g. ``BMF4SW8``).
        stock: Full stock name including any HL annotation suffixes such as
            ``*1`` (last business day close) or ``*R`` (price converted to
            sterling).
        units: Number of units held. May be fractional for OEIC funds.
        price_pence: Quoted bid price in pence.
        value_gbp: Sterling market value at the snapshot time.
        cost_gbp: Book cost in sterling.
        gain_gbp: Unrealised gain or loss in sterling.

    """

    code: str
    stock: str
    units: Decimal
    price_pence: Decimal
    value_gbp: Decimal
    cost_gbp: Decimal
    gain_gbp: Decimal


@dataclass(frozen=True, slots=True)
class HLAccountSummary:
    """Parsed contents of an HL ``account-summary-*.csv`` file.

    Attributes:
        kind: Account kind (ISA, SIPP or Fund & Share).
        client_number: HL client number (shared across all accounts).
        snapshot_at: Timestamp embedded in the ``Spreadsheet created at`` row.
        stock_value_gbp: Total market value of holdings.
        cash_gbp: Total cash balance.
        total_value_gbp: ``stock_value_gbp + cash_gbp`` as reported by HL.
        holdings: One :class:`HLHolding` per stock line in the holdings table.

    """

    kind: HLAccountKind
    client_number: str
    snapshot_at: date
    stock_value_gbp: Decimal
    cash_gbp: Decimal
    total_value_gbp: Decimal
    holdings: list[HLHolding]


@dataclass(frozen=True, slots=True)
class HLTransaction:
    """One row from an HL ``portfolio-summary`` ledger.

    For BUY/SELL rows the description encodes ``<stock name> <qty> @ <unit
    price>``; ``stock_name``, ``quantity`` and ``unit_cost_pence`` carry the
    parsed values. For cash-only rows those three fields are ``None``.

    Attributes:
        trade_date: Date the trade or cash movement was booked.
        settle_date: Settlement date (often equal to ``trade_date`` for cash
            rows).
        reference: HL reference code. Buys are prefixed ``B``, sells ``S``;
            other values include ``INTEREST``, ``MANAGE FEE``, ``CARD WEB``,
            ``FPC``, ``Transfer``.
        description: Raw description string as exported.
        kind: Classified transaction type.
        stock_name: Cleaned stock name for BUY/SELL rows; ``None`` otherwise.
        quantity: Signed unit count (positive for BUY, negative for SELL);
            ``None`` for cash-only rows.
        unit_cost_pence: Price paid or received per unit in pence; ``None``
            for cash-only rows.
        value_gbp: Sterling cash impact on the account (negative when cash
            leaves, positive when it arrives).

    """

    trade_date: date
    settle_date: date
    reference: str
    description: str
    kind: HLTxnKind
    stock_name: str | None
    quantity: Decimal | None
    unit_cost_pence: Decimal | None
    value_gbp: Decimal
