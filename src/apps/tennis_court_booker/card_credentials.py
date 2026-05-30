"""OS keyring credential helper for tennis court booker card details.

Provides :class:`CardCredentialHelper`, a thin wrapper around the ``keyring``
library that stores and retrieves payment card details and billing address from
the operating system's secure credential store (e.g. macOS Keychain).

Each card field is stored as a separate keyring entry under the service name
``"tennis-court-booker"``.  Sensitive values such as the card number and CVV
are never written to disk in plain text; they exist only inside the OS keyring.

Falls back to environment variables (``CARD_NUMBER``, ``CARD_EXPIRY_MMYY``,
``CARD_CVV``, ``CARD_NAME``, ``BILLING_FIRST_NAME``, ``BILLING_LAST_NAME``,
``BILLING_ADDRESS``, ``BILLING_ADDRESS2``, ``BILLING_CITY``,
``BILLING_POSTCODE``) when keyring entries are absent, which is convenient for
CI environments.
"""

from __future__ import annotations

import contextlib
import os

import keyring

from src.apps.tennis_court_booker.models import CardDetails


class CardCredentialHelper:
    """Store and retrieve payment card details via the OS keyring.

    Credentials are stored under the service identifier
    ``"tennis-court-booker"``.  Each :class:`~models.CardDetails` field maps
    to a distinct keyring entry keyed by a fixed field name string.  When a
    keyring entry is absent the helper falls back to a corresponding
    environment variable.

    Typical usage::

        # Store card details once (e.g. via the card-credentials subcommand):
        CardCredentialHelper.set_card(card)

        # Retrieve them at booking time:
        card = CardCredentialHelper.get_card()
        if card is None:
            raise RuntimeError("No card details found — run: tennis-court-booker card-credentials set")
    """

    SERVICE: str = "tennis-court-booker"

    # Keyring key → (env var name, required)
    _FIELDS: tuple[tuple[str, str, bool], ...] = (
        ("card_number",              "CARD_NUMBER",          True),
        ("expiry_mmyy",              "CARD_EXPIRY_MMYY",     True),
        ("security_code",            "CARD_CVV",             True),
        ("cardholder_name",          "CARD_NAME",            True),
        ("billing_first_name",       "BILLING_FIRST_NAME",   True),
        ("billing_last_name",        "BILLING_LAST_NAME",    True),
        ("billing_address_line_one", "BILLING_ADDRESS",      True),
        ("billing_address_line_two", "BILLING_ADDRESS2",     False),
        ("billing_city",             "BILLING_CITY",         True),
        ("billing_postcode",         "BILLING_POSTCODE",     True),
    )

    @classmethod
    def _get_field(cls, key: str, env_var: str) -> str | None:
        """Return the value of a single card field from the keyring or env.

        Checks the OS keyring first; falls back to the named environment
        variable when the keyring entry is absent.

        Args:
            key: The keyring entry name (e.g. ``"card_number"``).
            env_var: The environment variable to fall back to.

        Returns:
            The field value as a string, or ``None`` if neither source has it.

        """
        value = keyring.get_password(cls.SERVICE, key)
        if value:
            return value
        return os.getenv(env_var)

    @classmethod
    def get_card(cls) -> CardDetails | None:
        """Return a :class:`~models.CardDetails` built from stored credentials.

        Reads each required card field from the OS keyring, falling back to
        environment variables.  Returns ``None`` if any required field is
        missing.

        Returns:
            A fully-populated :class:`~models.CardDetails` on success,
            ``None`` when one or more required fields cannot be resolved.

        """
        values: dict[str, str] = {}
        for key, env_var, required in cls._FIELDS:
            raw = cls._get_field(key, env_var)
            if raw is None:
                if required:
                    return None
                values[key] = ""
            else:
                values[key] = raw

        return CardDetails(
            card_number=values["card_number"],
            expiry_mmyy=values["expiry_mmyy"],
            security_code=values["security_code"],
            cardholder_name=values["cardholder_name"],
            billing_first_name=values["billing_first_name"],
            billing_last_name=values["billing_last_name"],
            billing_address_line_one=values["billing_address_line_one"],
            billing_address_line_two=values["billing_address_line_two"],
            billing_city=values["billing_city"],
            billing_postcode=values["billing_postcode"],
        )

    @classmethod
    def set_card(cls, card: CardDetails) -> None:
        """Persist all card fields to the OS keyring.

        Stores each :class:`~models.CardDetails` field as a separate keyring
        entry so that sensitive data (card number, CVV) is held only in the
        OS secure store.

        Args:
            card: The :class:`~models.CardDetails` instance whose fields
                should be saved.

        """
        field_values: dict[str, str] = {
            "card_number":              card.card_number,
            "expiry_mmyy":              card.expiry_mmyy,
            "security_code":            card.security_code,
            "cardholder_name":          card.cardholder_name,
            "billing_first_name":       card.billing_first_name,
            "billing_last_name":        card.billing_last_name,
            "billing_address_line_one": card.billing_address_line_one,
            "billing_address_line_two": card.billing_address_line_two,
            "billing_city":             card.billing_city,
            "billing_postcode":         card.billing_postcode,
        }
        for key, value in field_values.items():
            keyring.set_password(cls.SERVICE, key, value)

    @classmethod
    def delete_card(cls) -> None:
        """Remove all stored card fields from the OS keyring.

        Silently skips any entry that is not present (e.g. optional fields
        that were never stored).

        """
        for key, _env_var, _required in cls._FIELDS:
            with contextlib.suppress(keyring.errors.PasswordDeleteError):  # type: ignore[attr-defined]
                keyring.delete_password(cls.SERVICE, key)
