"""Low-level CSV parsers for Hargreaves Lansdown export files.

HL exports are cp1252-encoded CSVs with a free-form preamble (client name,
client number, valuation timestamp, blank line) followed by a header row and
a body. This module skips the preamble, parses the body into typed records,
and returns :mod:`src.data.hl_models` instances.
"""

from __future__ import annotations

import csv
import re
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from src.data.hl_models import (
    HLAccountKind,
    HLAccountSummary,
    HLHolding,
    HLTransaction,
    HLTxnKind,
)

if TYPE_CHECKING:
    from pathlib import Path

HL_ENCODING = "cp1252"

_MIN_HOLDING_COLS = 8
_MIN_TXN_COLS = 7

# Description format for BUY/SELL rows:
#     "<stock name> <qty> @  <unit_price>"
# Quantity may carry thousands separators; unit price is plain decimal.
_TRADE_DESC_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<qty>[\d,]+(?:\.\d+)?)\s+@\s+(?P<price>[\d.]+)\s*$",
)


def _decimal(value: str) -> Decimal:
    """Parse an HL numeric cell, stripping thousands separators and quotes.

    Args:
        value: Cell value as it appears in the CSV (already stripped of
            surrounding quotes by :mod:`csv`).

    Returns:
        Parsed :class:`~decimal.Decimal`.

    Raises:
        ValueError: When ``value`` is empty or non-numeric.

    """
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        raise ValueError("Empty numeric cell")
    return Decimal(cleaned)


def _parse_hl_date(value: str) -> date:
    """Parse an HL ``dd/mm/yyyy`` date string."""
    return datetime.strptime(value.strip(), "%d/%m/%Y").date()  # noqa: DTZ007  # naive date is fine


def _classify(reference: str, value_gbp: Decimal) -> HLTxnKind:  # noqa: PLR0911
    """Classify a transaction row from its ``Reference`` code.

    Args:
        reference: Raw ``Reference`` cell.
        value_gbp: Signed sterling cash impact, used to disambiguate sells
            (positive) from cash-out rows when needed.

    Returns:
        Classified :class:`HLTxnKind`. ``B``-prefixed references are BUY,
        ``S``-prefixed are SELL.

    """
    ref = reference.strip()
    upper = ref.upper()
    if upper == "INTEREST":
        return HLTxnKind.INTEREST
    if upper == "MANAGE FEE":
        return HLTxnKind.MANAGE_FEE
    if upper in {"FPC", "CARD WEB"}:
        return HLTxnKind.DEPOSIT
    if upper == "TRANSFER":
        return HLTxnKind.TRANSFER
    # Gilt/bond maturity: HL credits face value as cash and the position
    # disappears from the holdings snapshot — there is no S-prefix sell row,
    # so a naive FIFO replay never consumes the units and the matured holding
    # sticks around as a phantom position worth its mark × qty.
    if upper == "RDP CR":
        return HLTxnKind.REDEMPTION
    # B-prefixed references are buys (cash out), S-prefixed are sells.
    if ref and ref[0] in {"B", "b"} and ref[1:].isdigit():
        return HLTxnKind.BUY
    if ref and ref[0] in {"S", "s"} and ref[1:].isdigit():
        return HLTxnKind.SELL
    # Anything we don't recognise but that moves cash positively without a
    # trade reference is reported as OTHER (treated as cash-only by downstream
    # reconciliation).
    _ = value_gbp  # currently unused but kept for future heuristics
    return HLTxnKind.OTHER


def _parse_trade_description(description: str) -> tuple[str, Decimal, Decimal] | None:
    """Extract stock name, quantity and unit price from a BUY/SELL description.

    Args:
        description: Raw ``Description`` cell.

    Returns:
        ``(stock_name, quantity, unit_price_pence)`` when the description
        matches the expected trade pattern; ``None`` otherwise.

    """
    match = _TRADE_DESC_RE.match(description.strip())
    if match is None:
        return None
    name = match.group("name").strip()
    qty = Decimal(match.group("qty").replace(",", ""))
    price = Decimal(match.group("price"))
    return name, qty, price


def _read_rows(path: Path) -> list[list[str]]:
    """Return every row of an HL CSV with cp1252 decoding."""
    with path.open(encoding=HL_ENCODING, newline="") as f:
        return list(csv.reader(f))


