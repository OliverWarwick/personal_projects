"""Average-cost realised P&L replay shared by synthetic providers.

IBKR's FlexQuery sets ``fifoPnlRealized`` on each SELL ``Trade`` element.
HL and AJ Bell exports do not carry that value, so we reconstruct it from
the trade ledger using a running average cost per symbol. The result is
attached to the synthesised ``Trade`` element so the dashboard's existing
realised-P&L pipeline (run.py::_events) works unchanged.

We use average cost rather than strict FIFO because:

* AJ Bell and HL both quote a single ``Cost`` figure per holding,
  matching the average-cost convention.
* Average cost is order-independent within a settlement window, which
  matters because the AJ Bell CSV exports newest-first and tax-year HL
  exports occasionally interleave odd orders.

For a symbol with positive remaining units the running average is
``total_cost / units``. On a SELL we book ``(price - avg) * |units|``
as realised and reduce both the unit count and the cost proportionally.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class TradeLeg:
    """One BUY/SELL leg to replay.

    Attributes:
        order: Sort key used to order legs deterministically (typically the
            trade date as ``YYYYMMDD``; ties broken by insertion order via
            stable sort).
        symbol: Dashboard symbol the leg trades.
        quantity: Signed units — positive for BUY, negative for SELL.
        unit_price: Price per unit in the account's base currency.

    """

    order: str
    symbol: str
    quantity: Decimal
    unit_price: Decimal


def compute_realized(legs: list[TradeLeg]) -> dict[int, Decimal]:
    """Return ``{leg index: realised P&L}`` using running average cost.

    Thin wrapper over :func:`compute_realized_detailed` that discards the
    average-cost field for callers that only need P&L numbers.
    """
    return {i: pnl for i, (pnl, _avg) in compute_realized_detailed(legs).items()}


def compute_realized_detailed(
    legs: list[TradeLeg],
) -> dict[int, tuple[Decimal, Decimal]]:
    """Return ``{leg index: (realised P&L, avg cost at sale)}`` for each SELL leg.

    The ``avg cost`` field is the running average unit cost basis for the
    symbol *immediately before* the SELL is booked, which is the value to
    surface as a closed-position open price in the dashboard.
    """
    indexed = sorted(enumerate(legs), key=lambda p: p[1].order)
    qty: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    cost: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    out: dict[int, tuple[Decimal, Decimal]] = {}
    for idx, leg in indexed:
        if leg.quantity > 0:
            qty[leg.symbol] += leg.quantity
            cost[leg.symbol] += leg.quantity * leg.unit_price
        elif leg.quantity < 0:
            units_sold = -leg.quantity
            running_qty = qty[leg.symbol]
            if running_qty <= 0:
                out[idx] = (units_sold * leg.unit_price, Decimal(0))
                qty[leg.symbol] -= units_sold
                continue
            avg = cost[leg.symbol] / running_qty
            out[idx] = ((leg.unit_price - avg) * units_sold, avg)
            cost[leg.symbol] -= units_sold * avg
            qty[leg.symbol] -= units_sold
    return out
