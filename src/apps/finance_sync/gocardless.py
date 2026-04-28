"""GoCardless Bank Account Data provider adapter."""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import requests

from src.apps.finance_sync.models import (
    AccountBalance,
    AccountKind,
    FinancialAccount,
    Transaction,
)

_BASE_URL = "https://bankaccountdata.gocardless.com/api/v2"
if TYPE_CHECKING:
    JsonDict = dict[str, Any]
else:
    JsonDict = dict[str, Any]


@dataclass
class GoCardlessConfig:
    """Configuration for GoCardless Bank Account Data access."""

    refresh_token: str
    requisition_id: str | None = None
    account_ids: list[str] | None = None

    @classmethod
    def from_env(cls) -> GoCardlessConfig:
        """Load config from environment variables."""
        refresh_token = os.environ["GOCARDLESS_REFRESH_TOKEN"]
        requisition_id = os.environ.get("GOCARDLESS_REQUISITION_ID")
        account_ids_raw = os.environ.get("GOCARDLESS_ACCOUNT_IDS")
        account_ids = (
            [item.strip() for item in account_ids_raw.split(",") if item.strip()]
            if account_ids_raw
            else None
        )
        return cls(refresh_token=refresh_token, requisition_id=requisition_id, account_ids=account_ids)


class GoCardlessProvider:
    """Fetch normalized financial data from GoCardless."""

    name = "gocardless"

    def __init__(
        self,
        config: GoCardlessConfig,
        *,
        session: requests.Session | None = None,
    ) -> None:
        """Create a provider with the given config and optional HTTP session."""
        self._config = config
        self._session = session or requests.Session()
        self._access_token: str | None = None

    def _headers(self) -> dict[str, str]:
        if not self._access_token:
            self._refresh_access_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    def _refresh_access_token(self) -> None:
        resp = self._session.post(
            f"{_BASE_URL}/token/refresh/",
            json={"refresh": self._config.refresh_token},
            timeout=15,
        )
        resp.raise_for_status()
        data = cast("JsonDict", resp.json())
        access = data.get("access")
        if not isinstance(access, str) or not access:
            msg = "GoCardless refresh token response did not contain access token"
            raise RuntimeError(msg)
        self._access_token = access

    def _get(self, path: str) -> dict[str, Any]:
        resp = self._session.get(f"{_BASE_URL}{path}", headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return cast("JsonDict", resp.json())

    def _iter_account_ids(self) -> list[str]:
        if self._config.account_ids:
            return list(self._config.account_ids)
        if not self._config.requisition_id:
            msg = "GoCardless config requires requisition_id or account_ids"
            raise ValueError(msg)
        requisition = self._get(f"/requisitions/{self._config.requisition_id}/")
        accounts = requisition.get("accounts", [])
        return [account_id for account_id in accounts if isinstance(account_id, str)]

    def list_accounts(self) -> list[FinancialAccount]:
        """Return normalized accounts discovered for the authenticated user."""
        accounts = []
        for account_id in self._iter_account_ids():
            details = self._get(f"/accounts/{account_id}/details/")
            name = self._extract_display_name(details) or account_id
            currency = self._extract_currency(details)
            kind = self._infer_kind(details)
            accounts.append(
                FinancialAccount(
                    provider=self.name,
                    provider_account_id=account_id,
                    display_name=name,
                    kind=kind,
                    currency=currency,
                )
            )
        return accounts

    def get_balances(self, account_ids: list[str]) -> list[AccountBalance]:
        """Return balance snapshots for the requested accounts."""
        result: list[AccountBalance] = []
        for account_id in account_ids:
            payload = self._get(f"/accounts/{account_id}/balances/")
            for balance in payload.get("balances", []):
                if not isinstance(balance, dict):
                    continue
                amount = balance.get("balanceAmount") or {}
                balance_amount = cast("JsonDict", amount)
                current = balance_amount.get("amount")
                currency = balance_amount.get("currency")
                reference_date = balance.get("referenceDate")
                if not isinstance(current, (int, float, str)) or not isinstance(currency, str):
                    continue
                as_of = (
                    datetime.datetime.fromisoformat(reference_date)
                    if isinstance(reference_date, str)
                    else datetime.datetime.now(datetime.UTC)
                )
                available = self._balance_if_type(balance, "openingAvailable")
                result.append(
                    AccountBalance(
                        provider=self.name,
                        provider_account_id=account_id,
                        as_of=as_of,
                        current=str(current),
                        available=available,
                        limit=self._balance_if_type(balance, "interimAvailable"),
                    )
                )
        return result

    def get_transactions(
        self,
        account_ids: list[str],
        *,
        from_date: datetime.date | None = None,
        to_date: datetime.date | None = None,
    ) -> list[Transaction]:
        """Return booked and pending transactions for the requested accounts."""
        result: list[Transaction] = []
        for account_id in account_ids:
            payload = self._get(f"/accounts/{account_id}/transactions/")
            for status in ("booked", "pending"):
                for raw in payload.get("transactions", {}).get(status, []):
                    if not isinstance(raw, dict):
                        continue
                    booked_at = self._transaction_date(raw)
                    if from_date and booked_at.date() < from_date:
                        continue
                    if to_date and booked_at.date() > to_date:
                        continue
                    amount = cast("JsonDict", raw.get("transactionAmount") or {})
                    tx_id = raw.get("transactionId") or raw.get("internalTransactionId")
                    if not isinstance(tx_id, str):
                        continue
                    currency = amount.get("currency")
                    tx_amount = amount.get("amount")
                    if not isinstance(currency, str) or not isinstance(tx_amount, (int, float, str)):
                        continue
                    description = self._transaction_description(raw)
                    result.append(
                        Transaction(
                            provider=self.name,
                            provider_account_id=account_id,
                            provider_transaction_id=tx_id,
                            booked_at=booked_at,
                            amount=str(tx_amount),
                            currency=currency,
                            description=description,
                            pending=status == "pending",
                            merchant_name=self._merchant_name(raw),
                        )
                    )
        return result

    @staticmethod
    def _extract_display_name(details: dict[str, Any]) -> str | None:
        name = details.get("displayName") or details.get("name")
        return name if isinstance(name, str) and name else None

    @staticmethod
    def _extract_currency(details: dict[str, Any]) -> str | None:
        currency = details.get("currency")
        return currency if isinstance(currency, str) and currency else None

    @staticmethod
    def _infer_kind(details: dict[str, Any]) -> AccountKind:
        cash_type = details.get("cashAccountType")
        if isinstance(cash_type, str) and cash_type:
            return AccountKind.DEBIT_CARD if "CARD" not in cash_type.upper() else AccountKind.CREDIT_CARD
        linked = details.get("linkedAccounts")
        if isinstance(linked, str) and linked:
            return AccountKind.CREDIT_CARD
        return AccountKind.BANK_ACCOUNT

    @staticmethod
    def _balance_if_type(balance: dict[str, Any], balance_type: str) -> str | None:
        if balance.get("balanceType") != balance_type:
            return None
        balance_amount = cast("JsonDict", balance.get("balanceAmount") or {})
        amount = balance_amount.get("amount")
        return str(amount) if isinstance(amount, (int, float, str)) else None

    @staticmethod
    def _transaction_date(raw: dict[str, Any]) -> datetime.datetime:
        for key in ("bookingDateTime", "bookingDate", "valueDateTime", "valueDate"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                if "T" in value:
                    return datetime.datetime.fromisoformat(value)
                return datetime.datetime.fromisoformat(f"{value}T00:00:00+00:00")
        return datetime.datetime.now(datetime.UTC)

    @staticmethod
    def _transaction_description(raw: dict[str, Any]) -> str:
        for key in (
            "remittanceInformationUnstructured",
            "additionalInformation",
            "remittanceInformationStructured",
            "purposeCode",
        ):
            value = raw.get(key)
            if isinstance(value, str) and value:
                return value
        return "GoCardless transaction"

    @staticmethod
    def _merchant_name(raw: dict[str, Any]) -> str | None:
        for key in ("creditorName", "debtorName", "ultimateCreditor", "ultimateDebtor"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                return value
        return None
