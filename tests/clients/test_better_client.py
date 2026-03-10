"""Unit tests for the Better.com HTTP client.

Verifies that :class:`BetterClient` correctly authenticates, parses
availability responses, and exposes the expected domain fields.  All
network I/O is replaced with mocks so the tests run without a network
connection.
"""

from __future__ import annotations

import datetime
from unittest.mock import Mock, patch

import pytest

from personal_project.clients.better_com.client import BetterClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_SLOT: dict[str, object] = {
    "date": "2026-03-10",
    "starts_at": {"format_24_hour": "07:00"},
    "ends_at": {"format_24_hour": "08:00"},
    "composite_key": "ck1",
    "name": "Tennis Court - Indoor",
    "action_to_show": {"status": "BOOK"},
    "booking": None,
    "price": {"formatted_amount": "£40.00"},
}

_DATE = datetime.date(2026, 3, 10)


def _make_session(*, post_status: int = 200, get_status: int = 200) -> Mock:
    """Return a mock requests.Session configured for happy-path responses.

    Args:
        post_status: HTTP status code for POST calls (login endpoint).
        get_status: HTTP status code for GET calls (auth check / availability).

    Returns:
        A :class:`~unittest.mock.Mock` that mimics a requests ``Session``.

    """
    session = Mock()
    post_resp = Mock()
    post_resp.status_code = post_status
    post_resp.json.return_value = {"token": "tok123"}
    session.post.return_value = post_resp

    get_resp = Mock()
    get_resp.status_code = get_status
    get_resp.json.return_value = {"data": {"id": 1}}
    session.get.return_value = get_resp
    return session


# ---------------------------------------------------------------------------
# TestLogin
# ---------------------------------------------------------------------------


class TestLogin:
    """Tests for BetterClient.login."""

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_true_on_success(self, mock_cls: Mock) -> None:
        """Return True when the login POST returns a token."""
        mock_cls.return_value = _make_session()
        assert BetterClient().login("me@example.com", "secret") is True

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_sets_authorization_header(self, mock_cls: Mock) -> None:
        """Call headers.update with an Authorization key after a successful login."""
        session = _make_session()
        mock_cls.return_value = session
        BetterClient().login("me@example.com", "secret")
        all_keys = {k for call in session.headers.update.call_args_list for k in call[0][0]}
        assert "Authorization" in all_keys

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_false_on_bad_status(self, mock_cls: Mock) -> None:
        """Return False when the login POST returns a non-200 status code."""
        mock_cls.return_value = _make_session(post_status=401)
        assert BetterClient().login("me@example.com", "wrong") is False

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_false_when_no_token_in_response(self, mock_cls: Mock) -> None:
        """Return False when the 200 response body contains no token."""
        session = _make_session()
        session.post.return_value.json.return_value = {}
        mock_cls.return_value = session
        assert BetterClient().login("me@example.com", "secret") is False


# ---------------------------------------------------------------------------
# TestGetAvailability
# ---------------------------------------------------------------------------


