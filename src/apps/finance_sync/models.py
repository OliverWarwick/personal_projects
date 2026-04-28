"""Domain models for aggregated financial account data."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class AccountKind(StrEnum):
    """High-level account kind for normalized financial data."""

    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    BANK_ACCOUNT = "bank_account"


@dataclass(frozen=True)
class FinancialAccount:
    """A normalized account exposed by an aggregation provider."""

    provider: str
    provider_account_id: str
    display_name: str
    kind: AccountKind
    currency: str | None = None


@dataclass(frozen=True)
class AccountBalance:
    """A normalized account balance snapshot."""

    provider: str
    provider_account_id: str
    as_of: datetime
    current: str
    available: str | None = None
    limit: str | None = None


@dataclass(frozen=True)
class Transaction:
    """A normalized financial transaction."""

    provider: str
    provider_account_id: str
    provider_transaction_id: str
    booked_at: datetime
    amount: str
    currency: str
    description: str
    pending: bool = False
    merchant_name: str | None = None


@dataclass(frozen=True)
class SyncResult:
    """Summary of a sync run."""

    provider: str
    accounts_seen: int
    balances_seen: int
    transactions_seen: int
    cached_accounts: int
    cached_balances: int
    cached_transactions: int
