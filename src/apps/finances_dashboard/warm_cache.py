"""Warm the on-disk yfinance cache for every symbol across every account.

Walks the configured IBKR FlexStatements and synthesised HL statements,
gathers every ``OpenPosition`` symbol plus every ``Trade`` symbol, resolves
each to a Yahoo Finance ticker via :data:`IBKR_TO_YF`, and pre-fetches its
full daily close series into :class:`CachedYFinanceClient`. Symbols Yahoo
refuses are recorded in the persistent negative-symbol cache so subsequent
dashboard requests do not hit upstream for them.

Run as ``finances-cache-warm`` (registered in ``pyproject.toml``) or
``python -m src.apps.finances_dashboard.warm_cache``.
"""

from __future__ import annotations

import argparse
import logging
import time
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from src.apps.finances_dashboard.config import load_config
from src.apps.finances_dashboard.ajbell_provider import (
    load_ajbell_statements,
)
from src.apps.finances_dashboard.hl_provider import (
    HL_ACCOUNT_PREFIX,
    load_hl_statements,
)
from src.apps.finances_dashboard.run import (
    DEFAULT_CONFIG,
    DEFAULT_XML,
    IBKR_TO_YF,
    ensure_flex_xml,
)
from src.apps.finances_dashboard.timeline_cache import TimelineCache
from src.clients.marketdata.cache import CachedYFinanceClient
from src.clients.marketdata.yfinance_client import MarketDataError

logger = logging.getLogger(__name__)

_THROTTLE_SECONDS = 0.8
_RATE_LIMIT_BACKOFF_SECONDS = 30
_MAX_RATE_LIMIT_RETRIES = 3


def _collect_symbols(stmt: ET.Element, base_currency: str) -> dict[str, tuple[str, str, date]]:
    """Return ``{resolved_yf: (ibkr_sym, ccy, earliest_trade_date)}`` for ``stmt``.

    Both ``OpenPosition`` and historical ``Trade`` rows contribute symbols so
    closed positions are also backfilled. The earliest trade date determines
    how far back the warm-up fetches each series.
    """
    out: dict[str, tuple[str, str, date]] = {}
    for elem in list(stmt.iter("OpenPosition")) + list(stmt.iter("Trade")):
        sym = elem.get("symbol") or ""
        if not sym or "." in sym:
            continue
        yf_sym, ccy = IBKR_TO_YF.get(sym, (sym, base_currency))
        d_str = (elem.get("tradeDate") or "")[:8]
        earliest = (
            datetime.strptime(d_str, "%Y%m%d").replace(tzinfo=UTC).date()
            if d_str
            else date.today()  # noqa: DTZ011 — local "today" is fine here
        )
        if yf_sym in out:
            prior_sym, prior_ccy, prior_date = out[yf_sym]
            out[yf_sym] = (prior_sym, prior_ccy, min(prior_date, earliest))
        else:
            out[yf_sym] = (sym, ccy, earliest)
    return out


def _statements(xml_path: Path) -> list[tuple[str, ET.Element, str]]:
    """Return ``(account_id, FlexStatement, base_currency)`` for every account."""
    statements: list[tuple[str, ET.Element, str]] = []
    if xml_path.exists():
        tree = ET.parse(xml_path)  # noqa: S314 — trusted local Flex XML
        for stmt in tree.iter("FlexStatement"):
            account_id = stmt.get("accountId") or ""
            base = stmt.get("currency") or "USD"
            if account_id:
                statements.append((account_id, stmt, base))
    for account_id, stmt in load_hl_statements().items():
        statements.append((account_id, stmt, "GBP"))
    for account_id, stmt in load_ajbell_statements().items():
        statements.append((account_id, stmt, "GBP"))
    return statements


def _warm_one(
    yf_client: CachedYFinanceClient,
    yf_sym: str,
    ccy: str,
    base_currency: str,
    start: date,
    end: date,
) -> str:
    """Fetch one symbol with rate-limit retries; return a status string."""
    for attempt in range(_MAX_RATE_LIMIT_RETRIES):
        try:
            yf_client.get_close_series(
                symbol=yf_sym,
                start=start,
                end=end,
                instrument_currency=ccy,
                base_currency=base_currency,
            )
        except MarketDataError as exc:
            return f"skipped — {exc}"
        except Exception as exc:  # noqa: BLE001 — yfinance raises a variety
            if "RateLimit" in type(exc).__name__ and attempt < _MAX_RATE_LIMIT_RETRIES - 1:
                wait = _RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1)
                logger.warning("rate limited on %s; sleeping %ds", yf_sym, wait)
                time.sleep(wait)
                continue
            return f"failed — {type(exc).__name__}: {exc}"
        else:
            return "ok"
    return "rate-limited"


def main() -> int:  # noqa: PLR0915 — single linear script
    """Warm the price cache for every holding across every account."""
    parser = argparse.ArgumentParser(description="Pre-warm the yfinance cache")
    parser.add_argument("--xml", default=str(DEFAULT_XML))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument(
        "--bust-timeline",
        action="store_true",
        help="Drop cached timelines so new mappings take effect immediately",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(message)s")

    config = load_config(Path(args.config))  # noqa: F841 — validates config
    ensure_flex_xml(Path(args.xml))
    statements = _statements(Path(args.xml))
    logger.info("found %d statements", len(statements))

    universe: dict[str, tuple[str, str, str, date]] = {}
    for account_id, stmt, base in statements:
        for yf_sym, (ibkr_sym, ccy, earliest) in _collect_symbols(stmt, base).items():
            prior = universe.get(yf_sym)
            if prior is None or earliest < prior[3]:
                universe[yf_sym] = (account_id, ibkr_sym, ccy, earliest)
    logger.info("resolved %d unique yfinance symbols", len(universe))

    yf_client = CachedYFinanceClient()
    today = date.today()  # noqa: DTZ011
    results: list[tuple[str, str, str]] = []
    for yf_sym, (account_id, ibkr_sym, ccy, earliest) in sorted(universe.items()):
        # Pad start a touch to absorb weekends / earliest off-by-one trade rows.
        start = earliest - timedelta(days=7)
        status = _warm_one(yf_client, yf_sym, ccy, "GBP" if account_id.startswith(
            HL_ACCOUNT_PREFIX) else "USD", start, today)
        results.append((yf_sym, ibkr_sym, status))
        time.sleep(_THROTTLE_SECONDS)

    if args.bust_timeline:
        tc = TimelineCache()
        for account_id, _stmt, _base in statements:
            tc.invalidate(account_id)
        logger.info("timeline cache invalidated for %d accounts", len(statements))

    print()  # noqa: T201 — CLI report
    print(f"{'YF Symbol':18s} {'IBKR Symbol':18s} Status")  # noqa: T201
    print("-" * 100)  # noqa: T201
    for yf_sym, ibkr_sym, status in results:
        print(f"{yf_sym:18s} {ibkr_sym:18s} {status}")  # noqa: T201
    ok_count = sum(1 for _, _, s in results if s == "ok")
    print(f"\n{ok_count}/{len(results)} symbols cached.")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
