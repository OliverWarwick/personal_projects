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


# ---------------------------------------------------------------------------
# TestGetUserId
# ---------------------------------------------------------------------------


class TestGetUserId:
    """Tests for BetterClient.get_user_id."""

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_integer_id(self, mock_cls: Mock) -> None:
        """Return the integer user ID from the auth user endpoint."""
        session = _make_session()
        session.get.return_value.json.return_value = {"data": {"id": 1741614}}
        mock_cls.return_value = session
        assert BetterClient().get_user_id() == 1741614  # noqa: PLR2004

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_none_on_non_200(self, mock_cls: Mock) -> None:
        """Return None when the auth user endpoint returns non-200."""
        mock_cls.return_value = _make_session(get_status=401)
        assert BetterClient().get_user_id() is None


# ---------------------------------------------------------------------------
# TestGetSlotDetails
# ---------------------------------------------------------------------------


class TestGetSlotDetails:
    """Tests for BetterClient.get_slot_details."""

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_first_slot(self, mock_cls: Mock) -> None:
        """Return the first item from the slots data array."""
        session = _make_session()
        session.get.return_value.json.return_value = {
            "data": [{"id": 87670583, "pricing_option_id": 1161, "cart_type": "activity"}]
        }
        mock_cls.return_value = session
        result = BetterClient().get_slot_details(
            "islington-tennis-centre", "tennis-court-indoor",
            _DATE, "18:00", "19:00", "abc123",
        )
        assert result is not None
        assert result["id"] == 87670583  # noqa: PLR2004

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_none_on_empty_data(self, mock_cls: Mock) -> None:
        """Return None when the API returns an empty data array."""
        session = _make_session()
        session.get.return_value.json.return_value = {"data": []}
        mock_cls.return_value = session
        assert BetterClient().get_slot_details(
            "islington-tennis-centre", "tennis-court-indoor",
            _DATE, "18:00", "19:00", "abc123",
        ) is None

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_none_on_non_200(self, mock_cls: Mock) -> None:
        """Return None when the slots endpoint returns non-200."""
        mock_cls.return_value = _make_session(get_status=404)
        assert BetterClient().get_slot_details(
            "islington-tennis-centre", "tennis-court-indoor",
            _DATE, "18:00", "19:00", "abc123",
        ) is None


# ---------------------------------------------------------------------------
# TestAddToCart
# ---------------------------------------------------------------------------


class TestAddToCart:
    """Tests for BetterClient.add_to_cart."""

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_cart_dict_on_success(self, mock_cls: Mock) -> None:
        """Return the cart response dict on a successful add."""
        session = _make_session()
        cart_resp = {"data": {"id": 100318498, "itemHash": "aGFzaA=="}}
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = cart_resp
        mock_cls.return_value = session
        result = BetterClient().add_to_cart(87670583, 1161, 1741614)
        assert result == cart_resp

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_none_on_non_200(self, mock_cls: Mock) -> None:
        """Return None when the cart endpoint returns non-200."""
        session = _make_session(post_status=422)
        mock_cls.return_value = session
        assert BetterClient().add_to_cart(87670583, 1161, 1741614) is None


# ---------------------------------------------------------------------------
# TestRemoveFromCart
# ---------------------------------------------------------------------------


class TestRemoveFromCart:
    """Tests for BetterClient.remove_from_cart."""

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_true_on_success(self, mock_cls: Mock) -> None:
        """Return True when the cart remove endpoint returns 200."""
        session = _make_session()
        session.post.return_value.status_code = 200
        mock_cls.return_value = session
        assert BetterClient().remove_from_cart([140779560], 1741614) is True

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_false_on_non_200(self, mock_cls: Mock) -> None:
        """Return False when the cart remove endpoint returns non-200."""
        session = _make_session(post_status=422)
        mock_cls.return_value = session
        assert BetterClient().remove_from_cart([140779560], 1741614) is False


# ---------------------------------------------------------------------------
# TestCheckoutPrepare
# ---------------------------------------------------------------------------


class TestCheckoutPrepare:
    """Tests for BetterClient.checkout_prepare."""

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_prepare_dict(self, mock_cls: Mock) -> None:
        """Return the prepare response dict with session_key and provider config."""
        session = _make_session()
        prep = {"session_key": "SK-UUID", "payment_provider": "Opayo",
                "payment_provider_configuration": {"url": "https://live.opayo.eu.elavon.com"}}
        session.get.return_value.json.return_value = prep
        mock_cls.return_value = session
        result = BetterClient().checkout_prepare()
        assert result is not None
        assert result["session_key"] == "SK-UUID"

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_none_on_non_200(self, mock_cls: Mock) -> None:
        """Return None when prepare endpoint returns non-200."""
        mock_cls.return_value = _make_session(get_status=403)
        assert BetterClient().checkout_prepare() is None


# ---------------------------------------------------------------------------
# TestTokeniseCardOpayo
# ---------------------------------------------------------------------------


class TestTokeniseCardOpayo:
    """Tests for BetterClient.tokenise_card_opayo."""

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_card_identifier_on_success(self, mock_cls: Mock) -> None:
        """Return the cardIdentifier string from Opayo on success."""
        session = _make_session()
        opayo_resp = Mock(status_code=201)
        opayo_resp.json.return_value = {"cardIdentifier": "CARD-ID-UUID", "cardType": "Visa"}
        session.post.return_value = opayo_resp
        mock_cls.return_value = session
        result = BetterClient().tokenise_card_opayo(
            "https://live.opayo.eu.elavon.com", "SESSION-KEY",
            "4929000000006", "1225", "123", "Oliver Warwick",
        )
        assert result == "CARD-ID-UUID"

    @patch("personal_project.clients.better_com.client.requests.Session")
    def test_returns_none_on_401(self, mock_cls: Mock) -> None:
        """Return None when Opayo returns 401."""
        session = _make_session()
        opayo_resp = Mock(status_code=401)
        opayo_resp.json.return_value = {"description": "Incorrect auth", "code": 1002}
        session.post.return_value = opayo_resp
        mock_cls.return_value = session
        result = BetterClient().tokenise_card_opayo(
            "https://live.opayo.eu.elavon.com", "BAD-KEY",
            "4929000000006", "1225", "123", "Oliver Warwick",
        )
        assert result is None
