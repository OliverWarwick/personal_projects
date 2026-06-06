"""Bootstrap the finances dashboard auth database.

Creates ~/.config/personal_project/finances_auth.db with two users:

    owarwick   — full access to all accounts
    jsaunders  — IBKR GIA only (account U20984788)

Run once before starting the service:

    uv run python scripts/setup_finances_auth.py

The script is idempotent: re-running it resets both users' passwords to the
values below without touching any other data.
"""

from __future__ import annotations

from pathlib import Path

from src.apps.finances_dashboard.auth import AuthDB

# Passwords — change these if you rotate credentials, then restart the service.
_USERS: list[tuple[str, str, list[str]]] = [
    # (username, password, permitted_account_ids — empty = all accounts)
    ("owarwick",  "fc6a6d1d-a9bb-4c35-8e97-582eabbc1085", []),
    ("jsaunders", "9d5b2da9-62a8-43b8-a179-e01a5dd0c63b", ["U20984788"]),
]

_DB_PATH = Path("~/.config/personal_project/finances_auth.db").expanduser()


def main() -> None:
    db = AuthDB(_DB_PATH)
    for username, password, accounts in _USERS:
        db.create_user(username, password, accounts)
        scope = "all accounts" if not accounts else ", ".join(accounts)
        print(f"  {username:12s}  scope: {scope}")  # noqa: T201
    print(f"\nAuth DB written to {_DB_PATH} (mode 0o600)")  # noqa: T201
    print("Start the service with:  uv run finances-dashboard --auth-db ~/.config/personal_project/finances_auth.db")  # noqa: T201


if __name__ == "__main__":
    main()
