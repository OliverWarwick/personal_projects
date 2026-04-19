"""Tests for the finance sync CLI."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from personal_project.apps.finance_sync.run import main

if TYPE_CHECKING:
    from pathlib import Path

ACCOUNT_COUNT = 2


def test_finance_sync_cli_writes_sqlite(tmp_path: Path, capsys) -> None:
    """The CLI should run end-to-end against the sample provider."""
    db_path = tmp_path / "finance.sqlite3"
    exit_code = main(["sync", "--db", str(db_path), "--json"])
    assert exit_code == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["provider"] == "sample"
    assert payload["accounts_seen"] == ACCOUNT_COUNT
    assert payload["cached_accounts"] == ACCOUNT_COUNT
    assert db_path.exists()
