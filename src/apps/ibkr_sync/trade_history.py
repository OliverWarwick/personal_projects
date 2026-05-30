"""Closed-position history derived from an IBKR Flex Query response.

Companion to :mod:`portfolio`, which renders the *current* state of an
account. This module reconstructs how the account got there by treating every
closing trade as a :class:`ClosedPosition` (one row per close), and bundles
them per account in a :class:`TradeHistory` with aggregate realised figures
in the account's base currency.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pandas as pd

from src.clients.ibkr import IBKRFlexClient

if TYPE_CHECKING:
    from ibflex.Types import FlexQueryResponse

    from src.apps.ibkr_sync.config import IBKRSyncConfig

_CLOSE_INDICATORS = frozenset({"CLOSE", "OPENCLOSE"})


@dataclass(frozen=True, slots=True)
class ClosedPosition:
    """A single closing event reported by IBKR.

    One row corresponds to one closing trade (or the closing portion of a
    partial open/close). Prices and money fields are denominated in the
    instrument's own currency. ``realized_pnl`` is IBKR's FIFO realised P&L
    for the closed lot; ``cost_basis`` is the open price multiplied by the
    closed quantity, and ``proceeds`` is what the close returned in cash.

    Attributes:
        ticker: Instrument identifier (the ``symbol`` field).
        asset_class: IBKR asset category code (e.g., ``"STK"``, ``"OPT"``),
            or ``None`` if not reported.
        currency: Three-letter ISO currency code the trade settled in.
        quantity: Signed quantity that closed. Negative when closing a long
            (a sell), positive when closing a short (a buy-to-cover).
        open_price: Per-unit price of the originating open lot, if reported.
        close_price: Per-unit execution price of the closing trade.
        open_date: Timestamp of the originating open, if reported.
        close_date: Timestamp of the closing trade.
        cost_basis: Acquisition cost of the closed lot in instrument currency
            (``open_price * abs(quantity)``), if derivable.
        proceeds: Cash returned by the close in instrument currency, if
            reported by IBKR.
        realized_pnl: IBKR FIFO realised P&L for the close, in instrument
            currency, if reported.
        trade_id: ``tradeID`` of the closing trade.
        orig_trade_id: ``tradeID`` of the originating opening trade, if
            reported.

    """

    ticker: str
    asset_class: str | None
    currency: str
    quantity: Decimal
    open_price: Decimal | None
    close_price: Decimal | None
    open_date: datetime | None
    close_date: datetime | None
    cost_basis: Decimal | None
    proceeds: Decimal | None
    realized_pnl: Decimal | None
    trade_id: str
    orig_trade_id: str | None


@dataclass(frozen=True, slots=True)
class TradeHistorySummary:
    """Aggregate realised figures for an account, in the base currency.

    Attributes:
        capital_invested: Total cost basis of closed lots, converted to base
            currency via each row's ``fxRateToBase``. ``None`` if no closed
            row reported a cost basis.
        capital_returned: Total proceeds from closing trades, converted to
            base currency. ``None`` if no closed row reported proceeds.
        realized_pnl: Total realised P&L in base currency, summed from each
            row's ``fifoPnlRealized * fxRateToBase``. ``None`` if no closed
            row reported a realised P&L.

    """

    capital_invested: Decimal | None
    capital_returned: Decimal | None
    realized_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class TradeHistory:
    """Closed-position history for one IBKR account over a statement period.

    One :class:`TradeHistory` is produced per ``FlexStatement`` in the Flex
    response. The ``closed_positions`` list preserves IBKR's reporting order
    so callers can sort or group as they wish.

    Attributes:
        account_id: IBKR account identifier (e.g., ``"U1234567"``).
        base_currency: Three-letter ISO currency code the account reports in,
            used for the figures in :attr:`summary`.
        from_date: Start of the statement window, or ``None`` if the
            statement omits one.
        to_date: End of the statement window, or ``None`` if the statement
            omits one.
        summary: Aggregate realised figures for the account.
        closed_positions: One :class:`ClosedPosition` per closing trade.

    """

    account_id: str
    base_currency: str
    from_date: datetime | None
    to_date: datetime | None
    summary: TradeHistorySummary
    closed_positions: list[ClosedPosition] = field(
        default_factory=list[ClosedPosition]
    )

    def to_dataframe(self) -> pd.DataFrame:
        """Return the closed positions as a :class:`pandas.DataFrame`.

        The frame has one row per :class:`ClosedPosition` with columns
        matching the dataclass fields. Account-level fields and the summary
        are not included; access them on the parent :class:`TradeHistory`
        instead.

        Returns:
            A :class:`pandas.DataFrame` indexed by integer row number, or an
            empty frame with the expected columns when no positions closed.

        """
        if not self.closed_positions:
            columns = list(ClosedPosition.__dataclass_fields__.keys())
            return pd.DataFrame(columns=columns)
        return pd.DataFrame([asdict(p) for p in self.closed_positions])


def get_closed_positions(response: FlexQueryResponse) -> list[TradeHistory]:
    """Build :class:`TradeHistory` snapshots from a parsed Flex response.

    Iterates each ``FlexStatement``, filters its ``Trades`` down to those
    that closed (or partially closed) a position, and produces one
    :class:`TradeHistory` per statement with the closes converted to
    :class:`ClosedPosition` rows plus an aggregate :class:`TradeHistorySummary`
    in base currency.

    A trade qualifies as closing when its ``openCloseIndicator`` is ``CLOSE``
    or ``OPENCLOSE``, or when it carries a non-null ``fifoPnlRealized`` (some
    Flex Query configurations omit the indicator but still report realised
    P&L on closes).

    Args:
        response: The typed Flex response returned by
            :meth:`IBKRFlexClient.download_and_parse` or
            :meth:`IBKRFlexClient.parse_xml`.

    Returns:
        One :class:`TradeHistory` per ``FlexStatement``. Empty when the
        response contains no statements.

    """
    histories: list[TradeHistory] = []
    for stmt in response.FlexStatements:
        closing_trades = [
            trade
            for trade in (getattr(stmt, "Trades", None) or ())
            if _is_closing(trade)
        ]
        positions = [_to_closed_position(trade) for trade in closing_trades]
        summary = _summarise(closing_trades)
        histories.append(
            TradeHistory(
                account_id=str(getattr(stmt, "accountId", "") or ""),
                base_currency=_resolve_base_currency(stmt),
                from_date=_to_datetime(getattr(stmt, "fromDate", None)),
                to_date=_to_datetime(getattr(stmt, "toDate", None)),
                summary=summary,
                closed_positions=positions,
            )
        )
    return histories


def fetch_closed_positions(
    config: IBKRSyncConfig,
    query_id: str | None = None,
) -> list[TradeHistory]:
    """Download an activity Flex Query and return the closed-position history.

    Resolves the Flex Web Service token and Activity Flex Query ID from the
    supplied :class:`IBKRSyncConfig` (Trades live in the activity query),
    downloads the statement, and converts it via :func:`get_closed_positions`.

    Args:
        config: Loaded :class:`IBKRSyncConfig` providing the token and the
            default Activity Flex Query ID.
        query_id: Optional override for the Flex Query ID. When omitted,
            falls back to ``config.activity_query_id``.

    Returns:
        One :class:`TradeHistory` per ``FlexStatement`` returned by the Flex
        Web Service.

    Raises:
        RuntimeError: If no Flex Web Service token is configured, or no
            activity query ID is supplied or configured.

    """
    if not config.token:
        raise RuntimeError(
            "IBKR Flex token is not configured (set IBKR_FLEX_TOKEN or token: in YAML)"
        )
    qid = query_id or config.activity_query_id
    if not qid:
        raise RuntimeError(
            "No activity Flex Query ID provided "
            "(set IBKR_FLEX_ACTIVITY_QUERY or pass query_id)"
        )
    response = IBKRFlexClient(config.token).download_and_parse(qid)
    return get_closed_positions(response)


def _is_closing(trade: Any) -> bool:
    """Return ``True`` if a trade row represents a close.

    A trade is treated as closing when IBKR's ``openCloseIndicator`` is
    ``CLOSE`` or ``OPENCLOSE``, or when ``fifoPnlRealized`` is present
    (covering Flex Queries that omit the indicator).

    Args:
        trade: An ibflex ``Trade`` instance.

    Returns:
        ``True`` if the trade closed any quantity, ``False`` otherwise.

    """
    indicator = _enum_name(getattr(trade, "openCloseIndicator", None))
    if indicator and indicator.upper() in _CLOSE_INDICATORS:
        return True
    return getattr(trade, "fifoPnlRealized", None) is not None


def _to_closed_position(trade: Any) -> ClosedPosition:
    """Convert a single closing ``Trade`` row into a :class:`ClosedPosition`.

    Args:
        trade: An ibflex ``Trade`` instance that has been pre-filtered to
            closing trades by :func:`_is_closing`.

    Returns:
        The equivalent :class:`ClosedPosition`. Missing optional values
        become ``None``; ``cost_basis`` is computed from ``origTradePrice``
        when IBKR does not report it directly.

    """
    quantity = _to_decimal(getattr(trade, "quantity", None)) or Decimal(0)
    open_price = _to_decimal(getattr(trade, "origTradePrice", None))
    close_price = _to_decimal(getattr(trade, "tradePrice", None))
    proceeds = _to_decimal(getattr(trade, "proceeds", None))
    cost_basis = (
        open_price * abs(quantity) if open_price is not None else None
    )
    return ClosedPosition(
        ticker=str(getattr(trade, "symbol", "") or ""),
        asset_class=_enum_name(getattr(trade, "assetCategory", None)),
        currency=str(getattr(trade, "currency", "") or ""),
        quantity=quantity,
        open_price=open_price,
        close_price=close_price,
        open_date=_to_datetime(getattr(trade, "openDateTime", None)),
        close_date=_to_datetime(getattr(trade, "tradeDate", None)),
        cost_basis=cost_basis,
        proceeds=proceeds,
        realized_pnl=_to_decimal(getattr(trade, "fifoPnlRealized", None)),
        trade_id=str(getattr(trade, "tradeID", "") or ""),
        orig_trade_id=_opt_str(getattr(trade, "origTradeID", None)),
    )


def _summarise(trades: list[Any]) -> TradeHistorySummary:
    """Aggregate closing trades into a base-currency summary.

    Each trade's instrument-currency figures are converted to the account's
    base currency by multiplying by its own ``fxRateToBase``. ``proceeds``
    in base currency prefers ``netCashInBase`` when supplied.

    Args:
        trades: List of ibflex ``Trade`` rows already filtered to closes.

    Returns:
        A :class:`TradeHistorySummary` whose fields are ``None`` when no
        contributing trade reported the underlying value.

    """
    capital_invested: Decimal | None = None
    capital_returned: Decimal | None = None
    realized_pnl: Decimal | None = None

    for trade in trades:
        fx = _to_decimal(getattr(trade, "fxRateToBase", None)) or Decimal(1)

        open_price = _to_decimal(getattr(trade, "origTradePrice", None))
        quantity = _to_decimal(getattr(trade, "quantity", None))
        if open_price is not None and quantity is not None:
            capital_invested = (capital_invested or Decimal(0)) + (
                open_price * abs(quantity) * fx
            )

        proceeds_base = _to_decimal(getattr(trade, "netCashInBase", None))
        if proceeds_base is None:
            proceeds_local = _to_decimal(getattr(trade, "proceeds", None))
            if proceeds_local is not None:
                proceeds_base = proceeds_local * fx
        if proceeds_base is not None:
            capital_returned = (capital_returned or Decimal(0)) + proceeds_base

        pnl_local = _to_decimal(getattr(trade, "fifoPnlRealized", None))
        if pnl_local is not None:
            realized_pnl = (realized_pnl or Decimal(0)) + pnl_local * fx

    return TradeHistorySummary(
        capital_invested=capital_invested,
        capital_returned=capital_returned,
        realized_pnl=realized_pnl,
    )


def _resolve_base_currency(stmt: Any) -> str:
    """Determine the account's base currency for a statement.

    Falls back to the first ``EquitySummaryInBase`` row's currency, then to
    an empty string when no source on the statement reveals it.

    Args:
        stmt: An ibflex ``FlexStatement`` instance.

    Returns:
        The base currency code, or an empty string when not derivable.

    """
    direct = getattr(stmt, "currency", None)
    if direct:
        return str(direct)
    summaries = getattr(stmt, "EquitySummaryInBase", None) or ()
    for summary in summaries:
        ccy = getattr(summary, "currency", None)
        if ccy:
            return str(ccy)
    return ""


def _opt_str(value: Any) -> str | None:
    """Return ``value`` coerced to ``str``, or ``None`` if blank.

    Args:
        value: Any object; typically ``str``, ``None``, or an ibflex enum.

    Returns:
        The string representation, or ``None`` for ``None`` and empty values.

    """
    if value is None:
        return None
    text = str(value)
    return text or None


def _to_decimal(value: Any) -> Decimal | None:
    """Coerce a numeric or string value to :class:`Decimal`.

    Args:
        value: A ``Decimal``, number, numeric string, or ``None``.

    Returns:
        The coerced :class:`Decimal`, or ``None`` if the input is ``None``.

    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _to_datetime(value: Any) -> datetime | None:
    """Coerce an ibflex date/datetime to a timezone-aware :class:`datetime`.

    Args:
        value: A ``datetime``, ``date``, or ``None``. Naive datetimes are
            assumed to be UTC.

    Returns:
        A timezone-aware UTC datetime, or ``None`` if the input is ``None``.

    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    return None


def _enum_name(value: Any) -> str | None:
    """Return the ``.name`` attribute of an enum-like value.

    Args:
        value: An enum instance, plain string, or ``None``.

    Returns:
        The enum name, the stringified value, or ``None``.

    """
    if value is None:
        return None
    name = getattr(value, "name", None)
    if name:
        return str(name)
    text = str(value)
    return text or None
