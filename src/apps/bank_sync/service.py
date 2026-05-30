"""Service layer for bank data synchronization.

This module provides the core logic for fetching data from the GoCardless API
and persisting it to the local SQLite database.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.clients.gocardless.client import GoCardlessClient
from src.data.models import Account, Balance, Base, Institution, Transaction

if TYPE_CHECKING:
    from src.apps.bank_sync.config import BankSyncConfig

logger = logging.getLogger(__name__)


class BankSyncService:
    """Service to manage bank data synchronization."""

    def __init__(self, config: BankSyncConfig) -> None:
        """Initialise the service with configuration.

        Args:
            config: BankSyncConfig instance.

        """
        self.config = config
        self.engine = create_engine(config.database_url)
        self.Session = sessionmaker(bind=self.engine)
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create database tables if they do not exist."""
        Base.metadata.create_all(self.engine)

    def _get_client(self) -> GoCardlessClient:
        """Create and return a GoCardless API client.

        Returns:
            An initialised GoCardlessClient.

        Raises:
            RuntimeError: If credentials are missing.

        """
        if not self.config.secret_id or not self.config.secret_key:
            raise RuntimeError("GoCardless credentials (SECRET_ID, SECRET_KEY) are not configured")
        return GoCardlessClient(self.config.secret_id, self.config.secret_key)

    def sync_institutions(self, country: str = "GB") -> int:
        """Sync the list of supported institutions for a country.

        Args:
            country: Two-letter country code.

        Returns:
            Number of institutions synced.

        """
        client = self._get_client()
        institutions_data = client.list_institutions(country)

        with self.Session() as session:
            for inst_data in institutions_data:
                inst_id = str(inst_data.get("id"))
                inst = session.get(Institution, inst_id)
                if not inst:
                    inst = Institution(id=inst_id, name=str(inst_data.get("name")))
                    session.add(inst)
                else:
                    inst.name = str(inst_data.get("name"))
            session.commit()

        return len(institutions_data)

    def get_auth_link(self, institution_id: str, redirect_url: str, reference: str) -> str:
        """Generate an authorization link for a bank.

        Args:
            institution_id: The ID of the institution to link.
            redirect_url: Where the user is redirected after auth.
            reference: Internal reference.

        Returns:
            The authentication URL.

        """
        client = self._get_client()
        requisition = client.create_requisition(institution_id, redirect_url, reference)
        return str(requisition.get("link"))

    def sync_requisition(self, requisition_id: str) -> dict[str, int]:
        """Fetch and persist all data for an authorized requisition.

        Args:
            requisition_id: The ID of the authorized requisition.

        Returns:
            Summary of synced items (accounts, balances, transactions).

        """
        client = self._get_client()
        req_data = client.get_requisition(requisition_id)
        account_ids = list(req_data.get("accounts", []))

        stats = {"accounts": 0, "balances": 0, "transactions": 0}

        with self.Session() as session:
            for account_id in account_ids:
                # 1. Sync Account Metadata
                acc_metadata = client.get_account_metadata(account_id)
                acc = session.get(Account, account_id)
                if not acc:
                    acc = Account(
                        id=account_id,
                        iban=acc_metadata.get("iban"),
                        institution_id=acc_metadata.get("institution_id"),
                        name=acc_metadata.get("owner_name"),
                        currency=acc_metadata.get("currency"),
                    )
                    session.add(acc)
                    stats["accounts"] += 1

                # 2. Sync Balances
                balances_data = client.get_balances(account_id)
                for b_data in balances_data:
                    amount_data = b_data.get("balanceAmount", {})
                    balance = Balance(
                        account_id=account_id,
                        amount=str(amount_data.get("amount")),
                        date=datetime.now(timezone.utc),  # Or parse from b_data if available
                    )
                    session.add(balance)
                    stats["balances"] += 1

                # 3. Sync Transactions
                transactions_data = client.get_transactions(account_id)
                booked = transactions_data.get("booked", [])
                for t_data in booked:
                    t_id = t_data.get("transactionId") or t_data.get("internalTransactionId")
                    if not t_id:
                        continue

                    existing_t = session.get(Transaction, t_id)
                    if not existing_t:
                        amount_data = t_data.get("transactionAmount", {})
                        transaction = Transaction(
                            id=str(t_id),
                            account_id=account_id,
                            amount=str(amount_data.get("amount")),
                            description=str(
                                t_data.get("remittanceInformationUnstructured", "No description")
                            ),
                            date=datetime.fromisoformat(
                                str(t_data.get("bookingDate", datetime.now(timezone.utc).isoformat()))
                            ),
                            category=t_data.get("proprietaryBankTransactionCode"),
                        )
                        session.add(transaction)
                        stats["transactions"] += 1

            session.commit()

        return stats
