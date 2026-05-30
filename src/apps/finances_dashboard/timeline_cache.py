"""Per-account timeline persistence for the finances dashboard.

The dashboard's timeline (one :class:`TimelineRow` per business day) is
deterministic given the trade ledger and yfinance EOD closes. Both are
append-only in practice: past EOD closes are immutable, and once a trade
is exported it does not change.

This module caches each account's timeline in a SQLite file keyed by a
hash of the trade ledger:

* On read, return the cached rows when the stored hash matches the
  current ledger hash and the most-recent row is within
  :data:`REVALIDATE_DAYS` business days of today.
* Otherwise the caller recomputes from scratch and writes the result
  back via :meth:`TimelineCache.save`.

The trade-hash check invalidates the whole account when *any* trade
changes (including back-dated edits). The revalidation window protects
against yfinance backfilling late closes on the trailing days. Both are
conservative — never serve stale numbers, recompute when in doubt.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from xml.etree import ElementTree as ET

    from src.apps.finances_dashboard.run import TimelineRow

logger = logging.getLogger(__name__)

# Number of business days at the trailing edge to consider mutable. EOD
# closes are usually stable by T+1 but yfinance occasionally backfills
# dividend-adjusted prices a day or two later.
REVALIDATE_DAYS = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS timeline (
    account_id      TEXT NOT NULL,
    date            TEXT NOT NULL,
    cash            TEXT NOT NULL,
    deployed        TEXT NOT NULL,
    unrealized      TEXT NOT NULL,
    realized        TEXT NOT NULL,
    total_deposited TEXT NOT NULL,
    PRIMARY KEY (account_id, date)
);
CREATE TABLE IF NOT EXISTS meta (
    account_id   TEXT PRIMARY KEY,
    trades_hash  TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""


def default_db_path() -> Path:
    """Return the on-disk path for the timeline cache.

    Honours ``PERSONAL_PROJECT_TIMELINE_CACHE`` for tests; otherwise lands
    under ``~/.cache/personal_project/``.
    """
    override = os.environ.get("PERSONAL_PROJECT_TIMELINE_CACHE")
    if override:
        return Path(override).expanduser()
    return Path("~/.cache/personal_project/timeline.sqlite").expanduser()


def hash_trades(stmt: ET.Element) -> str:
    """Return a stable hash of every ``Trade`` element in ``stmt``.

    The hash covers the fields the timeline replay depends on — trade
    date, symbol, quantity, price, and realised P&L. Two stmts that
    produce the same timeline therefore hash identically; any edit to a
    historical trade invalidates the cache.
    """
    items: list[tuple[str, str, str, str, str]] = [
        (
            t.get("tradeDate") or "",
            t.get("symbol") or "",
            t.get("quantity") or "",
            t.get("tradePrice") or "",
            t.get("fifoPnlRealized") or "",
        )
        for t in stmt.iter("Trade")
    ]
    # Also hash CashTransaction rows — they drive the cash leg and so
    # affect cash/total_deposited even though they aren't Trades.
    cash_items: list[tuple[str, str, str]] = [
        (
            c.get("dateTime") or c.get("settleDate") or "",
            c.get("type") or "",
            c.get("amount") or "",
        )
        for c in stmt.iter("CashTransaction")
    ]
    items.sort()
    cash_items.sort()
    payload = "|".join("/".join(t) for t in items) + "#" + "|".join(
        "/".join(c) for c in cash_items
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TimelineCache:
    """SQLite-backed cache of per-account timeline rows."""

    def __init__(self, db_path: Path | None = None) -> None:
        """Open (or create) the SQLite cache file at ``db_path``.

        Args:
            db_path: Override for the cache location. Defaults to
                :func:`default_db_path`.

        """
        self.db_path = db_path or default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection with sensible defaults applied."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def load(
        self,
        account_id: str,
        trades_hash: str,
        today: date,
    ) -> list[TimelineRow] | None:
        """Return cached rows when fresh enough, else ``None``.

        Args:
            account_id: Account key (e.g. ``HL-ISA``, ``U20984787``).
            trades_hash: Current ledger hash from :func:`hash_trades`.
            today: Reference date used to decide whether the trailing
                edge of the cache is still fresh.

        Returns:
            A list of :class:`TimelineRow` when the cache is valid and
            its most-recent row is within :data:`REVALIDATE_DAYS`
            business days of ``today``. ``None`` otherwise so the caller
            recomputes.

        """
        # Import here to avoid a circular import with run.py at module load.
        from src.apps.finances_dashboard.run import TimelineRow  # noqa: PLC0415

        with self._connect() as conn:
            row = conn.execute(
                "SELECT trades_hash FROM meta WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if row is None or row[0] != trades_hash:
                return None
            cursor = conn.execute(
                "SELECT date, cash, deployed, unrealized, realized, total_deposited "
                "FROM timeline WHERE account_id = ? ORDER BY date",
                (account_id,),
            )
            rows = [
                TimelineRow(
                    date=date.fromisoformat(r[0]),
                    cash=Decimal(r[1]),
                    deployed=Decimal(r[2]),
                    unrealized=Decimal(r[3]),
                    realized=Decimal(r[4]),
                    total_deposited=Decimal(r[5]),
                )
                for r in cursor
            ]
        if not rows:
            return None
        # Stale if the trailing edge predates today by more than the
        # revalidation window. ``timedelta`` days, not business days —
        # the dashboard does its own business-day filtering and a couple
        # of weekend days of slack here are harmless.
        if (today - rows[-1].date) > timedelta(days=REVALIDATE_DAYS + 2):
            return None
        return rows

    def save(
        self,
        account_id: str,
        trades_hash: str,
        rows: list[TimelineRow],
    ) -> None:
        """Replace the cached rows for ``account_id`` with ``rows``.

        Writes are wholesale rather than incremental: simpler than
        merging, and the timeline is small (one row per business day).
        """
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM timeline WHERE account_id = ?", (account_id,))
            conn.executemany(
                "INSERT INTO timeline VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        account_id,
                        r.date.isoformat(),
                        str(r.cash),
                        str(r.deployed),
                        str(r.unrealized),
                        str(r.realized),
                        str(r.total_deposited),
                    )
                    for r in rows
                ],
            )
            conn.execute(
                "INSERT INTO meta(account_id, trades_hash, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(account_id) DO UPDATE SET "
                "trades_hash = excluded.trades_hash, "
                "updated_at  = excluded.updated_at",
                (account_id, trades_hash, now),
            )
        logger.debug(
            "timeline cache wrote %d rows for %s (hash=%s)",
            len(rows),
            account_id,
            trades_hash[:8],
        )

    def invalidate(self, account_id: str) -> None:
        """Drop the cached rows and hash for ``account_id``."""
        with self._connect() as conn:
            conn.execute("DELETE FROM timeline WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM meta WHERE account_id = ?", (account_id,))
