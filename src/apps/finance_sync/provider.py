"""Provider interface for open-banking style aggregators."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import date

    from src.apps.finance_sync.models import (
        AccountBalance,
        FinancialAccount,
        Transaction,
    )


class FinanceProvider(Protocol):
    """Minimal interface a financial data aggregator must provide."""

    name: str

    def list_accounts(self) -> list[FinancialAccount]:
        """Return normalized accounts available to the authenticated user."""

    def get_balances(self, account_ids: list[str]) -> list[AccountBalance]:
        """Return balance snapshots for the requested accounts."""

    def get_transactions(
        self,
        account_ids: list[str],
        *,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[Transaction]:
        """Return transactions for the requested accounts."""
