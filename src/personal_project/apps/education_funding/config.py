"""Configuration management for the education_funding application.

Loads settings from environment variables or a YAML configuration file.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

import yaml


class EducationFundingConfig:
    """Configuration for the education-funding application.

    Reads settings from an optional YAML file, falling back to environment
    variables where appropriate.
    """

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
    def database_url(self) -> str:
        """SQLite database URL for persisting research data."""
        return str(
            os.environ.get("DATABASE_URL")
            or self._config.get("database_url", "sqlite:///education_funding.db")
        )

    @property
    def session_secret(self) -> str:
        """Secret key used to sign session cookies.

        Read from the ``SESSION_SECRET`` environment variable first, then
        the ``session_secret`` key in the YAML config file. Falls back to a
        freshly generated token if neither is set — restarts will then
        invalidate existing sessions, which is acceptable only in local
        development.
        """
        return str(
            os.environ.get("SESSION_SECRET")
            or self._config.get("session_secret")
            or secrets.token_urlsafe(32)
        )
