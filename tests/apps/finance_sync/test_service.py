"""Tests for the finance sync orchestration."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING

from personal_project.apps.finance_sync.models import (
    AccountBalance,
    AccountKind,
    FinancialAccount,
    SyncResult,
    Transaction,
)
from personal_project.apps.finance_sync.service import sync_provider
from personal_project.apps.finance_sync.store import FinanceStore

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class FakeProvider:
    """Simple offline provider used to test orchestration."""

    name: str = "fake"
    list_calls: int = 0
    balance_calls: int = 0
    transaction_calls: int = 0

    def list_accounts(self) -> list[FinancialAccount]:
        """Return one mocked account."""
        self.list_calls += 1
        return [
            FinancialAccount(self.name, "acc-1", "Card One", AccountKind.CREDIT_CARD),
        ]

    def get_balances(self, account_ids: list[str]) -> list[AccountBalance]:
        """Return one mocked balance."""
        self.balance_calls += 1
        assert account_ids == ["acc-1"]
        return [
            AccountBalance(
                provider=self.name,
                provider_account_id="acc-1",
                as_of=datetime.datetime(2026, 4, 16, 8, 30, tzinfo=datetime.UTC),
                current="100.00",
            )
        ]

    def get_transactions(
        self,
        account_ids: list[str],
        *,
        from_date: datetime.date | None = None,
        to_date: datetime.date | None = None,
    ) -> list[Transaction]:
        """Return one mocked transaction."""
        self.transaction_calls += 1
        assert account_ids == ["acc-1"]
        assert from_date == datetime.date(2026, 4, 1)
        assert to_date == datetime.date(2026, 4, 16)
        return [
            Transaction(
                provider=self.name,
                provider_account_id="acc-1",
                provider_transaction_id="tx-1",
                booked_at=datetime.datetime(2026, 4, 15, 18, 0, tzinfo=datetime.UTC),
                amount="-8.25",
                currency="GBP",
                description="Dinner",
            )
        ]


def test_sync_provider_persists_snapshot(tmp_path: Path) -> None:
    """The sync service should fetch once and persist normalized snapshots."""
    provider = FakeProvider()
    with FinanceStore(tmp_path / "finance.sqlite3") as store:
        result = sync_provider(
            provider,
            store,
            from_date=datetime.date(2026, 4, 1),
            to_date=datetime.date(2026, 4, 16),
        )

    assert isinstance(result, SyncResult)
    assert result.provider == "fake"
    assert result.accounts_seen == 1
    assert result.balances_seen == 1
    assert result.transactions_seen == 1
    assert result.cached_accounts == 1
    assert result.cached_balances == 1
    assert result.cached_transactions == 1
    assert provider.list_calls == 1
    assert provider.balance_calls == 1
    assert provider.transaction_calls == 1
