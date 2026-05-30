"""Configuration management for the IBKR Flex sync app.

Loads the Flex Web Service token, Flex Query IDs, and database URL from
environment variables (preferred) or a YAML configuration file (fallback).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Load .env from the project root once on import so env-resolved settings
# (Flex token, query IDs) work without callers having to source the file.
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

_DEFAULT_DATABASE_URL = "sqlite:///ibkr_data.db"


class IBKRSyncConfig:
    """Configuration for the ibkr-sync application.

    Resolution order for every setting is environment variable first, then
    YAML file, then a hard-coded default where one exists. Secrets such as the
    Flex token are intended to come from the environment so the YAML file can
    be checked into the repository as a non-sensitive template.

    Attributes:
        _config: Raw YAML configuration dictionary; empty when no file is
            supplied or the file is missing.

    """

    def __init__(self, config_path: str | None = None) -> None:
        """Initialise configuration, optionally loading a YAML file.

        Args:
            config_path: Optional path to a YAML configuration file. The file
                may be absent — values then fall back to environment variables
                and built-in defaults.

        """
        self._config: dict[str, Any] = {}
        if config_path and Path(config_path).exists():
            with Path(config_path).open("r") as f:
                self._config = yaml.safe_load(f) or {}

    def _lookup(self, env_key: str, yaml_key: str) -> str | None:
        """Resolve a value from the environment or YAML file.

        Args:
            env_key: Environment variable name to consult first.
            yaml_key: YAML key to consult if the environment variable is unset.

        Returns:
            The resolved value as a string, or ``None`` if neither source has
            a non-empty value.

        """
        env_value = os.environ.get(env_key)
        if env_value:
            return env_value
        yaml_value = self._config.get(yaml_key)
        if yaml_value is None or yaml_value == "":
            return None
        return str(yaml_value)

    @property
    def token(self) -> str | None:
        """Return the Flex Web Service token, or ``None`` if not configured."""
        return self._lookup("IBKR_FLEX_TOKEN", "token")

    @property
    def activity_query_id(self) -> str | None:
        """Return the Flex Query ID for trades and cash transactions."""
        return self._lookup("IBKR_FLEX_ACTIVITY_QUERY", "activity_query_id")

    @property
    def portfolio_query_id(self) -> str | None:
        """Return the Flex Query ID for NAV and open positions."""
        return self._lookup("IBKR_FLEX_PORTFOLIO_QUERY", "portfolio_query_id")

    @property
    def database_url(self) -> str:
        """Return the SQLAlchemy database URL, defaulting to a local SQLite file."""
        return self._lookup("IBKR_DATABASE_URL", "database_url") or _DEFAULT_DATABASE_URL
