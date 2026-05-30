"""Service layer for IBKR Flex statement synchronisation.

Downloads Flex Query XML via :class:`IBKRFlexClient`, parses it into the typed
``ibflex`` model, and persists the relevant rows (trades, cash transactions,
open positions, account snapshots) to the local database.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.clients.ibkr import IBKRFlexClient
from src.data.ibkr_models import (
    IBKRAccountSnapshot,
    IBKRCashTransaction,
    IBKRPosition,
    IBKRTrade,
)
from src.data.models import Base

if TYPE_CHECKING:
    from ibflex.Types import FlexQueryResponse

    from src.apps.ibkr_sync.config import IBKRSyncConfig

logger = logging.getLogger(__name__)


class IBKRSyncService:
    """Orchestrate Flex statement downloads and database persistence.

    On construction the service materialises any missing tables on the
    configured engine. Each ``sync_*`` method downloads one Flex Query,
    iterates the parsed response, and upserts rows keyed by their natural
    IBKR identifiers (``tradeID``, ``transactionID``). Position and snapshot
    rows have no natural key and are appended per run, so historical
    snapshots can be reconstructed from the database.
    """

    def __init__(self, config: IBKRSyncConfig) -> None:
        """Initialise the service and ensure the schema exists.

        Args:
            config: Loaded :class:`IBKRSyncConfig` instance.

        """
        self.config = config
        self.engine = create_engine(config.database_url)
        self.Session = sessionmaker(bind=self.engine)
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create any missing tables on the configured engine."""
        Base.metadata.create_all(self.engine)

    def _get_client(self) -> IBKRFlexClient:
        """Return a configured Flex client.

        Returns:
            A new :class:`IBKRFlexClient` constructed with the resolved token.

        Raises:
            RuntimeError: If no Flex Web Service token has been configured.

        """
        if not self.config.token:
            raise RuntimeError(
                "IBKR Flex token is not configured (set IBKR_FLEX_TOKEN or token: in YAML)"
            )
        return IBKRFlexClient(self.config.token)

    def sync_activity(
        self,
        query_id: str | None = None,
        *,
        response: FlexQueryResponse | None = None,
    ) -> dict[str, int]:
        """Persist trades and cash transactions from an activity Flex Query.

        Args:
            query_id: Optional Flex Query ID. When omitted, falls back to
                ``config.activity_query_id``. Ignored when ``response`` is
                supplied.
            response: Optional pre-parsed :class:`FlexQueryResponse` to use
                instead of downloading from IBKR. Useful for replaying a
                saved XML payload during development or while throttled.

        Returns:
            A dictionary with ``trades`` and ``cash_transactions`` counts of
            new rows inserted.

        Raises:
            RuntimeError: If no ``response`` is supplied and no query ID is
                supplied or configured.

        """
        if response is None:
            qid = query_id or self.config.activity_query_id
            if not qid:
                raise RuntimeError(
                    "No activity Flex Query ID provided "
                    "(set IBKR_FLEX_ACTIVITY_QUERY or pass --query-id)"
                )
            logger.info("Syncing IBKR activity from Flex Query %s", qid)
            response = self._get_client().download_and_parse(qid)
        else:
            logger.info("Syncing IBKR activity from supplied response")

        stats = {"trades": 0, "cash_transactions": 0}
        with self.Session() as session:
            for stmt in response.FlexStatements:
                stats["trades"] += self._persist_trades(session, getattr(stmt, "Trades", None))
                stats["cash_transactions"] += self._persist_cash_transactions(
                    session, getattr(stmt, "CashTransactions", None)
                )
            session.commit()

        logger.info("Activity sync complete: %s", stats)
        return stats

    def sync_portfolio(
        self,
        query_id: str | None = None,
        *,
        response: FlexQueryResponse | None = None,
    ) -> dict[str, int]:
        """Persist positions and account snapshots from a portfolio Flex Query.

        Args:
            query_id: Optional Flex Query ID. When omitted, falls back to
                ``config.portfolio_query_id``. Ignored when ``response`` is
                supplied.
            response: Optional pre-parsed :class:`FlexQueryResponse` to use
                instead of downloading from IBKR.

        Returns:
            A dictionary with ``positions`` and ``snapshots`` counts of rows
            inserted.

        Raises:
            RuntimeError: If no ``response`` is supplied and no query ID is
                supplied or configured.

        """
        if response is None:
            qid = query_id or self.config.portfolio_query_id
            if not qid:
                raise RuntimeError(
                    "No portfolio Flex Query ID provided "
                    "(set IBKR_FLEX_PORTFOLIO_QUERY or pass --query-id)"
                )
            logger.info("Syncing IBKR portfolio from Flex Query %s", qid)
            response = self._get_client().download_and_parse(qid)
        else:
            logger.info("Syncing IBKR portfolio from supplied response")

        fallback_ts = datetime.now(UTC)
        stats = {"positions": 0, "snapshots": 0}
        with self.Session() as session:
            for stmt in response.FlexStatements:
                acct_id = str(getattr(stmt, "accountId", "") or "")
                stats["snapshots"] += self._persist_snapshots(
                    session,
                    getattr(stmt, "EquitySummaryInBase", None),
                    fallback_account=acct_id,
                    fallback_ts=fallback_ts,
                )
                stats["positions"] += self._persist_positions(
                    session,
                    getattr(stmt, "OpenPositions", None),
                    fallback_account=acct_id,
                    fallback_ts=fallback_ts,
                )
            session.commit()

        logger.info("Portfolio sync complete: %s", stats)
        return stats

    @staticmethod
    def _persist_trades(session: Any, trades: Any) -> int:
        """Insert any new ``Trade`` rows from the parsed statement.

        Args:
            session: An active SQLAlchemy session.
            trades: Iterable of ibflex ``Trade`` objects, or ``None``.

        Returns:
            The number of new rows inserted.

        """
        added = 0
        for trade in trades or ():
            tid = _opt_str(getattr(trade, "tradeID", None))
            if tid is None or session.get(IBKRTrade, tid) is not None:
                continue
            session.add(
                IBKRTrade(
                    trade_id=tid,
                    account_id=_opt_str(getattr(trade, "accountId", None)) or "",
                    symbol=_opt_str(getattr(trade, "symbol", None)) or "",
                    asset_class=_enum_name(getattr(trade, "assetCategory", None)),
                    currency=_opt_str(getattr(trade, "currency", None)) or "",
                    quantity=_to_decimal(getattr(trade, "quantity", None)) or Decimal(0),
                    price=_to_decimal(getattr(trade, "tradePrice", None)) or Decimal(0),
                    proceeds=_to_decimal(getattr(trade, "proceeds", None)),
                    commission=_to_decimal(getattr(trade, "ibCommission", None)),
                    buy_sell=_enum_name(getattr(trade, "buySell", None)) or "",
                    trade_date=_to_datetime(getattr(trade, "tradeDate", None))
                    or datetime.now(UTC),
                    open_close=_enum_name(getattr(trade, "openCloseIndicator", None)),
                    realized_pnl=_to_decimal(getattr(trade, "fifoPnlRealized", None)),
                    open_date=_to_datetime(getattr(trade, "openDateTime", None)),
                    orig_trade_id=_opt_str(getattr(trade, "origTradeID", None)),
                )
            )
            added += 1
        return added

    @staticmethod
    def _persist_cash_transactions(session: Any, cash_txs: Any) -> int:
        """Insert any new ``CashTransaction`` rows from the parsed statement.

        Args:
            session: An active SQLAlchemy session.
            cash_txs: Iterable of ibflex ``CashTransaction`` objects, or ``None``.

        Returns:
            The number of new rows inserted.

        """
        added = 0
        for cash in cash_txs or ():
            cid = _opt_str(getattr(cash, "transactionID", None))
            if cid is None or session.get(IBKRCashTransaction, cid) is not None:
                continue
            session.add(
                IBKRCashTransaction(
                    transaction_id=cid,
                    account_id=_opt_str(getattr(cash, "accountId", None)) or "",
                    currency=_opt_str(getattr(cash, "currency", None)) or "",
                    amount=_to_decimal(getattr(cash, "amount", None)) or Decimal(0),
                    type_=_enum_name(getattr(cash, "type", None)),
                    description=_opt_str(getattr(cash, "description", None)),
                    date=_to_datetime(getattr(cash, "dateTime", None))
                    or datetime.now(UTC),
                )
            )
            added += 1
        return added

    @staticmethod
    def _persist_snapshots(
        session: Any,
        summaries: Any,
        fallback_account: str,
        fallback_ts: datetime,
    ) -> int:
        """Append one snapshot row per ``EquitySummaryInBase`` entry.

        Args:
            session: An active SQLAlchemy session.
            summaries: Iterable of equity summary entries, or ``None``.
            fallback_account: Account identifier to use when an entry omits one.
            fallback_ts: Timestamp to use when an entry omits ``reportDate``.

        Returns:
            The number of rows inserted.

        """
        added = 0
        for summary in summaries or ():
            session.add(
                IBKRAccountSnapshot(
                    account_id=_opt_str(getattr(summary, "accountId", None)) or fallback_account,
                    currency=_opt_str(getattr(summary, "currency", None)) or "",
                    nav=_to_decimal(getattr(summary, "total", None)),
                    cash_balance=_to_decimal(getattr(summary, "cash", None)),
                    snapshot_date=_to_datetime(getattr(summary, "reportDate", None))
                    or fallback_ts,
                )
            )
            added += 1
        return added

    @staticmethod
    def _persist_positions(
        session: Any,
        positions: Any,
        fallback_account: str,
        fallback_ts: datetime,
    ) -> int:
        """Append one row per ``OpenPosition`` entry in the statement.

        Args:
            session: An active SQLAlchemy session.
            positions: Iterable of open positions, or ``None``.
            fallback_account: Account identifier to use when a row omits one.
            fallback_ts: Timestamp to use when a row omits ``reportDate``.

        Returns:
            The number of rows inserted.

        """
        added = 0
        for pos in positions or ():
            session.add(
                IBKRPosition(
                    account_id=_opt_str(getattr(pos, "accountId", None)) or fallback_account,
                    symbol=_opt_str(getattr(pos, "symbol", None)) or "",
                    asset_class=_enum_name(getattr(pos, "assetCategory", None)),
                    currency=_opt_str(getattr(pos, "currency", None)) or "",
                    quantity=_to_decimal(getattr(pos, "position", None)) or Decimal(0),
                    mark_price=_to_decimal(getattr(pos, "markPrice", None)),
                    position_value=_to_decimal(getattr(pos, "positionValue", None)),
                    snapshot_date=_to_datetime(getattr(pos, "reportDate", None)) or fallback_ts,
                )
            )
            added += 1
        return added


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
