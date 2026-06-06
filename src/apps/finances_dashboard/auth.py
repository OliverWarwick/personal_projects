"""Username/password authentication for the finances dashboard.

Credentials are stored in a local SQLite file with PBKDF2-HMAC-SHA256 hashed
passwords.  Each user carries a set of permitted account IDs; an empty set
means the user may view all accounts.

The database file is always created with mode 0o600 (owner read/write only).
Use :func:`AuthDB.create_user` to add users, or run ``scripts/setup_finances_auth.py``
which bootstraps the two initial accounts.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    salt          TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS user_accounts (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id TEXT    NOT NULL,
    PRIMARY KEY (user_id, account_id)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_PBKDF2_ITERATIONS = 600_000


@dataclass(frozen=True, slots=True)
class AuthUser:
    """An authenticated user with their permitted accounts.

    Attributes:
        username: Login name.
        permitted_accounts: Frozenset of account IDs this user may access.
            An empty frozenset means the user may access all accounts.

    """

    username: str
    permitted_accounts: frozenset[str]

    @property
    def sees_all(self) -> bool:
        """True when the user has unrestricted account access."""
        return not self.permitted_accounts


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _PBKDF2_ITERATIONS,
    )
    return dk.hex()


class AuthDB:
    """SQLite-backed store for dashboard credentials and account permissions."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Create file before setting permissions so the mode applies on first open.
        db_path.touch(mode=0o600, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        # Re-apply in case the file pre-existed with loose permissions.
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Session secret
    # ------------------------------------------------------------------

    def session_secret(self) -> str:
        """Return a stable session-signing secret, generating it on first call."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'session_secret'",
            ).fetchone()
            if row:
                return row[0]
            secret = secrets.token_hex(32)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('session_secret', ?)",
                (secret,),
            )
            return secret

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def create_user(
        self,
        username: str,
        password: str,
        permitted_accounts: list[str],
    ) -> None:
        """Create or update a user.

        Args:
            username: Login name (case-sensitive).
            password: Plaintext password — hashed before storage.
            permitted_accounts: Account IDs the user may see.  Pass an empty
                list to grant access to all accounts.

        """
        salt = secrets.token_hex(16)
        pw_hash = _hash_password(password, salt)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users(username, password_hash, salt) VALUES (?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET "
                "  password_hash = excluded.password_hash, "
                "  salt          = excluded.salt",
                (username, pw_hash, salt),
            )
            (user_id,) = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,),
            ).fetchone()
            conn.execute("DELETE FROM user_accounts WHERE user_id = ?", (user_id,))
            conn.executemany(
                "INSERT INTO user_accounts(user_id, account_id) VALUES (?, ?)",
                [(user_id, a) for a in permitted_accounts],
            )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def verify(self, username: str, password: str) -> AuthUser | None:
        """Return the :class:`AuthUser` if credentials are correct, else ``None``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, password_hash, salt FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if not row:
                # Run the hash anyway to prevent timing-based username enumeration.
                _hash_password(password, "dummy-salt-constant")
                return None
            user_id, stored_hash, salt = row
            if not secrets.compare_digest(stored_hash, _hash_password(password, salt)):
                return None
            return self._load_user(conn, user_id, username)

    def get_user(self, username: str) -> AuthUser | None:
        """Return the :class:`AuthUser` by username without verifying password."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,),
            ).fetchone()
            if not row:
                return None
            (user_id,) = row
            return self._load_user(conn, user_id, username)

    @staticmethod
    def _load_user(conn: sqlite3.Connection, user_id: int, username: str) -> AuthUser:
        rows = conn.execute(
            "SELECT account_id FROM user_accounts WHERE user_id = ?", (user_id,),
        ).fetchall()
        permitted = frozenset(r[0] for r in rows)
        return AuthUser(username=username, permitted_accounts=permitted)
