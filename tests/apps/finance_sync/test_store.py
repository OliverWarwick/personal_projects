"""Tests for the SQLite finance store."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from personal_project.apps.finance_sync.models import (
    AccountBalance,
    AccountKind,
    FinancialAccount,
    Transaction,
)
from personal_project.apps.finance_sync.store import FinanceStore

if TYPE_CHECKING:
    from pathlib import Path


def test_store_persists_and_updates_rows(tmp_path: Path) -> None:
    """The store should upsert accounts, balances, and transactions."""
    store_path = tmp_path / "finance.sqlite3"
    with FinanceStore(store_path) as store:
        accounts = [
            FinancialAccount("gocardless", "acc-1", "Main Card", AccountKind.CREDIT_CARD),
        ]
        balances = [
            AccountBalance(
                provider="gocardless",
                provider_account_id="acc-1",
                as_of=datetime.datetime(2026, 4, 16, 8, 30, tzinfo=datetime.UTC),
                current="12.34",
                available="10.00",
                limit="5000.00",
            ),
        ]
        transactions = [
            Transaction(
                provider="gocardless",
                provider_account_id="acc-1",
                provider_transaction_id="tx-1",
                booked_at=datetime.datetime(2026, 4, 15, 18, 0, tzinfo=datetime.UTC),
                amount="-4.50",
                currency="GBP",
                description="Coffee Shop",
                pending=False,
                merchant_name="Coffee Shop",
            ),
        ]

        assert store.upsert_accounts(accounts) == 1
        assert store.upsert_balances(balances) == 1
        assert store.upsert_transactions(transactions) == 1

        updated_accounts = [
            FinancialAccount("gocardless", "acc-1", "Main Credit Card", AccountKind.CREDIT_CARD),
        ]
        assert store.upsert_accounts(updated_accounts) == 1

    with FinanceStore(store_path) as store:
        rows = store.fetch_accounts()
        assert len(rows) == 1
        assert rows[0]["display_name"] == "Main Credit Card"
        assert rows[0]["kind"] == AccountKind.CREDIT_CARD.value
