"""Tests for the HL CSV parsers and combined-loader."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from personal_project.clients.hl.loader import load_account
from personal_project.clients.hl.parser import (
    parse_account_summary,
    parse_portfolio_summary,
)
from personal_project.clients.hl.reconcile import reconcile_account
from personal_project.data.hl_models import HLAccountKind, HLTxnKind

if TYPE_CHECKING:
    from pathlib import Path


class TestAccountSummary:
    """Cover the header skipping and holdings parsing of account-summary CSVs."""

    def test_parses_header_and_holdings(self, hl_isa_dir: Path) -> None:
        """Stock value, cash and holdings should round-trip cleanly."""
        summary = parse_account_summary(
            hl_isa_dir / "ISA" / "account-summary-isa.csv",
            HLAccountKind.ISA,
        )
        assert summary.client_number == "1234567"
        assert summary.stock_value_gbp == Decimal("10500.00")
        assert summary.cash_gbp == Decimal("100.00")
        assert summary.total_value_gbp == Decimal("10600.00")
        assert len(summary.holdings) == 1
        h = summary.holdings[0]
        assert h.code == "VUAG"
        assert h.units == Decimal(100)
        assert h.cost_gbp == Decimal("9000.00")


class TestPortfolioSummary:
    """Cover ledger parsing including BUY/SELL classification."""

    def test_classifies_buy_and_cash_rows(self, hl_isa_dir: Path) -> None:
        """B-prefixed refs become BUY with parsed quantity; INTEREST stays cash-only."""
        txns = parse_portfolio_summary(
            hl_isa_dir / "ISA" / "portfolio-summary-isa.csv",
        )
        assert len(txns) == 2  # noqa: PLR2004
        buy = next(t for t in txns if t.kind is HLTxnKind.BUY)
        assert buy.quantity == Decimal(40)
        assert buy.unit_cost_pence == Decimal("9000.00")
        assert buy.stock_name is not None
        assert "Vanguard" in buy.stock_name
        cash = next(t for t in txns if t.kind is HLTxnKind.INTEREST)
        assert cash.quantity is None


class TestLoaderCombination:
    """Cover dedupe and reconciliation across multiple tax-year files."""

    def test_combined_ledger_dedupes_trades(self, hl_isa_dir: Path) -> None:
        """Trades shared across overlapping files must only count once."""
        account = load_account(hl_isa_dir, HLAccountKind.ISA)
        buy_refs = [t.reference for t in account.transactions if t.kind is HLTxnKind.BUY]
        assert buy_refs.count("B100000001") == 1
        assert "B100000000" in buy_refs

    def test_reconciliation_passes_when_units_match(self, hl_isa_dir: Path) -> None:
        """Replaying BUYs should sum to the snapshot units exactly."""
        account = load_account(hl_isa_dir, HLAccountKind.ISA)
        result = reconcile_account(account)
        assert result.ok
        assert result.per_ticker[0].code == "VUAG"
        assert result.per_ticker[0].delta == Decimal(0)

    def test_reconciliation_flags_missing_trades(self, hl_isa_dir: Path) -> None:
        """If a trade is removed, the snapshot should report a positive delta."""
        # Strip the prior-year file so the 60-unit BUY disappears from replay.
        (hl_isa_dir / "ISA" / "portfolio-summary-isa_2526.csv").unlink()
        account = load_account(hl_isa_dir, HLAccountKind.ISA)
        result = reconcile_account(account)
        assert not result.ok
        assert result.per_ticker[0].delta == Decimal(60)
