"""Tests for the GoCardless provider adapter."""

from __future__ import annotations

import datetime
from typing import Any

from personal_project.apps.finance_sync.gocardless import (
    GoCardlessConfig,
    GoCardlessProvider,
)

EXPECTED_CALLS = 4


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def post(self, url: str, json: dict[str, Any], timeout: int) -> _FakeResponse:
        _ = json, timeout
        self.calls.append(("POST", url))
        return _FakeResponse({"access": "access-token"})

    def get(
        self,
        url: str,
        headers: dict[str, str],
        timeout: int,
    ) -> _FakeResponse:
        _ = headers, timeout
        self.calls.append(("GET", url))
        if url.endswith("/requisitions/req-1/"):
            return _FakeResponse({"accounts": ["acc-1"]})
        if url.endswith("/accounts/acc-1/details/"):
            return _FakeResponse({"displayName": "Amex Gold", "currency": "GBP", "linkedAccounts": "cash"})
        if url.endswith("/accounts/acc-1/balances/"):
            return _FakeResponse(
                {
                    "balances": [
                        {
                            "balanceAmount": {"amount": 12.34, "currency": "GBP"},
                            "balanceType": "closingBooked",
                            "referenceDate": "2026-04-16",
                        }
                    ]
                }
            )
        if url.endswith("/accounts/acc-1/transactions/"):
            return _FakeResponse(
                {
                    "transactions": {
                        "booked": [
                            {
                                "transactionId": "tx-1",
                                "transactionAmount": {"amount": -4.5, "currency": "GBP"},
                                "bookingDate": "2026-04-15",
                                "remittanceInformationUnstructured": "Coffee",
                            }
                        ],
                        "pending": [],
                    }
                }
            )
        raise AssertionError(url)


def test_gocardless_provider_reads_accounts_balances_transactions() -> None:
    """The adapter should normalize the provider payloads."""
    session = _FakeSession()
    config = GoCardlessConfig(refresh_token="refresh", requisition_id="req-1")
    provider = GoCardlessProvider(config, session=session)  # type: ignore[arg-type]

    accounts = provider.list_accounts()
    balances = provider.get_balances(["acc-1"])
    transactions = provider.get_transactions(["acc-1"], from_date=datetime.date(2026, 4, 1))

    assert accounts[0].display_name == "Amex Gold"
    assert balances[0].current == "12.34"
    assert transactions[0].provider_transaction_id == "tx-1"
    assert len(session.calls) >= EXPECTED_CALLS
