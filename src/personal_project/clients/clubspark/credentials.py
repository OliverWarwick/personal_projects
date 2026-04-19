"""OS keyring credential helper for ClubSpark / LTA authentication.

Provides :class:`ClubSparkCredentialHelper`, a thin wrapper around the
``keyring`` library that stores and retrieves LTA account credentials from
the operating system's secure credential store (e.g. macOS Keychain).

Falls back to the ``CLUBSPARK_EMAIL`` and ``CLUBSPARK_PASSWORD`` environment
variables when keyring entries are absent, which is convenient for CI
environments.
"""

from __future__ import annotations

import os

import keyring


class ClubSparkCredentialHelper:
    """Retrieve, store, and delete LTA / ClubSpark credentials via the OS keyring.

    Credentials are stored under the service identifier ``"clubspark.lta.org.uk"``.
    When keyring entries are not available the helper falls back to the
    ``CLUBSPARK_EMAIL`` and ``CLUBSPARK_PASSWORD`` environment variables.
    """

    SERVICE: str = "clubspark.lta.org.uk"
    _EMAIL_KEY: str = "__email__"

    @classmethod
    def get_credentials(cls, email: str | None = None) -> tuple[str, str] | None:
        """Return an ``(email, password)`` pair or ``None`` if unavailable.

        Looks up credentials in the following order:

        1. If both ``CLUBSPARK_EMAIL`` and ``CLUBSPARK_PASSWORD`` env vars are
           set and *email* is ``None``, return them directly.
        2. If ``CLUBSPARK_EMAIL`` is set (or *email* is provided), attempt to
           read the password from the OS keyring.
        3. If no env var or explicit email is given, look up the stored email
           from the keyring (written by :meth:`set_credentials`) and use it to
           fetch the password.
        4. Fall back to ``CLUBSPARK_PASSWORD`` env var as the password.

        Args:
            email: Optional LTA account email to look up.  When ``None`` the
                ``CLUBSPARK_EMAIL`` environment variable or the stored keyring
                email entry is used.

        Returns:
            An ``(email, password)`` tuple when credentials are found,
            ``None`` otherwise.

        """
        env_email: str | None = os.getenv("CLUBSPARK_EMAIL")
        env_pass: str | None = os.getenv("CLUBSPARK_PASSWORD")

        if email is None and env_email and env_pass:
            return env_email, env_pass

        if email is None and env_email:
            password = keyring.get_password(cls.SERVICE, env_email)
            if password:
                return env_email, password

        if email is None:
            stored_email = keyring.get_password(cls.SERVICE, cls._EMAIL_KEY)
            if stored_email:
                password = keyring.get_password(cls.SERVICE, stored_email)
                if password:
                    return stored_email, password

        if email:
            password = keyring.get_password(cls.SERVICE, email)
            if password:
                return email, password
            if env_pass:
                return email, env_pass

        return None

    @classmethod
    def set_credentials(cls, email: str, password: str) -> None:
        """Store *email* and *password* in the OS keyring.

        Also persists the email itself under a fixed key so that
        :meth:`get_credentials` can retrieve it without requiring the
        ``CLUBSPARK_EMAIL`` environment variable.

        Args:
            email: The LTA account email to store.
            password: The corresponding password.

        """
        keyring.set_password(cls.SERVICE, cls._EMAIL_KEY, email)
        keyring.set_password(cls.SERVICE, email, password)

    @classmethod
    def delete_credentials(cls, email: str) -> None:
        """Remove the stored credentials for *email* from the OS keyring.

        Args:
            email: The account email whose credentials should be deleted.

        Raises:
            keyring.errors.PasswordDeleteError: If no credentials are found
                for *email* under the service.

        """
        keyring.delete_password(cls.SERVICE, email)
