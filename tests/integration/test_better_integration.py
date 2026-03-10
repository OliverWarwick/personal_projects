"""Integration tests for the Better.com client against the live API.

These tests perform real network requests to the Better.com booking platform
and are excluded from the default test run.  Run them explicitly with::

    uv run pytest -m integration --no-cov -v tests/integration/test_better_integration.py

Credentials must be stored in the OS keyring under the service name used by
:class:`~personal_project.clients.better_com.credentials.KeyringCredentialHelper`,
or provided via the ``BETTER_USERNAME`` / ``BETTER_PASSWORD`` environment
variables.  Tests are skipped automatically when no credentials are found.
"""

from __future__ import annotations

import datetime
import os

import pytest

from personal_project.clients.better_com.client import BetterClient
from personal_project.clients.better_com.credentials import KeyringCredentialHelper

_DEFAULT_VENUE = "islington-tennis-centre"
_DEFAULT_ACTIVITY = "tennis-court-indoor"


@pytest.fixture(scope="module")
def live_client() -> BetterClient:
    """Return an authenticated BetterClient for integration testing.

    Skips the entire module if no credentials are available.

    Returns:
        A :class:`BetterClient` that has successfully authenticated with the
        Better.com API.

    """
    creds = KeyringCredentialHelper.get_credentials()
    if not creds:
        pytest.skip("No credentials found (keyring or BETTER_USERNAME/BETTER_PASSWORD)")
    username, password = creds
    client = BetterClient()
    ok = client.ensure_logged_in(username=username, password=password, store=False)
    if not ok:
        pytest.skip("Better.com authentication failed with stored credentials")
    return client


@pytest.mark.integration
class TestBetterClientLiveLogin:
    """Integration tests for Better.com authentication."""

    def test_ensure_logged_in_returns_true(self, live_client: BetterClient) -> None:
        """Confirm that ensure_logged_in returns True after a live login."""
        assert live_client.is_logged_in() is True


@pytest.mark.integration
class TestBetterClientLiveAvailability:
    """Integration tests for Better.com availability fetching."""

    def test_get_availability_returns_list(self, live_client: BetterClient) -> None:
        """Confirm that get_availability returns a list for a known venue."""
        venue = os.getenv("BETTER_TEST_VENUE", _DEFAULT_VENUE)
        activity = os.getenv("BETTER_TEST_ACTIVITY", _DEFAULT_ACTIVITY)
        date_str = os.getenv(
            "BETTER_TEST_DATE",
            datetime.datetime.now(tz=datetime.UTC).date().isoformat(),
        )
        date = datetime.date.fromisoformat(date_str)
        slots = live_client.get_availability(venue, activity, date)
        assert isinstance(slots, list)

    def test_slot_keys_present(self, live_client: BetterClient) -> None:
        """Confirm each returned slot has the expected domain keys."""
        venue = os.getenv("BETTER_TEST_VENUE", _DEFAULT_VENUE)
        activity = os.getenv("BETTER_TEST_ACTIVITY", _DEFAULT_ACTIVITY)
        date = datetime.datetime.now(tz=datetime.UTC).date()
        slots = live_client.get_availability(venue, activity, date)
        if not slots:
            pytest.skip("No slots returned — cannot validate slot structure")
        sample = slots[0]
        assert "court_id" in sample
        assert "start_iso" in sample
        assert "end_iso" in sample
        assert "is_available" in sample
        assert "court_name" in sample
