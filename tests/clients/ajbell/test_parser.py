"""Parser, loader, and reconciliation tests for the AJ Bell client."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from personal_project.clients.ajbell import (
    discover_accounts,
    parse_portfolio,
    parse_transaction_history,
    reconcile_account,
)
from personal_project.data.ajbell_models import AJBellTxnKind

if TYPE_CHECKING:
    from pathlib import Path

_EXPECTED_HOLDINGS = 1
_EXPECTED_REPLAY_UNITS = 100
_EXPECTED_TXNS = 4


class TestParsePortfolio:
    """Cover the AJ Bell portfolio snapshot parser."""

    def test_parses_holdings_and_cash(self, ajbell_dir: Path) -> None:
        """Holdings, cash, and the ``(LSE:TICK)`` suffix split correctly."""
        portfolio = parse_portfolio(
            ajbell_dir / "portfolio-ABBXDDL-Lifetime ISA.csv",
        )
        assert portfolio.account_id == "ABBXDDL"
        assert portfolio.account_name == "Lifetime ISA"
        assert len(portfolio.holdings) == _EXPECTED_HOLDINGS
        holding = portfolio.holdings[0]
        assert holding.investment == "iShares S&P 500 GBP Hedged ETF Acc"
        assert holding.ticker == "IGUS"
        assert holding.quantity == Decimal(100)
        assert portfolio.cash_gbp == Decimal("72.47")
        assert portfolio.stock_value_gbp == Decimal("17154.00")
        assert portfolio.total_value_gbp == Decimal("17226.47")


class TestParseTransactionHistory:
    """Cover the AJ Bell transaction history parser."""

    def test_classifies_and_signs_quantities(self, ajbell_dir: Path) -> None:
        """PURCHASE/SALE/DISTRIBUTION are classified; SALE flips sign."""
        txns = parse_transaction_history(ajbell_dir / "transactionhistory.csv")
        assert len(txns) == _EXPECTED_TXNS
        kinds = [t.kind for t in txns]
        assert kinds.count(AJBellTxnKind.PURCHASE) == 2  # noqa: PLR2004
        assert AJBellTxnKind.SALE in kinds
        assert AJBellTxnKind.DISTRIBUTION in kinds
        sale = next(t for t in txns if t.kind is AJBellTxnKind.SALE)
        assert sale.quantity == Decimal(-100)
        dist = next(t for t in txns if t.kind is AJBellTxnKind.DISTRIBUTION)
        assert dist.quantity is None


class TestLoadAccounts:
    """Cover the loader that slices the broker-wide ledger by portfolio."""

    def test_slice_matches_holdings_only(self, ajbell_dir: Path) -> None:
        """The per-account slice excludes the closed Vanguard position."""
        accounts = discover_accounts(root=ajbell_dir)
        assert len(accounts) == 1
        account = accounts[0]
        assert len(account.all_transactions) == _EXPECTED_TXNS
        # Vanguard SALE belongs to a closed position not in this snapshot.
        descs = {t.description for t in account.transactions}
        assert descs == {"iShares S&P 500 GBP Hedged ETF Acc"}


class TestReconcileAccount:
    """Cover replay-vs-snapshot reconciliation."""

    def test_matches_snapshot_and_lists_closed_positions(
        self, ajbell_dir: Path,
    ) -> None:
        """Replay sums to snapshot units; closed positions surface as unmatched."""
        accounts = discover_accounts(root=ajbell_dir)
        result = reconcile_account(accounts[0])
        assert result.ok
        assert len(result.per_ticker) == _EXPECTED_HOLDINGS
        delta = result.per_ticker[0]
        assert delta.replayed_units == Decimal(_EXPECTED_REPLAY_UNITS)
        assert delta.snapshot_units == Decimal(_EXPECTED_REPLAY_UNITS)
        assert delta.ok
        assert "vanguard lifestrategy 80 acc" in result.unmatched_trade_descriptions
