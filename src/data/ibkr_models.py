"""SQLAlchemy models for Interactive Brokers (IBKR) Flex statement persistence.

This module defines the database schema for storing data parsed from IBKR Flex
Query XML statements: account snapshots (NAV/cash), executed trades, end-of-day
positions, and cash transactions (dividends, taxes, deposits, withdrawals).

The models share the same declarative ``Base`` as the bank-data models, so a
single ``Base.metadata.create_all(engine)`` call materialises both schemas on
one engine when desired.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models import Base


class IBKRAccountSnapshot(Base):
    """Point-in-time snapshot of an IBKR account's headline figures.

    Captures the net asset value and cash balance for an account on a given
    date, typically derived from the ``EquitySummaryInBase`` section of an
    IBKR Flex Query report.

    Attributes:
        id: Auto-incrementing primary key.
        account_id: IBKR account identifier (e.g., "U1234567").
        currency: Three-letter ISO currency code the figures are denominated in.
        nav: Net asset value of the account at the snapshot date, if reported.
        cash_balance: Settled cash balance at the snapshot date, if reported.
        snapshot_date: Timestamp the snapshot represents.

    """

    __tablename__ = "ibkr_account_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(50), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    nav: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    cash_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    snapshot_date: Mapped[datetime] = mapped_column(DateTime)


class IBKRTrade(Base):
    """Executed trade reported by IBKR.

    Each row corresponds to a single ``Trade`` element in a Flex Query report,
    keyed by IBKR's globally unique ``tradeID``.

    Attributes:
        trade_id: IBKR ``tradeID``; primary key.
        account_id: IBKR account the trade was executed in.
        symbol: Instrument symbol as reported by IBKR.
        asset_class: IBKR asset class code (e.g., "STK", "OPT", "FUT"), if known.
        currency: Three-letter ISO currency code the trade settled in.
        quantity: Signed quantity traded (positive for buys, negative for sells).
        price: Execution price per unit.
        proceeds: Gross proceeds of the trade, if reported.
        commission: Commission paid (typically negative), if reported.
        buy_sell: Side of the trade, "BUY" or "SELL".
        trade_date: Timestamp of trade execution.
        open_close: Whether the trade ``OPEN``ed, ``CLOSE``d, or partially
            opened-and-closed (``OPENCLOSE``) a position. ``None`` when IBKR
            does not classify the trade.
        realized_pnl: FIFO realised P&L on the closing portion of the trade,
            in ``currency``. ``None`` for pure opens or when IBKR omits it.
        open_date: For closing trades, the timestamp of the originating open
            (``openDateTime``). Used to compute holding period.
        orig_trade_id: ``tradeID`` of the originating opening trade for the
            lot this row closes, if reported.

    """

    __tablename__ = "ibkr_trades"

    trade_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(50), index=True)
    symbol: Mapped[str] = mapped_column(String(50))
    asset_class: Mapped[str | None] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    proceeds: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    commission: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    buy_sell: Mapped[str] = mapped_column(String(10))
    trade_date: Mapped[datetime] = mapped_column(DateTime)
    open_close: Mapped[str | None] = mapped_column(String(20))
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    open_date: Mapped[datetime | None] = mapped_column(DateTime)
    orig_trade_id: Mapped[str | None] = mapped_column(String(100), index=True)


class IBKRPosition(Base):
    """End-of-day open position held in an IBKR account.

    Mirrors a single ``OpenPosition`` row in a Flex Query report. A new row is
    persisted per snapshot date so historical position series can be
    reconstructed.

    Attributes:
        id: Auto-incrementing primary key.
        account_id: IBKR account holding the position.
        symbol: Instrument symbol as reported by IBKR.
        asset_class: IBKR asset class code (e.g., "STK", "OPT"), if known.
        currency: Three-letter ISO currency code the position is denominated in.
        quantity: Signed position size (positive long, negative short).
        mark_price: Mark price used to value the position, if reported.
        position_value: Mark-to-market value of the position, if reported.
        snapshot_date: Timestamp the snapshot represents.

    """

    __tablename__ = "ibkr_positions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(50), index=True)
    symbol: Mapped[str] = mapped_column(String(50), index=True)
    asset_class: Mapped[str | None] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    mark_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    position_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    snapshot_date: Mapped[datetime] = mapped_column(DateTime)


class IBKRCashTransaction(Base):
    """Cash movement on an IBKR account.

    Represents one row from the ``CashTransaction`` section of a Flex Query
    report - dividends, withholding taxes, deposits, withdrawals, fees, and
    similar non-trade cash events. Keyed by IBKR's ``transactionID``.

    The Python attribute is ``type_`` (trailing underscore) to avoid shadowing
    the built-in ``type`` and the ruff A003 rule, while the database column
    keeps the natural name ``type``.

    Attributes:
        transaction_id: IBKR ``transactionID``; primary key.
        account_id: IBKR account the cash event occurred in.
        currency: Three-letter ISO currency code of the amount.
        amount: Signed cash amount (credit positive, debit negative).
        type_: IBKR transaction type label (e.g., "Dividends",
            "Withholding Tax", "Deposits/Withdrawals"); stored in column "type".
        description: Free-text narrative provided by IBKR, if any.
        date: Timestamp the cash event posted.

    """

    __tablename__ = "ibkr_cash_transactions"

    transaction_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(50), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    type_: Mapped[str | None] = mapped_column("type", String(50))
    description: Mapped[str | None] = mapped_column(String(500))
    date: Mapped[datetime] = mapped_column(DateTime)
