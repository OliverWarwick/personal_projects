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
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from src.clients.hl.loader import discover_accounts
from src.clients.hl.reconcile import normalise_name

_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")

# Weekday index that marks the start of the weekend (Saturday = 5). The
# dashboard timeline iterates pandas business days only, so events dated
# on Sat/Sun are silently dropped.
_SATURDAY_WEEKDAY = 5

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

    # HL exports cover ~5 tax years, so any units bought (and cash deposited)
    # before that window are absent from the ledger. Rather than papering over
    # this with mid-timeline plug rows, surface the gap as one synthetic
    # opening row dated before the first ledger event: opening units per code
    # to match the snapshot, plus an opening cash deposit so the final balance
    # reconciles to the CSV.
    _inject_opening_balances(
        stmt,
        snapshot_holdings=[(h.code, h.units, h.cost_gbp) for h in account.summary.holdings],
        target_final_cash=Decimal(account.summary.cash_gbp or 0),
    )

    return stmt


def _inject_opening_balances(
    stmt: ET.Element,
    *,
    snapshot_holdings: list[tuple[str, Decimal, Decimal]],
    target_final_cash: Decimal,
) -> None:
    """Inject one synthetic opening row before the earliest ledger event.

    HL exports only carry ~5 tax years of transactions, so units bought
    before that window have no BUY row and historical deposits are missing.
    Rather than papering over the gap with mid-timeline plugs (which create
    spurious cliffs), we emit a single opening day, dated one business day
    before the first real event:

    * For each snapshot holding where ``snapshot.units > replayed_units``,
      emit a synthetic opening BUY for the missing quantity, priced at the
      remaining-cost average so the snapshot cost basis lines up. The buy
      is paired with an equal-amount ``Opening Balance`` CashTransaction so
      it doesn't artificially drain opening cash.
    * Finally, emit one ``Opening Balance`` CashTransaction sized so the
      closing cash balance equals ``target_final_cash`` from the CSV.

    The result is a coherent opening state followed by purely real events —
    no mid-timeline plug rows and no final-day anchor cliff.

    Args:
        stmt: The synthesised ``FlexStatement`` element. Mutated in place.
        snapshot_holdings: One ``(code, units, cost_gbp)`` tuple per current
            holding from the broker snapshot.
        target_final_cash: Authoritative current cash from the broker CSV.

    """
    import pandas as pd  # noqa: PLC0415 — keep heavy import local to this helper

    # Replay existing Trade rows to recover net units and weighted-average
    # cost per code. Doing this from the stmt (rather than asking the caller
    # to pass it in) means the same helper works for HL and AJ Bell.
    replayed_qty: dict[str, Decimal] = {}
    replayed_cost: dict[str, Decimal] = {}
    trade_rows = sorted(
        (t for t in stmt if t.tag == "Trade" and t.get("tradeDate")),
        key=lambda t: t.get("tradeDate") or "",
    )
    for t in trade_rows:
        sym = t.get("symbol") or ""
        q = _to_dec_local(t.get("quantity"))
        px = _to_dec_local(t.get("tradePrice"))
        prior_q = replayed_qty.get(sym, Decimal(0))
        prior_c = replayed_cost.get(sym, Decimal(0))
        if q > 0:
            replayed_qty[sym] = prior_q + q
            replayed_cost[sym] = prior_c + q * px
        else:
            sold = -q
            avg = (prior_c / prior_q) if prior_q else Decimal(0)
            replayed_qty[sym] = prior_q + q
            replayed_cost[sym] = prior_c - sold * avg

    # Find earliest *business-day* event. The dashboard timeline iterates
    # pandas bdate_range and silently drops Sat/Sun rows, so we must mirror
    # that filter — otherwise weekend events appear in our cash reconcile
    # arithmetic but never in the replay, and opening_cash is off by their
    # sum (mostly fractional interest, but it accumulates).
    earliest: date | None = None
    for child in stmt:
        d_str: str | None
        if child.tag == "CashTransaction":
            d_str = child.get("dateTime") or child.get("settleDate")
        elif child.tag == "Trade":
            d_str = child.get("tradeDate")
        else:
            continue
        if not d_str:
            continue
        d = date(int(d_str[:4]), int(d_str[4:6]), int(d_str[6:8]))
        if pd.Timestamp(d).weekday() >= _SATURDAY_WEEKDAY:
            continue
        if earliest is None or d < earliest:
            earliest = d

    # Opening date: one business day before the earliest real event. If there
    # are no events yet, anchor to today so the snapshot is still represented.
    if earliest is None:
        opening_d = datetime.now(UTC).date()
    else:
        opening_d = pd.bdate_range(end=earliest, periods=2)[0].date()
        if opening_d >= earliest:
            opening_d = pd.bdate_range(end=earliest - pd.Timedelta(days=1), periods=1)[0].date()
    opening_date = opening_d.strftime("%Y%m%d")

    opening_rows: list[ET.Element] = []
    opening_cost_credit = Decimal(0)

    # Synthetic opening BUYs for any holdings under-represented by the replay.
    tol = Decimal("0.0001")
    for code, snap_units, snap_cost in snapshot_holdings:
        replayed_units = replayed_qty.get(code, Decimal(0))
        delta_units = snap_units - replayed_units
        if delta_units <= tol:
            continue
        replayed_cost_for_code = replayed_cost.get(code, Decimal(0))
        # Remaining cost = snapshot cost - cost of units bought in-window
        # that are still held. Approximate with the in-window average cost.
        in_window_avg = (
            replayed_cost_for_code / replayed_units if replayed_units > 0 else Decimal(0)
        )
        in_window_still_held_cost = min(replayed_units, snap_units) * in_window_avg
        opening_cost = snap_cost - in_window_still_held_cost
        if opening_cost <= 0:
            opening_cost = (snap_cost / snap_units) * delta_units if snap_units else Decimal(0)
        opening_price = opening_cost / delta_units if delta_units else Decimal(0)
        proceeds = -delta_units * opening_price
        opening_rows.append(ET.Element(
            "Trade",
            {
                "levelOfDetail": "EXECUTION",
                "symbol": code,
                "currency": "GBP",
                "tradeDate": opening_date,
                "quantity": str(delta_units),
                "tradePrice": str(opening_price),
                "proceeds": str(proceeds),
                "fxRateToBase": "1",
                "openCloseIndicator": "O",
            },
        ))
        opening_cost_credit += delta_units * opening_price

    # Compute net cash impact across every business-day-dated row plus the
    # synthetic buys above, then size one opening deposit to land on
    # target_final_cash. Weekend-dated rows are excluded to match the
    # business-day replay in the dashboard timeline.
    net_cash = Decimal(0)
    for child in stmt:
        if child.tag == "CashTransaction":
            d_str = child.get("dateTime") or child.get("settleDate")
            amt = _to_dec_local(child.get("amount"))
        elif child.tag == "Trade":
            d_str = child.get("tradeDate")
            amt = _to_dec_local(child.get("proceeds"))
        else:
            continue
        if not d_str:
            continue
        ev_d = date(int(d_str[:4]), int(d_str[4:6]), int(d_str[6:8]))
        if pd.Timestamp(ev_d).weekday() >= _SATURDAY_WEEKDAY:
            continue
        net_cash += amt
    # Synthetic opening buys deduct cash via their proceeds; credit it back
    # so the opening trades don't appear to consume in-window cash.
    opening_cash = target_final_cash - net_cash + opening_cost_credit

    if opening_cash != 0:
        # Label as Deposits/Withdrawals so the dashboard's "Total Invested"
        # widget counts pre-window money toward invested capital. The amount
        # = (cash needed today to reconcile) + (cost of pre-window holdings)
        # which is exactly the cash that was put in before the export window.
        opening_rows.append(ET.Element(
            "CashTransaction",
            {
                "levelOfDetail": "DETAIL",
                "dateTime": opening_date,
                "settleDate": opening_date,
                "amount": str(opening_cash),
                "fxRateToBase": "1",
                "type": "Deposits/Withdrawals",
                "currency": "GBP",
            },
        ))

    # Insert opening rows at the front so they precede every real event in
    # the timeline replay.
    for row in reversed(opening_rows):
        stmt.insert(0, row)


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
