"""OS keyring credential helper for Better.com authentication.

Provides :class:`KeyringCredentialHelper`, a thin wrapper around the
``keyring`` library that stores and retrieves Better.com account credentials
from the operating system's secure credential store (e.g. macOS Keychain).

Falls back to the ``BETTER_USERNAME`` and ``BETTER_PASSWORD`` environment
variables when keyring entries are absent, which is convenient for CI
environments.
"""

from __future__ import annotations

import os

import keyring


class KeyringCredentialHelper:
    """Retrieve, store, and delete Better.com credentials via the OS keyring.

    Credentials are stored under the service identifier ``"better.com"``.
    When keyring entries are not available the helper falls back to the
    ``BETTER_USERNAME`` and ``BETTER_PASSWORD`` environment variables.
    """

    SERVICE: str = "better.com"
    _USERNAME_KEY: str = "__username__"

    @classmethod
    def get_credentials(cls, username: str | None = None) -> tuple[str, str] | None:
        """Return a ``(username, password)`` pair or ``None`` if unavailable.

        Looks up credentials in the following order:

        1. If both ``BETTER_USERNAME`` and ``BETTER_PASSWORD`` env vars are
           set and *username* is ``None``, return them directly.
        2. If ``BETTER_USERNAME`` is set (or *username* is provided), attempt
           to read the password from the OS keyring.
        3. If no env var or explicit username is given, look up the stored
           username from the keyring (written by :meth:`set_credentials`) and
           use it to fetch the password.
        4. Fall back to ``BETTER_PASSWORD`` env var as the password.

        Args:
            username: Optional account email to look up.  When ``None`` the
                ``BETTER_USERNAME`` environment variable or the stored keyring
                username entry is used.

        Returns:
            A ``(username, password)`` tuple when credentials are found,
            ``None`` otherwise.

        """
        env_user: str | None = os.getenv("BETTER_USERNAME")
        env_pass: str | None = os.getenv("BETTER_PASSWORD")

        if username is None and env_user and env_pass:
            return env_user, env_pass

        if username is None and env_user:
            password = keyring.get_password(cls.SERVICE, env_user)
            if password:
                return env_user, password

        if username is None:
            stored_user = keyring.get_password(cls.SERVICE, cls._USERNAME_KEY)
            if stored_user:
                password = keyring.get_password(cls.SERVICE, stored_user)
                if password:
                    return stored_user, password

        if username:
            password = keyring.get_password(cls.SERVICE, username)
            if password:
                return username, password
            if env_pass:
                return username, env_pass

        return None

    @classmethod
    def set_credentials(cls, username: str, password: str) -> None:
        """Store *username* and *password* in the OS keyring.

        Also persists the username itself under a fixed key so that
        :meth:`get_credentials` can retrieve it without requiring the
        ``BETTER_USERNAME`` environment variable.

        Args:
            username: The Better.com account email to store.
            password: The corresponding password.

        """
        keyring.set_password(cls.SERVICE, cls._USERNAME_KEY, username)
        keyring.set_password(cls.SERVICE, username, password)

    @classmethod
    def delete_credentials(cls, username: str) -> None:
        """Remove the stored credentials for *username* from the OS keyring.

        Args:
            username: The account email whose credentials should be deleted.

        Raises:
            keyring.errors.PasswordDeleteError: If no credentials are found
                for *username* under the service.

        """
        keyring.delete_password(cls.SERVICE, username)
