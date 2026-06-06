"""Shared reconciliation helpers for synthetic broker providers.

:func:`inject_opening_balances` is used by both the HL and AJ Bell providers
to plug the gap between a broker's CSV export window and the earliest event
in the synthesised ledger.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from decimal import Decimal

# Weekday index that marks the start of the weekend (Saturday = 5). The
# dashboard timeline iterates pandas business days only, so events dated
# on Sat/Sun are silently dropped.
_SATURDAY_WEEKDAY = 5


def _parse_dec(v: str | None) -> Decimal:
    """Parse a decimal attribute, treating empty/None as 0."""
    if v is None or v == "":
        return Decimal(0)
    try:
        return Decimal(v)
    except (ArithmeticError, ValueError):
        return Decimal(0)


def inject_opening_balances(  # noqa: PLR0912, PLR0915
    stmt: ET.Element,
    *,
    snapshot_holdings: list[tuple[str, Decimal, Decimal]],
    target_final_cash: Decimal,
) -> None:
    """Inject one synthetic opening row before the earliest ledger event.

    HL and AJ Bell exports only cover a limited history window, so units
    bought before that window have no BUY row and earlier deposits are
    missing. Rather than papering over the gap with mid-timeline plug rows
    (which create spurious cliffs), we emit a single opening day, dated one
    business day before the first real event:

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
        q = _parse_dec(t.get("quantity"))
        px = _parse_dec(t.get("tradePrice"))
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
            amt = _parse_dec(child.get("amount"))
        elif child.tag == "Trade":
            d_str = child.get("tradeDate")
            amt = _parse_dec(child.get("proceeds"))
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
        # widget counts pre-window money toward invested capital.
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