class TestGetAvailability:
    """Tests for BetterClient.get_availability."""

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_list(self, mock_cls: Mock) -> None:
        """Return a list of slot dicts for a valid response."""
        session = _make_session()
        session.get.return_value.json.return_value = {"data": [dict(_SAMPLE_SLOT)]}
        mock_cls.return_value = session
        result = BetterClient().get_availability(
            "islington-tennis-centre", "tennis-court-indoor", _DATE
        )
        assert isinstance(result, list)

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_court_id_extracted(self, mock_cls: Mock) -> None:
        """Map composite_key from the API response to the court_id field."""
        session = _make_session()
        session.get.return_value.json.return_value = {"data": [dict(_SAMPLE_SLOT)]}
        mock_cls.return_value = session
        result = BetterClient().get_availability(
            "islington-tennis-centre", "tennis-court-indoor", _DATE
        )
        assert result[0]["court_id"] == "ck1"

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_is_available_true_for_bookable_slot(self, mock_cls: Mock) -> None:
        """Set is_available=True when action_to_show.status is 'BOOK'."""
        session = _make_session()
        session.get.return_value.json.return_value = {"data": [dict(_SAMPLE_SLOT)]}
        mock_cls.return_value = session
        result = BetterClient().get_availability(
            "islington-tennis-centre", "tennis-court-indoor", _DATE
        )
        assert result[0]["is_available"] is True

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_is_available_false_for_non_book_status(self, mock_cls: Mock) -> None:
        """Set is_available=False when action_to_show.status is not 'BOOK'."""
        slot = dict(_SAMPLE_SLOT)
        slot["action_to_show"] = {"status": "FULL"}
        session = _make_session()
        session.get.return_value.json.return_value = {"data": [slot]}
        mock_cls.return_value = session
        result = BetterClient().get_availability(
            "islington-tennis-centre", "tennis-court-indoor", _DATE
        )
        assert result[0]["is_available"] is False

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_start_iso_populated(self, mock_cls: Mock) -> None:
        """Populate start_iso as a full ISO datetime string."""
        session = _make_session()
        session.get.return_value.json.return_value = {"data": [dict(_SAMPLE_SLOT)]}
        mock_cls.return_value = session
        result = BetterClient().get_availability(
            "islington-tennis-centre", "tennis-court-indoor", _DATE
        )
        assert "T" in result[0]["start_iso"]

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_price_extracted(self, mock_cls: Mock) -> None:
        """Extract the formatted price string from the price dict."""
        session = _make_session()
        session.get.return_value.json.return_value = {"data": [dict(_SAMPLE_SLOT)]}
        mock_cls.return_value = session
        result = BetterClient().get_availability(
            "islington-tennis-centre", "tennis-court-indoor", _DATE
        )
        assert result[0]["price"] == "£40.00"

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_empty_data_returns_empty_list(self, mock_cls: Mock) -> None:
        """Return an empty list when the API returns data: []."""
        session = _make_session()
        session.get.return_value.json.return_value = {"data": []}
        mock_cls.return_value = session
        result = BetterClient().get_availability(
            "islington-tennis-centre", "tennis-court-indoor", _DATE
        )
        assert result == []

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_non_200_returns_empty_list(self, mock_cls: Mock) -> None:
        """Return an empty list when the availability GET returns a non-200 status."""
        session = _make_session(get_status=403)
        mock_cls.return_value = session
        result = BetterClient().get_availability(
            "islington-tennis-centre", "tennis-court-indoor", _DATE
        )
        assert result == []


# ---------------------------------------------------------------------------
# TestIsLoggedIn
# ---------------------------------------------------------------------------


class TestIsLoggedIn:
    """Tests for BetterClient.is_logged_in."""

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_true_when_auth_endpoint_200(self, mock_cls: Mock) -> None:
        """Return True when the auth user endpoint responds with 200."""
        mock_cls.return_value = _make_session()
        assert BetterClient().is_logged_in() is True

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_false_when_auth_endpoint_401(self, mock_cls: Mock) -> None:
        """Return False when the auth user endpoint responds with 401."""
        mock_cls.return_value = _make_session(get_status=401)
        assert BetterClient().is_logged_in() is False


# ---------------------------------------------------------------------------
# TestEnsureLoggedIn
# ---------------------------------------------------------------------------


class TestEnsureLoggedIn:
    """Tests for BetterClient.ensure_logged_in."""

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_true_when_already_logged_in(self, mock_cls: Mock) -> None:
        """Return True immediately when is_logged_in is already True."""
        mock_cls.return_value = _make_session()
        client = BetterClient()
        assert client.ensure_logged_in() is True

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_skips_credentials_when_already_logged_in(self, mock_cls: Mock) -> None:
        """Not call login when is_logged_in already returns True."""
        session = _make_session()
        mock_cls.return_value = session
        BetterClient().ensure_logged_in()
        session.post.assert_not_called()

    @patch("personal_project.clients.better_com.client.KeyringCredentialHelper")
    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_false_when_no_credentials(
        self, mock_cls: Mock, mock_creds: Mock
    ) -> None:
        """Return False when not logged in and no credentials are available."""
        session = _make_session(get_status=401)
        mock_cls.return_value = session
        mock_creds.get_credentials.return_value = None
        assert BetterClient().ensure_logged_in() is False

    @pytest.mark.parametrize("status", [200])
    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_true_after_successful_login(
        self, mock_cls: Mock, status: int  # noqa: ARG002
    ) -> None:
        """Return True after successfully logging in with stored credentials."""
        # First GET (is_logged_in check) returns 401; POST (login) returns 200;
        # subsequent GET (is_logged_in re-check) returns 200.
        session = Mock()
        get_401 = Mock(status_code=401)
        get_401.json.return_value = {}
        get_200 = Mock(status_code=200)
        get_200.json.return_value = {"data": {"id": 1}}
        session.get.side_effect = [get_401, get_200]
        post_resp = Mock(status_code=200)
        post_resp.json.return_value = {"token": "tok"}
        session.post.return_value = post_resp
        mock_cls.return_value = session

        with patch(
            "personal_project.clients.better_com.client.KeyringCredentialHelper"
        ) as mock_creds:
            mock_creds.get_credentials.return_value = ("user@example.com", "pass")
            result = BetterClient().ensure_logged_in()

        assert result is True
