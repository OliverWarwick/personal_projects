"""Tests for BankSyncService."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from personal_project.apps.bank_sync.config import BankSyncConfig
from personal_project.apps.bank_sync.service import BankSyncService
from personal_project.data.models import Account, Institution


@pytest.fixture
def mock_config() -> BankSyncConfig:
    """Provide a mock configuration."""
    config = Mock(spec=BankSyncConfig)
    config.secret_id = "test_id"
    config.secret_key = "test_key"
    config.database_url = "sqlite:///:memory:"
    return config


@pytest.fixture
def service(mock_config: BankSyncConfig) -> BankSyncService:
    """Provide a BankSyncService with an in-memory database."""
    return BankSyncService(mock_config)


def test_sync_institutions(service: BankSyncService) -> None:
    """Verify that institutions are synced and persisted."""
    mock_data = [
        {"id": "BANK1", "name": "Bank One"},
        {"id": "BANK2", "name": "Bank Two"},
    ]

    with patch("personal_project.apps.bank_sync.service.GoCardlessClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.list_institutions.return_value = mock_data

        count = service.sync_institutions("GB")

        assert count == 2
        with service.Session() as session:
            inst1 = session.get(Institution, "BANK1")
            assert inst1 is not None
            assert inst1.name == "Bank One"


def test_get_auth_link(service: BankSyncService) -> None:
    """Verify that the auth link is correctly retrieved."""
    with patch("personal_project.apps.bank_sync.service.GoCardlessClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.create_requisition.return_value = {"link": "https://auth.link"}

        link = service.get_auth_link("BANK1", "https://redirect", "ref1")

        assert link == "https://auth.link"
        mock_client.create_requisition.assert_called_once_with("BANK1", "https://redirect", "ref1")


def test_sync_requisition(service: BankSyncService) -> None:
    """Verify that requisition data (accounts, balances, transactions) is synced."""
    with patch("personal_project.apps.bank_sync.service.GoCardlessClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value

        # Mock requisition data
        mock_client.get_requisition.return_value = {"accounts": ["acc1"]}

        # Mock account metadata
        mock_client.get_account_metadata.return_value = {
            "iban": "GB123",
            "institution_id": "BANK1",
            "owner_name": "John Doe",
            "currency": "GBP",
        }

        # Mock balances
        mock_client.get_balances.return_value = [
            {"balanceAmount": {"amount": "100.00"}, "balanceType": "closingBooked"}
        ]

        # Mock transactions
        mock_client.get_transactions.return_value = {
            "booked": [
                {
                    "transactionId": "tx1",
                    "transactionAmount": {"amount": "-10.00"},
                    "remittanceInformationUnstructured": "Coffee",
                    "bookingDate": "2024-01-01",
                }
            ]
        }

        stats = service.sync_requisition("req1")

        assert stats["accounts"] == 1
        assert stats["balances"] == 1
        assert stats["transactions"] == 1

        with service.Session() as session:
            acc = session.get(Account, "acc1")
            assert acc is not None
            assert acc.iban == "GB123"
            assert len(acc.balances) == 1
            assert len(acc.transactions) == 1
            assert acc.transactions[0].description == "Coffee"
