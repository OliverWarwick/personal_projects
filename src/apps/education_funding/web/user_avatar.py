"""Helpers that derive an avatar (initials + colour) from a username.

Mirrors the cadet-avatar pattern in ``cadets.py``: the same warm palette
is used so the client hero avatar visually fits alongside the rest of
the app.
"""

from __future__ import annotations

import hashlib

# Same palette as ``cadets._AVATAR_PALETTE`` — kept in lock-step so client
# and cadet avatars share a coherent visual language.
_USER_AVATAR_PALETTE: tuple[str, ...] = (
    "#C96342",
    "#7A8B6F",
    "#5B7BA3",
    "#A66B8C",
    "#B58A3F",
)


def username_initials(username: str) -> str:
    """Return up to two uppercase initials drawn from a username.

    Strips non-alphabetic characters so something like ``"ow6"`` yields
    ``"OW"``. Falls back to the raw first two characters if the username
    contains no letters.

    Args:
        username: The signed-in user's username.

    Returns:
        A 1-2 character uppercase initials string.

    """
    letters = "".join(ch for ch in username if ch.isalpha())
    if letters:
        return letters[:2].upper()
    return username[:2].upper()


def username_avatar_color_hex(username: str) -> str:
    """Return a stable palette colour for the user's avatar.

    Hashed from the username so the colour stays consistent across
    sessions and processes.

    Args:
        username: The signed-in user's username.

    Returns:
        A ``#RRGGBB`` colour string drawn from the warm palette.

    """
    digest = hashlib.sha256(username.encode("utf-8")).digest()
    return _USER_AVATAR_PALETTE[digest[0] % len(_USER_AVATAR_PALETTE)]
