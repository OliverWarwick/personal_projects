"""SQLite-backed cache for normalized financial data."""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personal_project.apps.finance_sync.models import (
        AccountBalance,
        FinancialAccount,
        Transaction,
    )


class FinanceStore:
    """Persist normalized financial data in SQLite."""

    def __init__(self, path: str | Path) -> None:
        """Create a store backed by the SQLite database at *path*."""
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def __enter__(self) -> FinanceStore:
        """Return the store for use as a context manager."""
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Close the store when leaving a context manager."""
        self.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS accounts (
                provider TEXT NOT NULL,
                provider_account_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                currency TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (provider, provider_account_id)
            );

            CREATE TABLE IF NOT EXISTS balances (
                provider TEXT NOT NULL,
                provider_account_id TEXT NOT NULL,
                as_of TEXT NOT NULL,
                current TEXT NOT NULL,
                available TEXT,
                limit_amount TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (provider, provider_account_id)
            );

            CREATE TABLE IF NOT EXISTS transactions (
                provider TEXT NOT NULL,
                provider_account_id TEXT NOT NULL,
                provider_transaction_id TEXT NOT NULL,
                booked_at TEXT NOT NULL,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                description TEXT NOT NULL,
                pending INTEGER NOT NULL,
                merchant_name TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (provider, provider_transaction_id)
            );
            """
        )
        self._conn.commit()

    def upsert_accounts(self, accounts: list[FinancialAccount]) -> int:
        """Insert or update a batch of accounts."""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        self._conn.executemany(
            """
            INSERT INTO accounts (
                provider, provider_account_id, display_name, kind, currency, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, provider_account_id) DO UPDATE SET
                display_name=excluded.display_name,
                kind=excluded.kind,
                currency=excluded.currency,
                updated_at=excluded.updated_at
            """,
            [
                (
                    account.provider,
                    account.provider_account_id,
                    account.display_name,
                    account.kind.value,
                    account.currency,
                    now,
                )
                for account in accounts
            ],
        )
        self._conn.commit()
        return len(accounts)

    def upsert_balances(self, balances: list[AccountBalance]) -> int:
        """Insert or update a batch of balance snapshots."""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        self._conn.executemany(
            """
            INSERT INTO balances (
                provider, provider_account_id, as_of, current, available, limit_amount, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, provider_account_id) DO UPDATE SET
                as_of=excluded.as_of,
                current=excluded.current,
                available=excluded.available,
                limit_amount=excluded.limit_amount,
                updated_at=excluded.updated_at
            """,
            [
                (
                    balance.provider,
                    balance.provider_account_id,
                    balance.as_of.isoformat(),
                    balance.current,
                    balance.available,
                    balance.limit,
                    now,
                )
                for balance in balances
            ],
        )
        self._conn.commit()
        return len(balances)

    def upsert_transactions(self, transactions: list[Transaction]) -> int:
        """Insert or update a batch of transactions."""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        self._conn.executemany(
            """
            INSERT INTO transactions (
                provider, provider_account_id, provider_transaction_id,
                booked_at, amount, currency, description, pending, merchant_name, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, provider_transaction_id) DO UPDATE SET
                provider_account_id=excluded.provider_account_id,
                booked_at=excluded.booked_at,
                amount=excluded.amount,
                currency=excluded.currency,
                description=excluded.description,
                pending=excluded.pending,
                merchant_name=excluded.merchant_name,
                updated_at=excluded.updated_at
            """,
            [
                (
                    transaction.provider,
                    transaction.provider_account_id,
                    transaction.provider_transaction_id,
                    transaction.booked_at.isoformat(),
                    transaction.amount,
                    transaction.currency,
                    transaction.description,
                    int(transaction.pending),
                    transaction.merchant_name,
                    now,
                )
                for transaction in transactions
            ],
        )
        self._conn.commit()
        return len(transactions)

    def fetch_accounts(self) -> list[sqlite3.Row]:
        """Return cached account rows for test/debug use."""
        return list(self._conn.execute("SELECT * FROM accounts ORDER BY provider_account_id"))
