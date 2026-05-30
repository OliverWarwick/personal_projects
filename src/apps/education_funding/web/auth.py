"""Authentication helpers for the education-funding web UI.

Holds the hardcoded MVP credentials and the session-inspection helpers used
by route handlers to gate access to role-specific dashboards. The credentials
here are deliberately trivial — this file is the single place to swap them
out when real authentication is wired up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from fastapi import Request


# Username -> password registry for the MVP. Trivially crackable on
# purpose — this is the single place to swap when real auth is wired up.
_VALID_CREDENTIALS: dict[str, str] = {
    "ow6": "ow6",
    "tom2": "tom2",
}

# Backward-compatible aliases so callers that previously imported the
# single-user constants keep working. ``ow6`` remains the canonical demo
# user.
VALID_USERNAME = "ow6"
VALID_PASSWORD = "ow6"  # noqa: S105 - intentional hardcoded credential for MVP

VALID_ROLES = frozenset({"investor", "client"})
"""The set of roles a visitor can sign in as."""


class SessionContext(TypedDict):
    """The shape of session data set on successful login.

    Attributes:
        username: The authenticated user's display name.
        role: The role the user signed in under (``investor`` or ``client``).

    """

    username: str
    role: str


def credentials_are_valid(username: str, password: str) -> bool:
    """Check whether the supplied credentials match a hardcoded MVP pair.

    Args:
        username: The username submitted via the login form.
        password: The password submitted via the login form.

    Returns:
        ``True`` if the username is registered and the password matches,
        otherwise ``False``.

    """
    expected = _VALID_CREDENTIALS.get(username)
    return expected is not None and password == expected


def get_session_context(request: Request, expected_role: str) -> SessionContext | None:
    """Return the session context if the request is authenticated as ``expected_role``.

    A request is considered authenticated when the session has both a
    ``username`` and a ``role``, and the role matches ``expected_role``.

    Args:
        request: The incoming Starlette/FastAPI request.
        expected_role: The role the caller is gating the route on
            (typically ``"investor"`` or ``"client"``).

    Returns:
        A populated ``SessionContext`` when the session is valid for the
        expected role, otherwise ``None``.

    """
    username = request.session.get("username")
    role = request.session.get("role")
    if not isinstance(username, str) or not isinstance(role, str):
        return None
    if role != expected_role:
        return None
    return {"username": username, "role": role}