def parse_account_summary(path: Path, kind: HLAccountKind) -> HLAccountSummary:
    """Parse an HL ``account-summary-*.csv`` file.

    Args:
        path: Path to the CSV file.
        kind: Which account this file describes.

    Returns:
        Populated :class:`HLAccountSummary`.

    Raises:
        ValueError: When the file does not contain the expected sections.

    """
    rows = _read_rows(path)

    client_number = ""
    snapshot_at: date | None = None
    stock_value = Decimal(0)
    cash = Decimal(0)
    total = Decimal(0)
    holdings: list[HLHolding] = []

    # Walk the preamble: each "label,value,..." pair carries one field.
    header_idx: int | None = None
    for idx, row in enumerate(rows):
        if not row or not row[0].strip():
            continue
        label = row[0].strip().rstrip(":")
        value = row[1].strip() if len(row) > 1 else ""
        if label == "Client Number":
            client_number = value
        elif label.startswith("Spreadsheet created at"):
            snapshot_at = datetime.strptime(  # noqa: DTZ007
                value,
                "%d-%m-%Y %H:%M",
            ).date()
        elif label.startswith("Stock value"):
            stock_value = _decimal(value)
        elif label.startswith("Total cash"):
            cash = _decimal(value)
        elif label.startswith("Total value"):
            total = _decimal(value)
        elif label == "Code":
            header_idx = idx
            break

    if header_idx is None or snapshot_at is None:
        raise ValueError(f"account-summary file is missing holdings header: {path}")

    # Body rows: stop at the Totals row (Code cell is empty).
    for row in rows[header_idx + 1 :]:
        if not row or len(row) < _MIN_HOLDING_COLS:
            continue
        code = row[0].strip()
        if not code:
            break
        holdings.append(
            HLHolding(
                code=code,
                stock=row[1].strip(),
                units=_decimal(row[2]),
                price_pence=_decimal(row[3]),
                value_gbp=_decimal(row[4]),
                cost_gbp=_decimal(row[5]),
                gain_gbp=_decimal(row[6]),
            ),
        )

    return HLAccountSummary(
        kind=kind,
        client_number=client_number,
        snapshot_at=snapshot_at,
        stock_value_gbp=stock_value,
        cash_gbp=cash,
        total_value_gbp=total,
        holdings=holdings,
    )


def parse_portfolio_summary(path: Path) -> list[HLTransaction]:
    """Parse an HL ``portfolio-summary-*.csv`` ledger.

    Args:
        path: Path to the CSV file.

    Returns:
        One :class:`HLTransaction` per ledger row, in the order they appear
        in the file (HL exports newest first; callers may sort if needed).

    Raises:
        ValueError: When the file lacks the expected ``Trade date`` header.

    """
    rows = _read_rows(path)

    header_idx: int | None = None
    for idx, row in enumerate(rows):
        if row and row[0].strip() == "Trade date":
            header_idx = idx
            break

    if header_idx is None:
        raise ValueError(f"portfolio-summary file missing 'Trade date' header: {path}")

    transactions: list[HLTransaction] = []
    for row in rows[header_idx + 1 :]:
        if not row or len(row) < _MIN_TXN_COLS:
            continue
        trade_date_raw = row[0].strip()
        if not trade_date_raw:
            continue
        trade_date = _parse_hl_date(trade_date_raw)
        settle_date = _parse_hl_date(row[1])
        reference = row[2].strip()
        description = row[3].strip()
        value_gbp = _decimal(row[6])
        kind = _classify(reference, value_gbp)

        stock_name: str | None = None
        quantity: Decimal | None = None
        unit_cost: Decimal | None = None
        if kind in {HLTxnKind.BUY, HLTxnKind.SELL}:
            parsed = _parse_trade_description(description)
            if parsed is not None:
                stock_name, qty, unit_cost = parsed
                # Signed quantity: BUYs add units, SELLs remove them.
                quantity = qty if kind is HLTxnKind.BUY else -qty
        elif kind is HLTxnKind.REDEMPTION:
            # "Treasury 0.125% 30/01/2026 Gilt Net Redemption Payment" →
            # name="Treasury 0.125% 30/01/2026 Gilt". The cash value is the
            # face value being paid out; the consumer needs the bond name so
            # it can match against the surviving open lots.
            stock_name = re.sub(
                r"\s+Net\s+Redemption\s+Payment\s*$", "", description,
            ).strip() or None

        transactions.append(
            HLTransaction(
                trade_date=trade_date,
                settle_date=settle_date,
                reference=reference,
                description=description,
                kind=kind,
                stock_name=stock_name,
                quantity=quantity,
                unit_cost_pence=unit_cost,
                value_gbp=value_gbp,
            ),
        )

    return transactions
