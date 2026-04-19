"""Finance sync orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from personal_project.apps.finance_sync.models import SyncResult

if TYPE_CHECKING:
    from datetime import date

    from personal_project.apps.finance_sync.provider import FinanceProvider
    from personal_project.apps.finance_sync.store import FinanceStore


def sync_provider(
    provider: FinanceProvider,
    store: FinanceStore,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> SyncResult:
    """Fetch provider data and persist it to SQLite.

    The function deliberately performs a single account discovery call, then
    batches balance and transaction retrieval around those discovered accounts.
    That keeps the implementation simple and avoids repeated calls for the
    same data during development.
    """
    accounts = provider.list_accounts()
    account_ids = [account.provider_account_id for account in accounts]

    balances = provider.get_balances(account_ids) if account_ids else []
    transactions = (
        provider.get_transactions(account_ids, from_date=from_date, to_date=to_date)
        if account_ids
        else []
    )

    return SyncResult(
        provider=provider.name,
        accounts_seen=len(accounts),
        balances_seen=len(balances),
        transactions_seen=len(transactions),
        cached_accounts=store.upsert_accounts(accounts),
        cached_balances=store.upsert_balances(balances),
        cached_transactions=store.upsert_transactions(transactions),
    )
