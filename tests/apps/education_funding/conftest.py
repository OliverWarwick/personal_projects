"""Shared pytest fixtures for the education_funding app tests.

Isolates the application-store data directory per test so persistent
JSON drafts and uploaded files don't leak between tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_application_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point the application store at a fresh ``tmp_path`` for every test.

    The store consults ``EDUCATION_FINANCING_DATA_DIR`` to decide where
    JSON drafts and uploaded files live; redirecting it per-test keeps
    the suite hermetic.
    """
    monkeypatch.setenv("EDUCATION_FINANCING_DATA_DIR", str(tmp_path))
