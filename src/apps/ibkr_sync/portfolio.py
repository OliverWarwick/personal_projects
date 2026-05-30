"""Current portfolio snapshot from an IBKR Flex Query response.

Provides dataclass views over the open positions in a Flex statement: a
:class:`Position` per holding, an aggregate :class:`PortfolioSummary`, and
:class:`Portfolio` which bundles them together with account metadata and
exposes a :meth:`Portfolio.to_dataframe` helper for tabular analysis.
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


@dataclass(frozen=True, slots=True)
class Position:
    """A single open position within an IBKR account.

    Fields are denominated in the instrument's own currency. ``pnl`` is
    unrealised, derived from IBKR's FIFO unrealised P&L when available and
    otherwise computed as ``(current_market_price - avg_purchase_price) *
    quantity``.

    Attributes:
        ticker: Instrument identifier reported by IBKR (the ``symbol`` field).
        asset_class: IBKR asset category code (e.g., ``"STK"``, ``"OPT"``),
            or ``None`` if not reported.
        currency: Three-letter ISO currency code the position trades in.
        quantity: Signed position size (positive long, negative short).
        current_market_price: Mark price used to value the position.
        avg_purchase_price: Average cost basis per unit (FIFO).
        market_value: ``quantity * current_market_price`` in instrument currency.
        market_value_base: Mark-to-market value converted to the account's
            base currency via the row's ``fxRateToBase``. Suitable for
            cross-currency weighting (e.g., portfolio risk metrics).
        cost_basis: Total acquisition cost in instrument currency.
        pnl: Unrealised profit and loss in instrument currency.

    """

    ticker: str
    asset_class: str | None
    currency: str
    quantity: Decimal
    current_market_price: Decimal | None
    avg_purchase_price: Decimal | None
    market_value: Decimal | None
    market_value_base: Decimal | None
    cost_basis: Decimal | None
    pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    """Aggregate headline figures for an IBKR account, in the base currency.

    Attributes:
        capital_deployed: Total cost basis across all open positions, converted
            to the account's base currency. ``None`` if no position reported a
            cost basis.
        current_market_value: Total mark-to-market value of all open positions
            in the base currency. ``None`` if no position reported a market
            value.
        pnl: Unrealised P&L in the base currency, computed as
            ``current_market_value - capital_deployed`` when both are present.

    """

    capital_deployed: Decimal | None
    current_market_value: Decimal | None
    pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class Portfolio:
    """Snapshot of an IBKR account's open positions on a given report date.

    One :class:`Portfolio` is produced per ``FlexStatement`` in the Flex
    response, so a multi-account download yields a list with one entry per
    account.

    Attributes:
        account_id: IBKR account identifier (e.g., ``"U1234567"``).
        base_currency: Three-letter ISO currency code of the account's
            reporting base, used for aggregate figures in :attr:`summary`.
        report_date: Statement ``reportDate`` the snapshot represents, or
            ``None`` if the statement omits one.
        summary: Aggregate headline figures for the account.
        positions: Open positions, in the order returned by IBKR.

    """

    account_id: str
    base_currency: str
    report_date: datetime | None
    summary: PortfolioSummary
    positions: list[Position] = field(default_factory=list[Position])

    def to_dataframe(self) -> pd.DataFrame:
        """Return the positions as a :class:`pandas.DataFrame`.

        The frame has one row per :class:`Position`, with columns matching
        the dataclass fields. Account-level fields and the summary are not
        included; access them on the parent :class:`Portfolio` instead.

        Returns:
            A :class:`pandas.DataFrame` indexed by integer row number, or an
            empty frame with the expected columns when there are no positions.

        """
        if not self.positions:
            columns = list(Position.__dataclass_fields__.keys())
            return pd.DataFrame(columns=columns)
        return pd.DataFrame([asdict(p) for p in self.positions])


def get_current_portfolio(response: FlexQueryResponse) -> list[Portfolio]:
    """Build :class:`Portfolio` snapshots from a parsed Flex response.

    Iterates the statements in ``response`` and constructs one
    :class:`Portfolio` per statement, populating positions from
    ``OpenPositions`` and aggregate figures from the same positions converted
    to the account's base currency via each row's ``fxRateToBase``.

    Args:
        response: The typed Flex response returned by
            :meth:`IBKRFlexClient.download_and_parse` or
            :meth:`IBKRFlexClient.parse_xml`.

    Returns:
        A list of :class:`Portfolio` snapshots, one per ``FlexStatement``.
        Empty when the response contains no statements.

    """
    portfolios: list[Portfolio] = []
    for stmt in response.FlexStatements:
        positions = [
            _to_position(row) for row in (getattr(stmt, "OpenPositions", None) or ())
        ]
        base_currency = _resolve_base_currency(stmt)
        summary = _summarise(
            getattr(stmt, "OpenPositions", None) or (),
        )
        portfolios.append(
            Portfolio(
                account_id=str(getattr(stmt, "accountId", "") or ""),
                base_currency=base_currency,
                report_date=_to_datetime(getattr(stmt, "toDate", None)),
                summary=summary,
                positions=positions,
            )
        )
    return portfolios


def fetch_current_portfolio(
    config: IBKRSyncConfig,
    query_id: str | None = None,
) -> list[Portfolio]:
    """Download a portfolio Flex Query and return the parsed snapshots.

    Resolves the Flex Web Service token and Portfolio Flex Query ID from the
    supplied :class:`IBKRSyncConfig`, downloads the statement, and converts
    it into :class:`Portfolio` snapshots.

    Args:
        config: Loaded :class:`IBKRSyncConfig` instance providing the token
            and default Portfolio Flex Query ID.
        query_id: Optional override for the Portfolio Flex Query ID. When
            omitted, falls back to ``config.portfolio_query_id``.

    Returns:
        A list of :class:`Portfolio` snapshots, one per ``FlexStatement``
        returned by the Flex Web Service.

    Raises:
        RuntimeError: If no Flex Web Service token is configured, or no
            portfolio query ID is supplied or configured.

    """
    if not config.token:
        raise RuntimeError(
            "IBKR Flex token is not configured (set IBKR_FLEX_TOKEN or token: in YAML)"
        )
    qid = query_id or config.portfolio_query_id
    if not qid:
        raise RuntimeError(
            "No portfolio Flex Query ID provided "
            "(set IBKR_FLEX_PORTFOLIO_QUERY or pass query_id)"
        )
    response = IBKRFlexClient(config.token).download_and_parse(qid)
    return get_current_portfolio(response)


def _to_position(row: Any) -> Position:
    """Convert a single ibflex ``OpenPosition`` into a :class:`Position`.

    Args:
        row: An ibflex ``OpenPosition`` dataclass instance.

    Returns:
        The equivalent :class:`Position`. Missing optional values become
        ``None``; quantity defaults to ``Decimal(0)`` so the field is always
        populated.

    """
    quantity = _to_decimal(getattr(row, "position", None)) or Decimal(0)
    market_price = _to_decimal(getattr(row, "markPrice", None))
    avg_price = _to_decimal(getattr(row, "costBasisPrice", None))
    market_value = _to_decimal(getattr(row, "positionValue", None))
    if market_value is None and market_price is not None:
        market_value = market_price * quantity
    fx = _to_decimal(getattr(row, "fxRateToBase", None)) or Decimal(1)
    market_value_base = _to_decimal(getattr(row, "positionValueInBase", None))
    if market_value_base is None and market_value is not None:
        market_value_base = market_value * fx
    cost_basis = _to_decimal(getattr(row, "costBasisMoney", None))
    if cost_basis is None and avg_price is not None:
        cost_basis = avg_price * quantity
    pnl = _to_decimal(getattr(row, "fifoPnlUnrealized", None))
    if pnl is None and market_value is not None and cost_basis is not None:
        pnl = market_value - cost_basis
    return Position(
        ticker=str(getattr(row, "symbol", "") or ""),
        asset_class=_enum_name(getattr(row, "assetCategory", None)),
        currency=str(getattr(row, "currency", "") or ""),
        quantity=quantity,
        current_market_price=market_price,
        avg_purchase_price=avg_price,
        market_value=market_value,
        market_value_base=market_value_base,
        cost_basis=cost_basis,
        pnl=pnl,
    )


def _summarise(positions: Any) -> PortfolioSummary:
    """Aggregate positions into a :class:`PortfolioSummary` in base currency.

    Each position's instrument-currency figures are converted to the account
    base currency by multiplying by its own ``fxRateToBase``. The base-currency
    market value prefers ``positionValueInBase`` when IBKR supplies it.

    Args:
        positions: Iterable of ibflex ``OpenPosition`` rows.

    Returns:
        A :class:`PortfolioSummary` whose fields are ``None`` when no
        contributing position reported the underlying value.

    """
    capital_deployed: Decimal | None = None
    market_value_base: Decimal | None = None

    for row in positions:
        fx = _to_decimal(getattr(row, "fxRateToBase", None)) or Decimal(1)
        cost_basis = _to_decimal(getattr(row, "costBasisMoney", None))
        if cost_basis is None:
            avg_price = _to_decimal(getattr(row, "costBasisPrice", None))
            quantity = _to_decimal(getattr(row, "position", None))
            if avg_price is not None and quantity is not None:
                cost_basis = avg_price * quantity
        if cost_basis is not None:
            capital_deployed = (capital_deployed or Decimal(0)) + cost_basis * fx

        value_base = _to_decimal(getattr(row, "positionValueInBase", None))
        if value_base is None:
            value_local = _to_decimal(getattr(row, "positionValue", None))
            if value_local is not None:
                value_base = value_local * fx
        if value_base is not None:
            market_value_base = (market_value_base or Decimal(0)) + value_base

    pnl: Decimal | None = None
    if capital_deployed is not None and market_value_base is not None:
        pnl = market_value_base - capital_deployed

    return PortfolioSummary(
        capital_deployed=capital_deployed,
        current_market_value=market_value_base,
        pnl=pnl,
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
