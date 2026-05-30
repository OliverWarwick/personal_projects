"""Low-level CSV parsers for AJ Bell export files.

Both file shapes are standard CSVs with a UTF-8 BOM. The portfolio file
mixes one cash row (``Investment`` cell starts with ``Cash ``) with one
row per holding; the transaction file has no preamble.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from src.data.ajbell_models import (
    AJBellHolding,
    AJBellPortfolio,
    AJBellTransaction,
    AJBellTxnKind,
)

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

# AJ Bell appends ``(<EXCHANGE>:<TICKER>)`` to the investment name for
# listed instruments. OEIC funds have no suffix.
_TICKER_RE = re.compile(r"\s*\(([A-Z]+):([A-Za-z0-9.\-]+)\)\s*$")

# Filenames are ``portfolio-<ACCOUNT_ID>-<ACCOUNT_NAME>.csv``.
_FILENAME_RE = re.compile(r"^portfolio-(?P<id>[^-]+)-(?P<name>.+)\.csv$")


def _decimal(value: str) -> Decimal:
    """Parse an AJ Bell numeric cell.

    Strips thousands separators and a leading ``£``. Empty strings become
    zero so blank quantity/price cells on cash-only transaction rows do
    not raise.

    Args:
        value: Raw cell value (already stripped of surrounding quotes).

    Returns:
        Parsed :class:`~decimal.Decimal`. ``Decimal(0)`` for empty input.

    """
    cleaned = value.replace(",", "").replace("£", "").strip()
    if not cleaned:
        return Decimal(0)
    return Decimal(cleaned)


def _parse_trade_date(value: str) -> date:
    """Parse an AJ Bell ``dd/mm/yyyy`` trade date."""
    return datetime.strptime(value.strip(), "%d/%m/%Y").date()  # noqa: DTZ007


def _parse_snapshot_datetime(date_cell: str) -> date | None:
    """Parse the ``25-May-26`` date cell on a snapshot row."""
    cleaned = date_cell.strip()
    if not cleaned:
        return None
    try:
        return datetime.strptime(cleaned, "%d-%b-%y").date()  # noqa: DTZ007
    except ValueError:
        return None


def _split_investment(name: str) -> tuple[str, str | None]:
    """Split ``"Name (LSE:TICK)"`` into ``("Name", "TICK")``.

    Args:
        name: Raw investment name as it appears in the CSV.

    Returns:
        Tuple of cleaned name and ticker (``None`` when no suffix was
        present, as for OEIC funds).

    """
    match = _TICKER_RE.search(name)
    if match is None:
        return name.strip(), None
    cleaned = _TICKER_RE.sub("", name).strip()
    return cleaned, match.group(2)


def _classify(raw_transaction: str) -> AJBellTxnKind:
    """Classify an AJ Bell transaction by its ``Transaction`` column.

    Args:
        raw_transaction: Raw ``Transaction`` cell. Empty cells appear on
            cash-only adjustment rows and are reported as ``OTHER`` so
            they do not contribute to unit reconciliation.

    Returns:
        Classified :class:`AJBellTxnKind`.

    """
    upper = raw_transaction.strip().upper()
    if upper == "PURCHASE":
        return AJBellTxnKind.PURCHASE
    if upper == "SALE":
        return AJBellTxnKind.SALE
    if "DISTRIBUTION" in upper:
        return AJBellTxnKind.DISTRIBUTION
    if "EQUALISATION" in upper:
        return AJBellTxnKind.EQUALISATION
    return AJBellTxnKind.OTHER


def parse_portfolio(path: Path) -> AJBellPortfolio:
    """Parse one ``portfolio-<id>-<name>.csv`` snapshot.

    Args:
        path: Path to the CSV file. The filename is also parsed to
            recover the account id and human-readable name.

    Returns:
        Populated :class:`AJBellPortfolio`.

    Raises:
        ValueError: When the filename does not match the expected
            ``portfolio-<id>-<name>.csv`` pattern.

    """
    match = _FILENAME_RE.match(path.name)
    if match is None:
        raise ValueError(f"Unexpected AJ Bell portfolio filename: {path.name}")
    account_id = match.group("id")
    account_name = match.group("name")

    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    holdings: list[AJBellHolding] = []
    cash = Decimal(0)
    snapshot_at: date | None = None
    stock_value = Decimal(0)

    for row in rows:
        investment = (row.get("Investment") or "").strip()
        if not investment:
            continue
        if snapshot_at is None:
            snapshot_at = _parse_snapshot_datetime(row.get("Date") or "")
        if investment.startswith("Cash "):
            cash += _decimal(row.get("Value (£)") or "0")
            continue
        name, ticker = _split_investment(investment)
        value_gbp = _decimal(row.get("Value (£)") or "0")
        stock_value += value_gbp
        holdings.append(
            AJBellHolding(
                investment=name,
                ticker=ticker,
                quantity=_decimal(row.get("Quantity") or "0"),
                price_gbp=_decimal(row.get("Price") or "0"),
                value_gbp=value_gbp,
                cost_gbp=_decimal(row.get("Cost (£)") or "0"),
                change_gbp=_decimal(row.get("Change (£)") or "0"),
            ),
        )

    return AJBellPortfolio(
        account_id=account_id,
        account_name=account_name,
        snapshot_at=snapshot_at,
        stock_value_gbp=stock_value,
        cash_gbp=cash,
        total_value_gbp=stock_value + cash,
        holdings=holdings,
    )


def parse_transaction_history(path: Path) -> list[AJBellTransaction]:
    """Parse the AJ Bell ``transactionhistory.csv`` ledger.

    Args:
        path: Path to the CSV file.

    Returns:
        One :class:`AJBellTransaction` per row, in file order (AJ Bell
        exports newest first; callers may sort if needed).

    """
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    transactions: list[AJBellTransaction] = []
    for row in rows:
        date_cell = (row.get("Date") or "").strip()
        if not date_cell:
            continue
        raw_transaction = (row.get("Transaction") or "").strip()
        kind = _classify(raw_transaction)
        description = (row.get("Description") or "").strip()
        quantity_dec = _decimal(row.get("Quantity") or "0")
        price = _decimal(row.get("Price") or "0")

        quantity: Decimal | None
        price_out: Decimal | None
        if kind is AJBellTxnKind.PURCHASE:
            quantity = quantity_dec
            price_out = price
        elif kind is AJBellTxnKind.SALE:
            quantity = -quantity_dec
            price_out = price
        else:
            quantity = None
            price_out = None

        transactions.append(
            AJBellTransaction(
                trade_date=_parse_trade_date(date_cell),
                kind=kind,
                description=description,
                quantity=quantity,
                price_gbp=price_out,
                amount_gbp=_decimal(row.get("Amount (GBP)") or "0"),
                reference=(row.get("Reference") or "").strip(),
                raw_transaction=raw_transaction,
            ),
        )

    return transactions
