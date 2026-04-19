"""Tests for the CardCredentialHelper keyring wrapper.

Verifies that :class:`~personal_project.apps.tennis_court_booker.card_credentials.CardCredentialHelper`
correctly stores, retrieves, and deletes card details via the OS keyring, and
that it falls back to environment variables when keyring entries are absent.
All keyring calls are mocked so these tests never touch the real OS keyring.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import keyring.errors

from personal_project.apps.tennis_court_booker.card_credentials import CardCredentialHelper
from personal_project.apps.tennis_court_booker.models import CardDetails

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_CARD = CardDetails(
    card_number="4929000000006",
    expiry_mmyy="1225",
    security_code="123",
    cardholder_name="Oliver Warwick",
    billing_first_name="Oliver",
    billing_last_name="Warwick",
    billing_address_line_one="1 Test Street",
    billing_address_line_two="Flat 2",
    billing_city="London",
    billing_postcode="EC1A 1BB",
)

_KEYRING_VALUES: dict[str, str] = {
    "card_number":              "4929000000006",
    "expiry_mmyy":              "1225",
    "security_code":            "123",
    "cardholder_name":          "Oliver Warwick",
    "billing_first_name":       "Oliver",
    "billing_last_name":        "Warwick",
    "billing_address_line_one": "1 Test Street",
    "billing_address_line_two": "Flat 2",
    "billing_city":             "London",
    "billing_postcode":         "EC1A 1BB",
}


def _keyring_get_side_effect(_service: str, key: str) -> str | None:
    """Simulate keyring.get_password returning stored values.

    Args:
        _service: The keyring service name (ignored in this mock).
        key: The keyring entry key.

    Returns:
        The stored value if present, otherwise ``None``.

    """
    return _KEYRING_VALUES.get(key)


# ---------------------------------------------------------------------------
# get_card tests
# ---------------------------------------------------------------------------


class TestGetCard:
    """Tests for CardCredentialHelper.get_card."""

    def test_returns_card_details_from_keyring(self) -> None:
        """Return a CardDetails instance when all fields are in the keyring."""
        with patch(
            "personal_project.apps.tennis_court_booker.card_credentials.keyring.get_password",
            side_effect=_keyring_get_side_effect,
        ):
            card = CardCredentialHelper.get_card()
        assert card is not None
        assert card.card_number == "4929000000006"
        assert card.expiry_mmyy == "1225"
        assert card.security_code == "123"
        assert card.cardholder_name == "Oliver Warwick"
        assert card.billing_first_name == "Oliver"
        assert card.billing_last_name == "Warwick"
        assert card.billing_address_line_one == "1 Test Street"
        assert card.billing_address_line_two == "Flat 2"
        assert card.billing_city == "London"
        assert card.billing_postcode == "EC1A 1BB"

    def test_returns_none_when_required_field_missing(self) -> None:
        """Return None when a required field is absent from both keyring and env."""
        with (
            patch(
                "personal_project.apps.tennis_court_booker.card_credentials.keyring.get_password",
                return_value=None,
            ),
            patch.dict("os.environ", {}, clear=True),
        ):
            card = CardCredentialHelper.get_card()
        assert card is None

    def test_falls_back_to_env_vars_when_keyring_empty(self) -> None:
        """Return card details sourced from environment variables when keyring has no entries."""
        env = {
            "CARD_NUMBER":          "4929000000006",
            "CARD_EXPIRY_MMYY":     "1225",
            "CARD_CVV":             "123",
            "CARD_NAME":            "Oliver Warwick",
            "BILLING_FIRST_NAME":   "Oliver",
            "BILLING_LAST_NAME":    "Warwick",
            "BILLING_ADDRESS":      "1 Test Street",
            "BILLING_ADDRESS2":     "",
            "BILLING_CITY":         "London",
            "BILLING_POSTCODE":     "EC1A 1BB",
        }
        with (
            patch(
                "personal_project.apps.tennis_court_booker.card_credentials.keyring.get_password",
                return_value=None,
            ),
            patch.dict("os.environ", env),
        ):
            card = CardCredentialHelper.get_card()
        assert card is not None
        assert card.card_number == "4929000000006"

    def test_optional_address_line_two_defaults_to_empty_string(self) -> None:
        """Use an empty string for billing_address_line_two when not stored."""
        values_without_line2 = {k: v for k, v in _KEYRING_VALUES.items()
                                 if k != "billing_address_line_two"}

        def _get(_service: str, key: str) -> str | None:
            return values_without_line2.get(key)

        with (
            patch(
                "personal_project.apps.tennis_court_booker.card_credentials.keyring.get_password",
                side_effect=_get,
            ),
            patch.dict("os.environ", {}, clear=True),
        ):
            card = CardCredentialHelper.get_card()
        assert card is not None
        assert card.billing_address_line_two == ""

    def test_keyring_takes_precedence_over_env_vars(self) -> None:
        """Prefer the keyring value over the environment variable for any given field."""
        env = {"CARD_NUMBER": "env-number"}
        with (
            patch(
                "personal_project.apps.tennis_court_booker.card_credentials.keyring.get_password",
                side_effect=_keyring_get_side_effect,
            ),
            patch.dict("os.environ", env),
        ):
            card = CardCredentialHelper.get_card()
        assert card is not None
        assert card.card_number == "4929000000006"  # keyring value, not env


# ---------------------------------------------------------------------------
# set_card tests
# ---------------------------------------------------------------------------


class TestSetCard:
    """Tests for CardCredentialHelper.set_card."""

    def test_calls_set_password_for_each_field(self) -> None:
        """Call keyring.set_password once for each card field."""
        with patch(
            "personal_project.apps.tennis_court_booker.card_credentials.keyring.set_password"
        ) as mock_set:
            CardCredentialHelper.set_card(_CARD)
        assert mock_set.call_count == len(CardCredentialHelper._FIELDS)

    def test_stores_card_number_correctly(self) -> None:
        """Store the card_number field under the expected keyring key."""
        with patch(
            "personal_project.apps.tennis_court_booker.card_credentials.keyring.set_password"
        ) as mock_set:
            CardCredentialHelper.set_card(_CARD)
        mock_set.assert_any_call(CardCredentialHelper.SERVICE, "card_number", "4929000000006")

    def test_stores_security_code_correctly(self) -> None:
        """Store the security_code (CVV) field under the expected keyring key."""
        with patch(
            "personal_project.apps.tennis_court_booker.card_credentials.keyring.set_password"
        ) as mock_set:
            CardCredentialHelper.set_card(_CARD)
        mock_set.assert_any_call(CardCredentialHelper.SERVICE, "security_code", "123")


# ---------------------------------------------------------------------------
# delete_card tests
# ---------------------------------------------------------------------------


class TestDeleteCard:
    """Tests for CardCredentialHelper.delete_card."""

    def test_calls_delete_password_for_each_field(self) -> None:
        """Call keyring.delete_password once for each configured field."""
        with patch(
            "personal_project.apps.tennis_court_booker.card_credentials.keyring.delete_password"
        ) as mock_del:
            CardCredentialHelper.delete_card()
        assert mock_del.call_count == len(CardCredentialHelper._FIELDS)

    def test_silently_ignores_missing_entries(self) -> None:
        """Not raise when keyring.delete_password raises PasswordDeleteError."""
        with patch(
            "personal_project.apps.tennis_court_booker.card_credentials.keyring.delete_password",
            side_effect=keyring.errors.PasswordDeleteError("not found"),
        ):
            # Should not raise
            CardCredentialHelper.delete_card()

    def test_deletes_all_fields_even_when_some_missing(self) -> None:
        """Attempt to delete every field even if earlier deletions raise PasswordDeleteError."""
        mock_del = MagicMock(side_effect=keyring.errors.PasswordDeleteError("x"))
        with patch(
            "personal_project.apps.tennis_court_booker.card_credentials.keyring.delete_password",
            mock_del,
        ):
            CardCredentialHelper.delete_card()
        assert mock_del.call_count == len(CardCredentialHelper._FIELDS)
