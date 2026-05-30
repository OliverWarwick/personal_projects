"""Tests for the username avatar helpers."""

from __future__ import annotations

from personal_project.apps.education_funding.web.user_avatar import (
    username_avatar_color_hex,
    username_initials,
)

EXPECTED_HEX_LENGTH = len("#000000")


class TestUsernameInitials:
    """Tests for ``username_initials``."""

    def test_strips_digits_and_uppercases(self) -> None:
        """A username like ``"ow6"`` yields ``"OW"``."""
        assert username_initials("ow6") == "OW"

    def test_caps_at_two_letters(self) -> None:
        """Long alphabetic usernames are clipped to two letters."""
        assert username_initials("abcdef") == "AB"

    def test_falls_back_to_raw_chars_when_no_letters(self) -> None:
        """A digits-only username falls back to its first two chars."""
        assert username_initials("1234") == "12"


class TestUsernameAvatarColorHex:
    """Tests for ``username_avatar_color_hex``."""

    def test_returns_six_digit_hex(self) -> None:
        """The returned colour is a valid ``#RRGGBB`` string."""
        colour = username_avatar_color_hex("ow6")

        assert colour.startswith("#")
        assert len(colour) == EXPECTED_HEX_LENGTH

    def test_deterministic(self) -> None:
        """The same username always hashes to the same colour."""
        assert username_avatar_color_hex("ow6") == username_avatar_color_hex("ow6")
