"""Configuration for the finances dashboard app.

Loads the user → broker → accounts hierarchy that drives the sidebar navigation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import yaml

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class AccountEntry:
    """One IBKR account belonging to a configured broker.

    Attributes:
        id: IBKR ``accountId`` matching a ``FlexStatement`` in the XML.
        label: Friendly name displayed alongside the account id (e.g. "ISA").
        description: Optional longer description for the account.

    """

    id: str
    label: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class BrokerEntry:
    """One broker grouping a list of accounts under a user.

    Attributes:
        name: Broker display name (e.g. "IBKR").
        accounts: Ordered list of accounts held with this broker.

    """

    name: str
    accounts: list[AccountEntry]


@dataclass(frozen=True, slots=True)
class UserEntry:
    """One user grouping brokers and their accounts.

    Attributes:
        name: Display name shown as the top of the sidebar tree.
        brokers: Ordered list of brokers owned by the user.

    """

    name: str
    brokers: list[BrokerEntry]


@dataclass(frozen=True, slots=True)
class DashboardConfig:
    """Resolved dashboard configuration.

    Attributes:
        users: Top-level list of users, each with one or more brokers.

    """

    users: list[UserEntry]

    def find_account(
        self,
        account_id: str,
    ) -> tuple[UserEntry, BrokerEntry, AccountEntry] | None:
        """Return the user/broker/account triple matching ``account_id``, if any."""
        for u in self.users:
            for b in u.brokers:
                for a in b.accounts:
                    if a.id == account_id:
                        return u, b, a
        return None

    def all_account_ids(self) -> list[str]:
        """Return every configured account id in display order."""
        return [a.id for u in self.users for b in u.brokers for a in b.accounts]

    def first_account_id(self) -> str:
        """Return the first configured account id.

        Raises:
            RuntimeError: When no accounts are configured.

        """
        ids = self.all_account_ids()
        if not ids:
            raise RuntimeError("No accounts configured in finances_dashboard.yaml")
        return ids[0]


def _parse_account(raw: Any, broker_name: str) -> AccountEntry:
    """Parse a single account entry from raw YAML."""
    if isinstance(raw, str):
        return AccountEntry(id=raw, label=raw)
    if not isinstance(raw, dict):
        raise TypeError(f"Bad account entry under {broker_name!r}: {raw!r}")
    entry = cast("dict[str, Any]", raw)
    acc_id = entry.get("id")
    if not isinstance(acc_id, str) or not acc_id:
        raise ValueError(f"Account under {broker_name!r} missing 'id' field")
    label = entry.get("label")
    label_str = label if isinstance(label, str) and label else acc_id
    description_raw = entry.get("description")
    description = (
        description_raw if isinstance(description_raw, str) and description_raw else None
    )
    return AccountEntry(id=acc_id, label=label_str, description=description)


def load_config(path: Path) -> DashboardConfig:
    """Load and validate the dashboard YAML config.

    Args:
        path: Path to the YAML config file.

    Returns:
        Parsed :class:`DashboardConfig`.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the YAML is malformed or missing required fields.

    """
    if not path.exists():
        raise FileNotFoundError(f"Dashboard config not found: {path}")
    raw = cast("dict[str, Any]", yaml.safe_load(path.read_text()) or {})
    users_raw = cast("list[dict[str, Any]]", raw.get("users") or [])
    users: list[UserEntry] = []
    for u in users_raw:
        name = u.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Each user entry requires a non-empty 'name' field")
        brokers: list[BrokerEntry] = []
        brokers_raw = cast("list[Any]", u.get("brokers") or [])
        if brokers_raw:
            for b in brokers_raw:
                if not isinstance(b, dict):
                    raise TypeError(f"Bad broker entry under {name!r}: {b!r}")
                bentry = cast("dict[str, Any]", b)
                bname = bentry.get("name")
                if not isinstance(bname, str) or not bname:
                    raise ValueError(
                        f"Broker under {name!r} requires a non-empty 'name' field",
                    )
                accounts_raw = cast("list[Any]", bentry.get("accounts") or [])
                accounts = [_parse_account(a, bname) for a in accounts_raw]
                brokers.append(BrokerEntry(name=bname, accounts=accounts))
        else:
            # Backward compat: flat accounts under user → wrap in a single unnamed broker.
            flat_accounts_raw = cast("list[Any]", u.get("accounts") or [])
            if flat_accounts_raw:
                accounts = [_parse_account(a, name) for a in flat_accounts_raw]
                brokers.append(BrokerEntry(name="Accounts", accounts=accounts))
        users.append(UserEntry(name=name, brokers=brokers))
    return DashboardConfig(users=users)
