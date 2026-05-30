"""Bridge between the HL CSV loader and the IBKR-shaped dashboard.

The dashboard renders open positions by iterating ``OpenPosition`` children
of a Flex ``FlexStatement`` element. This module synthesises an equivalent
``FlexStatement`` from an :class:`HLAccount` so HL holdings appear in the
sidebar accounts and aggregate together with IBKR positions sharing the same
ticker (e.g. ``VUAG``).

Only the attributes consumed by :func:`_open_positions` and the aggregation
helper are populated; timeline reconstruction (which needs daily marks) is
intentionally not provided for HL.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from src.clients.hl.loader import discover_accounts
from src.clients.hl.reconcile import normalise_name

_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")

if TYPE_CHECKING:
    from src.clients.hl.loader import HLAccount
    from src.data.hl_models import HLAccountKind

logger = logging.getLogger(__name__)

# Account-id prefix used in the dashboard config and URL to flag an HL
# account. ``_resolve`` checks this prefix before falling back to the
# Flex-XML lookup.
HL_ACCOUNT_PREFIX = "HL-"

# Stock splits that occurred after the HL trade was booked. HL CSV exports
# preserve the original (pre-split) quantity and price, but yfinance returns
# split-adjusted closes — so without rescaling the pre-split rows the cost
# basis and quantity would be off by the split ratio. Map ``code → list of
# (effective_date, ratio)`` where ``ratio`` is the post-split share count
# per one pre-split share.
_HL_SPLITS: dict[str, list[tuple[date, Decimal]]] = {
    "AMAZON-COM-INC-COM-STK-U": [(date(2022, 6, 6), Decimal(20))],
}


def _apply_split(code: str, trade_date: date, qty: Decimal, price: Decimal) -> tuple[Decimal, Decimal]:
    """Rescale a pre-split (qty, price) to post-split units for ``code``."""
    ratio = Decimal(1)
    for eff_date, r in _HL_SPLITS.get(code, []):
        if trade_date < eff_date:
            ratio *= r
    if ratio == 1:
        return qty, price
    return qty * ratio, price / ratio


def hl_account_id(kind: HLAccountKind) -> str:
    """Return the dashboard account id for an HL account kind."""
    return f"{HL_ACCOUNT_PREFIX}{kind.value}"


def _add(elem: ET.Element, tag: str, **attrs: str) -> ET.Element:
    """Append a child element with the given string attributes."""
    return ET.SubElement(elem, tag, attrs)


def synthesize_flex_statement(account: HLAccount) -> ET.Element:
    """Build a ``FlexStatement`` element mirroring an HL account's holdings.

    Args:
        account: Fully-loaded HL account (snapshot + ledger).

    Returns:
        ``FlexStatement`` element with ``OpenPosition`` children, one per
        holding, plus ``Trade`` children for parsed BUY/SELL rows so the
        last-purchase column populates as it does for IBKR accounts.

    """
    stmt = ET.Element(
        "FlexStatement",
        {
            "accountId": hl_account_id(account.kind),
            "currency": "GBP",
        },
    )

    # Cash-only ledger rows (deposits, transfers, dividends, fees, interest)
    # must surface as CashTransaction elements; otherwise the timeline never
    # funds the account and cash goes deeply negative once buys hit.
    from src.data.hl_models import HLTxnKind  # local: avoid cycle
    for txn in account.transactions:
        if txn.kind in (HLTxnKind.BUY, HLTxnKind.SELL):
            continue
        _add(
            stmt,
            "CashTransaction",
            levelOfDetail="DETAIL",
            dateTime=txn.settle_date.strftime("%Y%m%d"),
            settleDate=txn.settle_date.strftime("%Y%m%d"),
            amount=str(txn.value_gbp),
            fxRateToBase="1",
            type=txn.kind.value,
            currency="GBP",
        )

    for h in account.summary.holdings:
        units = h.units
        cost_per_unit = (h.cost_gbp / units) if units else Decimal(0)
        # HL quotes prices in pence; convert to pounds so the dashboard's
        # quantity × price math gives the sterling value directly.
        mark_pounds = h.price_pence / Decimal(100)
        _add(
            stmt,
            "OpenPosition",
            levelOfDetail="SUMMARY",
            symbol=h.code,
            listingExchange="HL",
            currency="GBP",
            position=str(units),
            costBasisPrice=str(cost_per_unit),
            markPrice=str(mark_pounds),
            positionValue=str(h.value_gbp),
            fxRateToBase="1",
            fifoPnlUnrealized=str(h.gain_gbp),
            description=h.stock,
            assetCategory="HL",
        )

    # Running weighted-average cost per code so closing trades can carry an
    # ``origTradePrice`` and ``fifoPnlRealized`` like an IBKR Trade row.
    lot_qty: dict[str, Decimal] = {}
    lot_cost: dict[str, Decimal] = {}
    sorted_txns = sorted(
        (t for t in account.transactions
         if t.stock_name is not None and t.quantity is not None and t.unit_cost_pence is not None),
        key=lambda t: t.trade_date,
    )
    for txn in sorted_txns:
        # Use the snapshot code if we recognise the stock; otherwise fall back
        # to a slugified name so closed positions still show with a marker.
        code = _resolve_code(txn.stock_name, account) or _slug(txn.stock_name or "")
        unit_pounds = txn.unit_cost_pence / Decimal(100)
        txn_quantity, unit_pounds = _apply_split(
            code, txn.trade_date, txn.quantity, unit_pounds,
        )
        # IBKR convention: proceeds is negative on a BUY (cash out) and
        # positive on a SELL (cash in). Without this, the timeline never
        # credits cash on disposal and portfolio value drops by the sale
        # amount on the day a position is closed.
        proceeds = -txn_quantity * unit_pounds
        attrs = {
            "levelOfDetail": "EXECUTION",
            "symbol": code,
            "currency": "GBP",
            "tradeDate": txn.trade_date.strftime("%Y%m%d"),
            "quantity": str(txn_quantity),
            "tradePrice": str(unit_pounds),
            "proceeds": str(proceeds),
            "fxRateToBase": "1",
            "openCloseIndicator": "O" if txn_quantity > 0 else "C",
        }
        prior_qty = lot_qty.get(code, Decimal(0))
        prior_cost = lot_cost.get(code, Decimal(0))
        if txn_quantity > 0:
            lot_qty[code] = prior_qty + txn_quantity
            lot_cost[code] = prior_cost + txn_quantity * unit_pounds
        else:
            avg = (prior_cost / prior_qty) if prior_qty else Decimal(0)
            sold = -txn_quantity
            attrs["origTradePrice"] = str(avg)
            attrs["fifoPnlRealized"] = str((unit_pounds - avg) * sold)
            lot_qty[code] = prior_qty + txn_quantity
            lot_cost[code] = prior_cost - sold * avg
        _add(stmt, "Trade", **attrs)

    # HL accounts (ISA, SIPP, Fund & Share) are non-margin — cash cannot go
    # negative. If the parsed ledger is missing early deposits (HL's CSV export
    # often only covers a finite window) the synthesised cash trace can dip
    # below zero. Compute the minimum running balance across all emitted
    # CashTransaction + Trade rows and, if it's negative, prepend a synthetic
    # "DEP" deposit equal to that shortfall dated the day before the earliest
    # event so the timeline never shows phantom leverage.
    _plug_negative_cash(stmt)

    return stmt


def _plug_negative_cash(stmt: ET.Element) -> None:
    """Prepend a synthetic deposit so the running cash balance never dips below zero.

    HL/SIPP/ISA wrappers can't borrow, so a negative cash trace always means
    the input ledger is missing an early deposit. This walks the synthesised
    CashTransaction (signed ``amount``) and Trade (signed ``proceeds``) rows
    in date order, tracks the minimum running balance, and — if it's negative
    — emits a single ``DEP`` ``CashTransaction`` for ``|min|`` dated one day
    before the earliest event.
    """
    events: list[tuple[date, Decimal]] = []
    earliest: date | None = None
    for child in stmt:
        d_str: str | None = None
        amount: Decimal = Decimal(0)
        if child.tag == "CashTransaction":
            d_str = child.get("dateTime") or child.get("settleDate")
            amount = _to_dec_local(child.get("amount"))
        elif child.tag == "Trade":
            d_str = child.get("tradeDate")
            amount = _to_dec_local(child.get("proceeds"))
        else:
            continue
        if not d_str:
            continue
        d = date(int(d_str[:4]), int(d_str[4:6]), int(d_str[6:8]))
        events.append((d, amount))
        earliest = d if earliest is None or d < earliest else earliest
    if not events or earliest is None:
        return
    events.sort(key=lambda e: e[0])
    running = Decimal(0)
    min_running = Decimal(0)
    for _d, amt in events:
        running += amt
        if running < min_running:
            min_running = running
    if min_running >= 0:
        return
    # Place the plug on the first business day on or after ``earliest`` —
    # the dashboard timeline iterates pandas business days, so a Sat/Sun
    # plug-date silently gets skipped. ``run.py::_events`` sorts cash events
    # before trades on the same date, so a same-day plug still funds any buy.
    import pandas as pd  # noqa: PLC0415 — keep heavy import local to this helper
    plug_dt = pd.bdate_range(start=earliest, periods=1)[0].date()
    plug_date = plug_dt.strftime("%Y%m%d")
    plug = ET.Element(
        "CashTransaction",
        {
            "levelOfDetail": "DETAIL",
            "dateTime": plug_date,
            "settleDate": plug_date,
            "amount": str(-min_running),
            "fxRateToBase": "1",
            "type": "DEP",
            "currency": "GBP",
        },
    )
    stmt.insert(0, plug)


def _to_dec_local(v: str | None) -> Decimal:
    """Parse a decimal attribute, treating empty/None as 0."""
    if v is None or v == "":
        return Decimal(0)
    try:
        return Decimal(v)
    except (ArithmeticError, ValueError):
        return Decimal(0)


def _slug(name: str) -> str:
    """Return a dashboard symbol for a closed-position stock name."""
    s = _SLUG_RE.sub("-", name).strip("-").upper()
    return s[:24] or "HL-OEIC"


def _resolve_code(stock_name: str, account: HLAccount) -> str | None:
    """Map a transaction's stock name to a snapshot ``code`` if possible."""
    target = normalise_name(stock_name)
    for h in account.summary.holdings:
        if normalise_name(h.stock) == target:
            return h.code
    return None


def load_hl_statements() -> dict[str, ET.Element]:
    """Load every HL account and return a map ``{account_id: FlexStatement}``.

    Returns an empty mapping when ``HL_DATA_DIR`` is unset or empty, so the
    dashboard still works without HL data present.
    """
    try:
        accounts = discover_accounts()
    except FileNotFoundError:
        logger.info("HL data dir not found; skipping HL accounts")
        return {}
    return {hl_account_id(a.kind): synthesize_flex_statement(a) for a in accounts}
