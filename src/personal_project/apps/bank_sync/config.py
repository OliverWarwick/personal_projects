"""Configuration management for bank sync.

This module handles loading GoCardless credentials and database settings
from environment variables or a YAML configuration file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


class BankSyncConfig:
    """Configuration for the bank-sync application."""

    def __init__(self, config_path: str | None = None) -> None:
        """Initialise configuration.

        Args:
            config_path: Path to the YAML configuration file.

        """
        self._config: dict[str, Any] = {}
        if config_path and Path(config_path).exists():
            with Path(config_path).open("r") as f:
                self._config = yaml.safe_load(f) or {}

    @property
    def secret_id(self) -> str | None:
        """GoCardless Secret ID."""
        return str(os.environ.get("GOCARDLESS_SECRET_ID")) or str(self._config.get("secret_id"))

    @property
    def secret_key(self) -> str | None:
        """GoCardless Secret Key."""
        return str(os.environ.get("GOCARDLESS_SECRET_KEY")) or str(self._config.get("secret_key"))

    @property
    def database_url(self) -> str:
        """SQLite database URL."""
        return str(os.environ.get("DATABASE_URL")) or str(
            self._config.get("database_url", "sqlite:///bank_data.db")
        )
