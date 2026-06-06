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
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from src.apps.finances_dashboard._reconcile import inject_opening_balances
from src.clients.hl.loader import discover_accounts
from src.clients.hl.reconcile import normalise_name

_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")

if TYPE_CHECKING:
    from src.clients.hl.loader import HLAccount
    from src.data.hl_models import HLAccountKind, HLHolding  # noqa: F401

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
        if txn.kind is HLTxnKind.REDEMPTION:
            # Handled later as a synthetic SELL Trade so FIFO consumes the
            # matured units; the cash credit comes from the Trade's
            # ``proceeds`` so emitting a parallel CashTransaction would
            # double-count.
            continue
        # Map HL kinds to IBKR-style cash-transaction types so the dashboard's
        # ``Total Invested`` filter (which keys on "Deposits/Withdrawals")
        # picks up real cash contributions. Other kinds keep their HL label.
        type_attr = (
            "Deposits/Withdrawals" if txn.kind is HLTxnKind.DEPOSIT
            else txn.kind.value
        )
        _add(
            stmt,
            "CashTransaction",
            levelOfDetail="DETAIL",
            dateTime=txn.settle_date.strftime("%Y%m%d"),
            settleDate=txn.settle_date.strftime("%Y%m%d"),
            amount=str(txn.value_gbp),
            fxRateToBase="1",
            type=type_attr,
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

    # Bond/gilt redemptions: HL emits a single ``RDP CR`` cash row crediting
    # the face value, with no S-prefix SELL to close the position. Emit a
    # synthetic SELL that consumes whatever lot remains under that name so
    # FIFO clears the units and the cash is delivered via ``proceeds``.
    for txn in account.transactions:
        if txn.kind is not HLTxnKind.REDEMPTION or txn.stock_name is None:
            continue
        code = _resolve_code(txn.stock_name, account) or _slug(txn.stock_name)
        remaining = lot_qty.get(code, Decimal(0))
        if remaining <= 0:
            continue
        avg = (lot_cost.get(code, Decimal(0)) / remaining) if remaining else Decimal(0)
        unit_price = (txn.value_gbp / remaining) if remaining else Decimal(0)
        _add(
            stmt,
            "Trade",
            levelOfDetail="EXECUTION",
            symbol=code,
            currency="GBP",
            tradeDate=txn.trade_date.strftime("%Y%m%d"),
            quantity=str(-remaining),
            tradePrice=str(unit_price),
            proceeds=str(txn.value_gbp),
            fxRateToBase="1",
            openCloseIndicator="C",
            origTradePrice=str(avg),
            fifoPnlRealized=str((unit_price - avg) * remaining),
        )
        lot_qty[code] = Decimal(0)
        lot_cost[code] = Decimal(0)

    # HL exports cover ~5 tax years, so any units bought (and cash deposited)
    # before that window are absent from the ledger. Rather than papering over
    # this with mid-timeline plug rows, surface the gap as one synthetic
    # opening row dated before the first ledger event: opening units per code
    # to match the snapshot, plus an opening cash deposit so the final balance
    # reconciles to the CSV.
    inject_opening_balances(
        stmt,
        snapshot_holdings=[(h.code, h.units, h.cost_gbp) for h in account.summary.holdings],
        target_final_cash=Decimal(account.summary.cash_gbp or 0),
    )

    return stmt


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
