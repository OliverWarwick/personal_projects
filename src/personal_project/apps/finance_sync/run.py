"""Command-line entry point for finance sync."""

from __future__ import annotations

import argparse
import datetime
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from personal_project.apps.finance_sync.gocardless import (
    GoCardlessConfig,
    GoCardlessProvider,
)
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
    from personal_project.apps.finance_sync.provider import FinanceProvider


@dataclass
class SampleFinanceProvider:
    """Offline provider used for development and smoke testing."""

    name: str = "sample"

    def list_accounts(self) -> list[FinancialAccount]:
        """Return a small fixed account set."""
        return [
            FinancialAccount(self.name, "amex-1", "Amex Gold", AccountKind.CREDIT_CARD, "GBP"),
            FinancialAccount(self.name, "yonder-1", "Yonder", AccountKind.DEBIT_CARD, "GBP"),
        ]

    def get_balances(self, account_ids: list[str]) -> list[AccountBalance]:
        """Return fixed balances for the sample accounts."""
        now = datetime.datetime.now(datetime.UTC)
        balances_by_id: dict[str, AccountBalance] = {
            "amex-1": AccountBalance(self.name, "amex-1", now, "1234.56", None, "5000.00"),
            "yonder-1": AccountBalance(self.name, "yonder-1", now, "245.12", "245.12", None),
        }
        return [balances_by_id[account_id] for account_id in account_ids if account_id in balances_by_id]

    def get_transactions(
        self,
        account_ids: list[str],
        *,
        from_date: datetime.date | None = None,
        to_date: datetime.date | None = None,
    ) -> list[Transaction]:
        """Return fixed transactions for the sample accounts."""
        _ = from_date, to_date
        booked_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
        txs = [
            Transaction(
                self.name,
                "amex-1",
                "tx-amex-1",
                booked_at,
                "-18.40",
                "GBP",
                "Pret A Manger",
                pending=False,
                merchant_name="Pret A Manger",
            ),
            Transaction(
                self.name,
                "yonder-1",
                "tx-yonder-1",
                booked_at,
                "-42.00",
                "GBP",
                "Dinner",
                pending=False,
                merchant_name="Restaurant",
            ),
        ]
        return [tx for tx in txs if tx.provider_account_id in account_ids]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finance-sync")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="Run a finance sync into SQLite")
    sync_parser.add_argument(
        "--db",
        default="research/finance_cache.sqlite3",
        help="SQLite database path",
    )
    sync_parser.add_argument(
        "--provider",
        choices=("sample", "gocardless"),
        default="sample",
        help="Provider to use for now",
    )
    sync_parser.add_argument(
        "--from-date",
        dest="from_date",
        default=None,
        help="Optional start date YYYY-MM-DD",
    )
    sync_parser.add_argument(
        "--to-date",
        dest="to_date",
        default=None,
        help="Optional end date YYYY-MM-DD",
    )
    sync_parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output instead of a human summary",
    )
    return parser


def _parse_date(value: str | None) -> datetime.date | None:
    if value is None:
        return None
    return datetime.date.fromisoformat(value)


def _build_provider(name: str) -> FinanceProvider:
    if name == "sample":
        return SampleFinanceProvider()
    if name == "gocardless":
        return GoCardlessProvider(GoCardlessConfig.from_env())
    msg = f"Unsupported provider: {name}"
    raise ValueError(msg)


def _run_sync(args: argparse.Namespace) -> SyncResult:
    provider = _build_provider(args.provider)
    from_date = _parse_date(args.from_date)
    to_date = _parse_date(args.to_date)
    with FinanceStore(Path(args.db)) as store:
        return sync_provider(provider, store, from_date=from_date, to_date=to_date)


def main(argv: list[str] | None = None) -> int:
    """Run the finance sync CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "sync":
        result = _run_sync(args)
        if args.json:
            print(json.dumps(asdict(result), indent=2))
        else:
            print(f"Provider: {result.provider}")
            print(f"Accounts: {result.accounts_seen} discovered, {result.cached_accounts} cached")
            print(f"Balances: {result.balances_seen} discovered, {result.cached_balances} cached")
            print(
                "Transactions: "
                f"{result.transactions_seen} discovered, {result.cached_transactions} cached"
            )
        return 0

    msg = f"Unsupported command: {args.command}"
    raise ValueError(msg)
