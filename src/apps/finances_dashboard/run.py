"""FastAPI dashboard for an IBKR account.

Renders a single HTML page with two panes: a left-hand table of open
positions in the account's base currency, and a right-hand stacked bar
chart of cash, deployed capital, unrealised P&L, and realised P&L over
the trailing 30 business days, overlaid with a grey step line showing
cumulative deposits.

The dashboard reads a saved Flex statement XML (default
``/tmp/ibkr.xml``) so it works while the IBKR Flex Web Service is
throttled. Daily marks for unrealised P&L are pulled from yfinance and
converted to the account's base currency via the
:class:`YFinanceClient` ``base_currency`` option.
"""

from __future__ import annotations

import argparse
import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.apps.finances_dashboard.config import (
    DashboardConfig,
    UserEntry,
    load_config,
)
from src.clients.marketdata.cache import CachedYFinanceClient
from src.clients.marketdata.yfinance_client import (
    MarketDataError,
    YFinanceClient,  # noqa: F401
)

# Symbols we've already warned about this process — keeps the negative-cache
# misses from spamming the log on every dashboard request.
_WARNED_MISSING: set[str] = set()

logger = logging.getLogger("finances_dashboard")

DEFAULT_XML = Path("/tmp/ibkr.xml")  # noqa: S108 (local dev cache of Flex XML)
# Below this size the Flex XML is considered empty (a 245-byte stub with
# no Trade / CashTransaction children) and the dashboard re-downloads it.
_FLEX_XML_MIN_BYTES = 1024


def ensure_flex_xml(xml_path: Path = DEFAULT_XML) -> None:
    """Re-download the Flex XML if missing or stub-sized.

    Uses :class:`IBKRSyncConfig` to read the token + activity query ID
    (from env or the project ``.env`` file) and writes a fresh XML via
    :class:`IBKRFlexClient`. Silently no-ops when credentials are absent
    so the dashboard still starts in dev environments without IBKR setup.
    """
    if xml_path.exists() and xml_path.stat().st_size >= _FLEX_XML_MIN_BYTES:
        return
    from src.apps.ibkr_sync.config import IBKRSyncConfig  # noqa: PLC0415
    from src.clients.ibkr import IBKRFlexClient  # noqa: PLC0415

    cfg = IBKRSyncConfig()
    token = cfg.token
    query_id = cfg.activity_query_id
    if not token or not query_id:
        logger.warning("Flex XML at %s is empty and no IBKR creds configured; skipping refresh", xml_path)
        return
    try:
        xml = IBKRFlexClient(token).download_xml(query_id)
    except Exception:
        logger.exception("Flex XML refresh failed; continuing with stale file")
        return
    xml_path.write_bytes(xml)
    logger.info("refreshed Flex XML at %s (%d bytes)", xml_path, len(xml))
DEFAULT_ACCOUNT = "U20984788"
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "finances_dashboard.yaml"
TRAILING_DAYS = 30

IBKR_TO_YF: dict[str, tuple[str, str]] = {
    "ATS": ("ATS.VI", "EUR"),
    "E61": ("E61.F", "EUR"),
    "SOI": ("SOI.PA", "EUR"),
    "HY9H": ("HY9H.F", "EUR"),  # Frankfurt-listed; no ADR
    "TER": ("TER", "USD"),
    "IBKR": ("IBKR", "USD"),
    "AMKR": ("AMKR", "USD"),
    "LITE": ("LITE", "USD"),
    "MKSI": ("MKSI", "USD"),
    "SMTC": ("SMTC", "USD"),
    "VUAG": ("VUAG.L", "GBP"),
    "IGUS": ("IGUS.L", "GBp"),
    "BRK B": ("BRK-B", "USD"),
    "GOOGL": ("GOOGL", "USD"),
    # HL UK-listed ETFs / investment trusts. HL exports a bare code; Yahoo
    # needs the ``.L`` suffix. Pence-quoted instruments use ``GBp`` so the
    # FX layer divides by 100 when converting to ``GBP`` base.
    "CTY": ("CTY.L", "GBp"),
    "IUKD": ("IUKD.L", "GBp"),
    "FEQP": ("FEQP.L", "GBP"),
    "VHYG": ("VHYG.L", "GBP"),
    "ISPE": ("ISPE.L", "GBP"),
    "LGEN": ("LGEN.L", "GBp"),
    # HL holds ASML as the Amsterdam-listed line (ISIN NL0010273215). The
    # bare ``ASML`` ticker on Yahoo is the US ADR quoted in USD; without an
    # explicit mapping the dashboard treats the USD price as GBP and
    # overstates the position by ~30%. Route to ``ASML.AS`` in EUR so the
    # FX layer converts correctly. (``ASML.L`` does not exist on yfinance.)
    "ASML": ("ASML.AS", "EUR"),
    # HL/AJ Bell description-style symbols — mapped from the truncated
    # instrument description string emitted by the synth providers.
    "AMAZON-COM-INC-COM-STK-U": ("AMZN", "USD"),
    "ASML-HOLDING-NV-EUR0-09": ("ASML.AS", "EUR"),
    "NEBIUS-GROUP-NV-USD0-01-": ("NBIS", "USD"),
    "VANGUARD-S-P-500-ETF-USD": ("VUSA.L", "GBp"),
}


@dataclass(frozen=True, slots=True)
class PositionRow:
    """Open-position row rendered on the LHS table.

    Attributes:
        ticker: IBKR symbol.
        exchange: ``listingExchange`` from the Flex row.
        currency: Instrument currency.
        quantity: Position size.
        mark_price: IBKR-reported mark in instrument currency.
        value_local: ``quantity * mark_price``.
        fx_rate: Instrument-to-base FX rate.
        value_base: Market value in base currency.
        unrealized_base: Unrealised P&L in base currency.

    """

    ticker: str
    exchange: str
    currency: str
    quantity: Decimal
    avg_price: Decimal
    mark_price: Decimal
    value_local: Decimal
    fx_rate: Decimal
    value_base: Decimal
    unrealized_base: Decimal
    last_purchase: date | None
    first_purchase: date | None
    isin: str
    description: str
    conid: str
    sec_type: str
    # Source account id for leaf rows; empty for aggregated rows. Populated
    # only on the aggregate-view page so the drill-down JSX can label the
    # contributing accounts.
    account_id: str = ""
    # Per-account contributors stitched in by :func:`_aggregate_position_rows`
    # when more than one account holds the same ticker. Empty otherwise so
    # leaf views render unchanged.
    sources: tuple[PositionRow, ...] = ()


@dataclass(frozen=True, slots=True)
class SummaryStats:
    """Headline figures for the top-of-page widgets.

    Attributes:
        portfolio_value: Cash plus current market value, in base currency.
        cash: Free cash, base currency.
        deployed: Sum of cost basis for open positions, base currency.
        unrealized: Mark-to-market gain/loss on open positions.
        realized: Cumulative realised P&L over the timeline window.
        total_invested: Cumulative net deposits.
        days_invested: Days from first deposit to the latest snapshot.
        cagr: Money-weighted annual return (XIRR) over the deposit
            cashflows and current portfolio value. Each deposit is weighted
            by the time it has been invested, so the figure stays meaningful
            with multiple staggered deposits. ``None`` when fewer than two
            distinct days are present, no capital has been deposited, or the
            solver cannot bracket a root.

    """

    portfolio_value: Decimal
    cash: Decimal
    deployed: Decimal
    unrealized: Decimal
    realized: Decimal
    total_invested: Decimal
    days_invested: int
    cagr: Decimal | None
    yesterday_pnl: Decimal | None
    pnl_30d: Decimal | None
    pnl_ytd: Decimal | None
    sharpe: Decimal | None
    var_99: Decimal | None


@dataclass(frozen=True, slots=True)
class ClosedRow:
    """Closed-position row rendered beneath the open-positions table.

    Attributes:
        close_date: Date of the closing trade.
        ticker: IBKR symbol.
        exchange: ``listingExchange`` from the Flex row.
        currency: Instrument currency.
        quantity: Signed quantity that closed (negative for sells).
        open_price: Average open price of the closed lot, if reported.
        close_price: Execution price of the close.
        realized_base: Realised P&L converted to the account base currency.

    """

    close_date: date
    ticker: str
    exchange: str
    currency: str
    quantity: Decimal
    open_price: Decimal
    close_price: Decimal
    realized_base: Decimal
    open_date: date | None


@dataclass(frozen=True, slots=True)
class TimelineRow:
    """Single daily snapshot rendered on the RHS chart.

    Attributes:
        date: Calendar date of the snapshot.
        cash: Free cash in base currency.
        deployed: Cumulative cost basis of open positions in base currency.
        unrealized: Mark-to-market gain/loss vs cost basis in base currency.
        realized: Cumulative realised P&L in base currency.
        total_deposited: Cumulative deposits in base currency.

    """

    date: date
    cash: Decimal
    deployed: Decimal
    unrealized: Decimal
    realized: Decimal
    total_deposited: Decimal


def _to_dec(value: str | None) -> Decimal:
    """Coerce a Flex attribute string to ``Decimal``, treating blank as zero."""
    return Decimal(value) if value else Decimal(0)


def _parse_account(xml_path: Path, account_id: str) -> tuple[ET.Element, str]:
    """Return the ``FlexStatement`` element for ``account_id`` and base currency."""
    tree = ET.parse(xml_path)  # noqa: S314 (trusted local Flex XML)
    for stmt in tree.iter("FlexStatement"):
        if stmt.get("accountId") == account_id:
            base = stmt.get("currency") or "GBP"
            return stmt, base
    raise RuntimeError(f"Account {account_id} not found in {xml_path}")


def _parse_aggregate(
    xml_path: Path,
    account_ids: list[str],
) -> tuple[ET.Element, str]:
    """Return a synthetic element wrapping all ``FlexStatement``s for ``account_ids``.

    The wrapper preserves the iter-based traversal used by the other parsers, so
    trades, open positions, cash transactions, and equity-summary rows from
    every matched account are visited together.
    """
    tree = ET.parse(xml_path)  # noqa: S314 (trusted local Flex XML)
    wanted = set(account_ids)
    matches: list[ET.Element] = []
    base = "GBP"
    for stmt in tree.iter("FlexStatement"):
        if stmt.get("accountId") in wanted:
            matches.append(stmt)
            base = stmt.get("currency") or base
    if not matches:
        raise RuntimeError(f"No FlexStatements found for accounts {account_ids}")
    wrapper = ET.Element("AggregateStatement")
    for s in matches:
        wrapper.append(s)
    return wrapper, base


def _user_slug(name: str) -> str:
    """Return a URL-safe slug for ``name`` (lowercase, non-alnum → ``-``)."""
    out: list[str] = []
    last_dash = False
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
            last_dash = False
        elif not last_dash:
            out.append("-")
            last_dash = True
    return "".join(out).strip("-") or "user"


def _aggregate_position_rows(rows: list[PositionRow]) -> list[PositionRow]:
    """Sum :class:`PositionRow` entries that share a ticker across accounts."""
    by_ticker: dict[str, list[PositionRow]] = {}
    for r in rows:
        by_ticker.setdefault(r.ticker, []).append(r)
    out: list[PositionRow] = []
    for ticker, lst in by_ticker.items():
        if len(lst) == 1:
            out.append(lst[0])
            continue
        qty = sum((r.quantity for r in lst), Decimal(0))
        cost = sum((r.quantity * r.avg_price for r in lst), Decimal(0))
        avg = (cost / qty) if qty else Decimal(0)
        value_local = sum((r.value_local for r in lst), Decimal(0))
        value_base = sum((r.value_base for r in lst), Decimal(0))
        unrealized = sum((r.unrealized_base for r in lst), Decimal(0))
        last_purchases = [r.last_purchase for r in lst if r.last_purchase]
        last_purchase = max(last_purchases) if last_purchases else None
        first_purchases = [r.first_purchase for r in lst if r.first_purchase]
        first_purchase = min(first_purchases) if first_purchases else None
        out.append(
            PositionRow(
                ticker=ticker,
                exchange=lst[0].exchange,
                currency=lst[0].currency,
                quantity=qty,
                avg_price=avg,
                mark_price=lst[0].mark_price,
                value_local=value_local,
                fx_rate=lst[0].fx_rate,
                value_base=value_base,
                unrealized_base=unrealized,
                last_purchase=last_purchase,
                first_purchase=first_purchase,
                isin=lst[0].isin,
                description=lst[0].description,
                conid=lst[0].conid,
                sec_type=lst[0].sec_type,
                account_id="",
                sources=tuple(sorted(lst, key=lambda r: r.account_id)),
            ),
        )
    out.sort(key=lambda r: r.ticker)
    return out


def _open_positions(stmt: ET.Element, account_id: str = "") -> list[PositionRow]:
    """Build :class:`PositionRow` objects from ``OpenPosition`` summaries.

    Args:
        stmt: ``FlexStatement`` element. May be a synthetic wrapper.
        account_id: When set, every row gets tagged with this id. The
            aggregate page handler passes the leaf account id so the
            drill-down knows which account each contributor came from.

    """
    last_buy: dict[str, date] = {}
    first_buy: dict[str, date] = {}
    for t in stmt.iter("Trade"):
        if t.get("levelOfDetail") != "EXECUTION":
            continue
        sym = t.get("symbol") or ""
        if "." in sym:
            continue
        oc = (t.get("openCloseIndicator") or "").upper()
        if oc not in {"O", "OPEN", "OPENCLOSE", ""}:
            continue
        d_str = (t.get("tradeDate") or "")[:8]
        if not d_str:
            continue
        d = datetime.strptime(d_str, "%Y%m%d").replace(tzinfo=UTC).date()
        prev = last_buy.get(sym)
        if prev is None or d > prev:
            last_buy[sym] = d
        first = first_buy.get(sym)
        if first is None or d < first:
            first_buy[sym] = d
    rows: list[PositionRow] = []
    for p in stmt.iter("OpenPosition"):
        if p.get("levelOfDetail") != "SUMMARY":
            continue
        qty = _to_dec(p.get("position"))
        mark = _to_dec(p.get("markPrice"))
        val = _to_dec(p.get("positionValue"))
        if not val and qty and mark:
            val = qty * mark
        fx = _to_dec(p.get("fxRateToBase")) or Decimal(1)
        sym = p.get("symbol") or ""
        # Fall back to IBKR-supplied lot open date / holding-period date when
        # the Trade ledger doesn't contain a BUY for this symbol — common for
        # long-held positions whose only purchase predates the Flex window.
        first_p = first_buy.get(sym)
        if first_p is None:
            for attr in ("openDateTime", "holdingPeriodDateForGains"):
                raw = (p.get(attr) or "")[:8]
                if raw and raw.isdigit() and len(raw) == 8:
                    try:
                        first_p = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=UTC).date()
                    except ValueError:
                        first_p = None
                    if first_p is not None:
                        break
        rows.append(
            PositionRow(
                ticker=sym,
                exchange=p.get("listingExchange") or "",
                currency=p.get("currency") or "",
                quantity=qty,
                avg_price=_to_dec(p.get("costBasisPrice")),
                mark_price=mark,
                value_local=val,
                fx_rate=fx,
                value_base=val * fx,
                unrealized_base=_to_dec(p.get("fifoPnlUnrealized")) * fx,
                last_purchase=last_buy.get(sym) or first_p,
                first_purchase=first_p,
                isin=p.get("isin") or "",
                description=p.get("description") or "",
                conid=p.get("conid") or "",
                sec_type=p.get("assetCategory") or "",
                account_id=account_id,
            ),
        )
    return sorted(rows, key=lambda r: r.ticker.upper())


def _closed_positions(stmt: ET.Element) -> list[ClosedRow]:
    """Build :class:`ClosedRow` objects from closing trades in the statement.

    Performs a one-pass FIFO walk so each closing trade can be annotated
    with the open date of the earliest matching open lot — needed to compute
    a hold-period CAGR in the UI.
    """
    sorted_trades: list[tuple[date, ET.Element]] = []
    for t in stmt.iter("Trade"):
        if t.get("levelOfDetail") != "EXECUTION":
            continue
        sym = t.get("symbol") or ""
        if "." in sym:
            continue
        d_str = (t.get("tradeDate") or "")[:8]
        if not d_str:
            continue
        d = datetime.strptime(d_str, "%Y%m%d").replace(tzinfo=UTC).date()
        sorted_trades.append((d, t))
    sorted_trades.sort(key=lambda x: x[0])

    # Per-ticker FIFO lot queue of (open_date, remaining_qty).
    lots: dict[str, list[list[Decimal | date]]] = {}
    rows: list[ClosedRow] = []
    for d, t in sorted_trades:
        sym = t.get("symbol") or ""
        qty = _to_dec(t.get("quantity"))
        oc = (t.get("openCloseIndicator") or "").upper()
        pnl_dec = _to_dec(t.get("fifoPnlRealized"))
        is_close = oc in {"C", "CLOSE", "OPENCLOSE"} or pnl_dec != 0 or qty < 0
        if not is_close:
            lots.setdefault(sym, []).append([d, qty])  # type: ignore[list-item]
            continue
        remaining = -qty if qty < 0 else qty
        open_date: date | None = None
        queue = lots.get(sym, [])
        while remaining > 0 and queue:
            lot_date, lot_qty = queue[0]
            if open_date is None:
                open_date = lot_date  # type: ignore[assignment]
            if lot_qty <= remaining:  # type: ignore[operator]
                remaining -= lot_qty  # type: ignore[assignment, operator]
                queue.pop(0)
            else:
                queue[0][1] = lot_qty - remaining  # type: ignore[operator]
                remaining = Decimal(0)
        fx = _to_dec(t.get("fxRateToBase")) or Decimal(1)
        rows.append(
            ClosedRow(
                close_date=d,
                ticker=sym,
                exchange=t.get("listingExchange") or "",
                currency=t.get("currency") or "",
                quantity=qty,
                open_price=_to_dec(t.get("origTradePrice")),
                close_price=_to_dec(t.get("tradePrice")),
                realized_base=pnl_dec * fx,
                open_date=open_date,
            ),
        )
    return sorted(rows, key=lambda r: (r.close_date, r.ticker))


def _events(
    stmt: ET.Element,
    base_currency: str = "USD",
) -> list[tuple[date, str, Decimal, str, Decimal]]:
    """Return chronological cash/trade events for the account.

    ``base_currency`` is used to fold IBKR forex ``Trade`` rows (e.g.
    ``GBP.USD``) into the cash stream. Those rows are skipped by the regular
    Trade loop because their symbol contains ``.``, but they represent real
    GBP outflow whenever the account converts before buying a USD-denominated
    instrument — without them, GBP cash appears to leak.
    """
    out: list[tuple[date, str, Decimal, str, Decimal]] = []
    for c in stmt.iter("CashTransaction"):
        if c.get("levelOfDetail") != "DETAIL":
            continue
        d_str = (c.get("dateTime") or c.get("settleDate") or "")[:8]
        if not d_str:
            continue
        d = datetime.strptime(d_str, "%Y%m%d").replace(tzinfo=UTC).date()
        amt = _to_dec(c.get("amount")) * (_to_dec(c.get("fxRateToBase")) or Decimal(1))
        out.append((d, "cash", amt, c.get("type") or "", Decimal(0)))
    for t in stmt.iter("Trade"):
        if t.get("levelOfDetail") != "EXECUTION":
            continue
        sym = t.get("symbol") or ""
        d_str = (t.get("tradeDate") or "")[:8]
        if not d_str:
            continue
        d = datetime.strptime(d_str, "%Y%m%d").replace(tzinfo=UTC).date()
        # Skip forex rows: they're zero-sum in base currency (one ccy pot
        # shrinks, another grows). The base-currency impact of a foreign
        # stock buy is captured by ``-proc * fxRateToBase`` on the stock
        # trade itself, which represents the value of that USD/EUR cash
        # outflow translated back to base.
        if "." in sym:
            continue
        proc = _to_dec(t.get("proceeds"))
        fx = _to_dec(t.get("fxRateToBase")) or Decimal(1)
        cost_base = -proc * fx
        pnl_base = _to_dec(t.get("fifoPnlRealized")) * fx
        out.append((d, "trade", cost_base, sym, pnl_base))
    out.sort(key=lambda e: (e[0], 0 if e[1] == "cash" else 1))
    return out


def _build_timeline(
    stmt: ET.Element,
    base_currency: str,
    trailing_days: int,
    cache_key: str | None = None,
) -> list[TimelineRow]:
    """Build the daily snapshot series for the trailing window.

    When ``cache_key`` is supplied, the result is read from / written to
    :class:`TimelineCache` keyed on a hash of the statement's trades and
    cash transactions. Aggregate views pass ``None`` to skip caching.
    """
    from src.apps.finances_dashboard.timeline_cache import (
        TimelineCache,
        hash_trades,
    )

    events = _events(stmt, base_currency)
    if not events:
        return []
    cache: TimelineCache | None = None
    trades_hash: str | None = None
    today_ref = datetime.now(UTC).date() - timedelta(days=1)
    if cache_key:
        cache = TimelineCache()
        trades_hash = hash_trades(stmt)
        cached = cache.load(cache_key, trades_hash, today_ref)
        if cached is not None:
            return cached
    today = max(*(e[0] for e in events), datetime.now(UTC).date() - timedelta(days=1))
    # The timeline must cover every cash/trade event so deposits, cost basis,
    # and XIRR cashflows are all consistent. Anchor ``start`` at the earlier
    # of (today − trailing_days) and the first event date; the chart layer
    # is free to slice the trailing window from the result.
    earliest_event = min(e[0] for e in events)
    window_start = (pd.bdate_range(end=today, periods=trailing_days)[0]).date()
    start = min(window_start, earliest_event)
    bdays = pd.bdate_range(start=start, end=today)

    yf_client = CachedYFinanceClient()
    series_cache: dict[str, pd.Series] = {}

    def _series(yf_sym: str, ccy: str) -> pd.Series:
        """Return cached close price series for ``yf_sym`` in ``base_currency``."""
        if yf_sym not in series_cache:
            s = yf_client.get_close_series(
                symbol=yf_sym,
                start=str(start),
                end=str(today + timedelta(days=2)),
                instrument_currency=ccy,
                base_currency=base_currency,
            )
            idx: Any = s.index
            if getattr(idx, "tz", None) is not None:
                s.index = idx.tz_localize(None)
            series_cache[yf_sym] = s
        return series_cache[yf_sym]

    cash = Decimal(0)
    realized = Decimal(0)
    deposited = Decimal(0)
    snapshots: list[TimelineRow] = []
    for ts in bdays:
        d = ts.date()
        for ev_d, kind, amt, _label, pnl in events:
            if ev_d != d:
                continue
            if kind == "cash":
                cash += amt
                # "Total Invested" = real money in/out only. Skip forex legs
                # (internal GBP↔USD conversions) and broker dividends/fees/
                # interest — they're not deposits.
                if _label == "Deposits/Withdrawals":
                    deposited += amt
            else:
                cash -= amt
                realized += pnl
        snapshots.append(_snapshot(d, stmt, cash, realized, deposited, _series, base_currency, is_final=(d == today)))
    first_active = events[0][0]
    result = [s for s in snapshots if s.date >= first_active]
    if cache is not None and cache_key and trades_hash is not None:
        cache.save(cache_key, trades_hash, result)
    return result


def _snapshot(
    d: date,
    stmt: ET.Element,
    cash: Decimal,
    realized: Decimal,
    deposited: Decimal,
    series_fn: Any,
    base_currency: str,
    *,
    is_final: bool = False,
) -> TimelineRow:
    """Compute deployed cost-basis and MTM unrealised P&L for date ``d``.

    Replays the statement's trades up to and including ``d`` to derive
    open quantities and cost bases per ticker (FIFO lot-matching), then
    marks each holding using the closest ``series_fn`` close on or before
    ``d``.
    """
    # FIFO replay: each BUY pushes a (qty, unit_cost_base) lot, each SELL
    # consumes lots from the front. This matches how UK brokers report
    # cost basis (and how HMRC computes CGT), so the replay's residual
    # cost basis agrees with the broker's authoritative figure — no
    # final-day anchor needed.
    # Group by IBKR ``conid`` (stable contract id) so trades booked under
    # different symbol codes for the same security fold into one position.
    # Synthetic statements (HL/AJB) have no conid, so fall back to the symbol.
    rows: list[tuple[date, str, str, Decimal, Decimal, Decimal]] = []
    for t in stmt.iter("Trade"):
        if t.get("levelOfDetail") != "EXECUTION":
            continue
        sym = t.get("symbol") or ""
        if "." in sym:
            continue
        d_str = (t.get("tradeDate") or "")[:8]
        if not d_str:
            continue
        td = datetime.strptime(d_str, "%Y%m%d").replace(tzinfo=UTC).date()
        if td > d:
            continue
        q = _to_dec(t.get("quantity"))
        proc = _to_dec(t.get("proceeds"))
        fx = _to_dec(t.get("fxRateToBase")) or Decimal(1)
        key = t.get("conid") or sym
        rows.append((td, key, sym, q, proc, fx))
    rows.sort(key=lambda r: r[0])
    lots: dict[str, list[tuple[Decimal, Decimal]]] = {}  # key → [(qty, unit_cost_base)]
    key_to_sym: dict[str, str] = {}
    for _td, key, sym, q, proc, fx in rows:
        key_to_sym[key] = sym  # latest symbol wins (rows sorted by date)
        if q > 0:
            unit_cost = (-proc * fx) / q
            lots.setdefault(key, []).append((q, unit_cost))
        elif q < 0:
            remaining = -q
            klots = lots.get(key, [])
            while remaining > 0 and klots:
                lot_q, lot_c = klots[0]
                take = min(lot_q, remaining)
                remaining -= take
                lot_q -= take
                if lot_q == 0:
                    klots.pop(0)
                else:
                    klots[0] = (lot_q, lot_c)
    qty: dict[str, Decimal] = {k: sum((lq for lq, _ in v), Decimal(0)) for k, v in lots.items()}
    cost: dict[str, Decimal] = {k: sum((lq * lc for lq, lc in v), Decimal(0)) for k, v in lots.items()}

    # Per-key snapshot mark (markPrice × fxRateToBase) from OpenPosition.
    # Used as a fallback when yfinance has no series for the symbol
    # (typical for HL OEICs and gilts) so the headline portfolio value
    # reflects the broker's bid quote instead of stale cost basis.
    op_mark_base: dict[str, Decimal] = {}
    for op in stmt.iter("OpenPosition"):
        if op.get("levelOfDetail") not in {"SUMMARY", None}:
            continue
        op_sym = op.get("symbol") or ""
        if not op_sym or "." in op_sym:
            continue
        op_key = op.get("conid") or op_sym
        mark = _to_dec(op.get("markPrice"))
        op_fx = _to_dec(op.get("fxRateToBase")) or Decimal(1)
        if mark > 0:
            op_mark_base[op_key] = mark * op_fx

    deployed = sum(cost.values(), Decimal(0))
    mtm = Decimal(0)
    for key, q in qty.items():
        if q == 0:
            continue
        sym = key_to_sym.get(key, key)
        # On the final-day snapshot, prefer the broker's authoritative
        # markPrice over yfinance close (which can drift from HL/AJB bids).
        if is_final and key in op_mark_base:
            mtm += op_mark_base[key] * q
            continue
        yf_sym, ccy = IBKR_TO_YF.get(sym, (sym, base_currency))
        try:
            s = series_fn(yf_sym, ccy)
        except MarketDataError:
            if yf_sym not in _WARNED_MISSING:
                logger.warning("no price data for %s; using OpenPosition.markPrice", yf_sym)
                _WARNED_MISSING.add(yf_sym)
            fallback_mark = op_mark_base.get(key)
            mtm += fallback_mark * q if fallback_mark is not None else cost.get(key, Decimal(0))
            continue
        except Exception:
            logger.exception("yfinance fetch failed for %s", yf_sym)
            fallback_mark = op_mark_base.get(key)
            mtm += fallback_mark * q if fallback_mark is not None else cost.get(key, Decimal(0))
            continue
        avail = s[s.index <= pd.Timestamp(d)]
        if avail.empty:
            fallback_mark = op_mark_base.get(key)
            mtm += fallback_mark * q if fallback_mark is not None else cost.get(key, Decimal(0))
            continue
        px = Decimal(str(float(avail.iloc[-1])))
        mtm += px * q
    unrealized = mtm - deployed
    return TimelineRow(
        date=d,
        cash=cash,
        deployed=deployed,
        unrealized=unrealized,
        realized=realized,
        total_deposited=deposited,
    )


def _xirr(cashflows: list[tuple[date, float]]) -> float | None:
    """Compute the money-weighted annual return (XIRR) for ``cashflows``.

    Solves for the rate ``r`` satisfying ``sum(cf_i / (1+r)**(t_i/365)) == 0``,
    where ``t_i`` is days from the earliest cashflow. This weights each
    deposit by how long it has been invested, so it remains meaningful when
    capital is added in stages rather than as a single lump sum.

    Args:
        cashflows: ``(date, amount)`` pairs. Deposits/buys are negative,
            withdrawals and the terminal portfolio value are positive.

    Returns:
        The annualised rate as a float (e.g. ``0.12`` for 12%), or ``None``
        if the inputs are degenerate (fewer than two cashflows, all same
        sign, or the solver fails to bracket a root).

    """
    min_cashflows = 2
    if len(cashflows) < min_cashflows:
        return None
    if not (any(cf > 0 for _, cf in cashflows) and any(cf < 0 for _, cf in cashflows)):
        return None
    t0 = min(d for d, _ in cashflows)
    days = [(d - t0).days for d, _ in cashflows]
    amts = [cf for _, cf in cashflows]

    def npv(r: float) -> float:
        """Return the net present value of ``cashflows`` at rate ``r``."""
        base = 1.0 + r
        return sum(a / base ** (t / 365.0) for a, t in zip(amts, days, strict=True))

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    # Expand the upper bound geometrically to bracket short-window outliers
    # (a small account up a few % over a couple of weeks can annualise into
    # the thousands of %, well past the default ``hi``).
    expansions = 40
    while f_lo * f_hi > 0 and expansions > 0:
        hi *= 10.0
        f_hi = npv(hi)
        expansions -= 1
    if f_lo * f_hi > 0:
        return None
    tol = 1e-9
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = npv(mid)
        if abs(f_mid) < tol or (hi - lo) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return 0.5 * (lo + hi)


def _risk_free_daily_returns(
    start: date,
    end: date,
    proxy_symbol: str = "CSH2.L",
) -> dict[date, float]:
    """Fetch a money-market ETF's close series and return daily returns.

    The returned mapping covers each available trading day ``d`` with
    ``close[d] / close[prev] - 1``, suitable for use as a per-day GBP
    risk-free rate. Returns an empty dict on failure so the caller can
    fall back to ``rf = 0``.

    Args:
        start: Inclusive lower bound for the price series.
        end: Inclusive upper bound for the price series.
        proxy_symbol: yfinance ticker. ``CSH2.L`` (Lyxor Smart Overnight
            Return) tracks SONIA-style overnight GBP rates and is the
            cleanest yfinance proxy for the BoE Bank Rate.

    Returns:
        ``{date: daily_return}`` or ``{}`` on fetch failure.

    """
    try:
        s = CachedYFinanceClient().get_close_series(
            symbol=proxy_symbol,
            start=str(start - timedelta(days=7)),
            end=str(end + timedelta(days=2)),
        )
    except Exception:
        logger.exception("risk-free proxy fetch failed for %s", proxy_symbol)
        return {}
    idx: Any = s.index
    if getattr(idx, "tz", None) is not None:
        s.index = idx.tz_localize(None)
    out: dict[date, float] = {}
    prev_px: float | None = None
    for ts, px in s.items():
        d = pd.Timestamp(ts).date()
        px_f = float(px)
        if prev_px is not None and prev_px > 0:
            out[d] = px_f / prev_px - 1.0
        prev_px = px_f
    return out


def _summarise(
    timeline: list[TimelineRow],
    deposit_events: list[tuple[date, Decimal]] | None = None,
    rf_returns: dict[date, float] | None = None,
) -> SummaryStats:
    """Derive headline figures from the most recent timeline snapshot.

    Args:
        timeline: Daily snapshot series in chronological order.
        deposit_events: Full historical deposit cashflows as
            ``(date, positive_amount)`` pairs. Used to compute XIRR over
            true deposit dates, since ``timeline`` only spans the trailing
            window and may miss earlier deposits. When ``None`` or empty,
            cashflows are derived by diffing ``total_deposited`` across
            ``timeline`` (only correct if all deposits fall inside it).

    Returns:
        A :class:`SummaryStats` populated from the last snapshot. When
        ``timeline`` is empty all numeric fields default to zero and
        ``cagr`` is ``None``.

    """
    if not timeline:
        return SummaryStats(
            portfolio_value=Decimal(0),
            cash=Decimal(0),
            deployed=Decimal(0),
            unrealized=Decimal(0),
            realized=Decimal(0),
            total_invested=Decimal(0),
            days_invested=0,
            cagr=None,
            yesterday_pnl=None,
            pnl_30d=None,
            pnl_ytd=None,
            sharpe=None,
            var_99=None,
        )
    last = timeline[-1]
    # Closed-loop accounting: realised PnL is already reflected in ``cash``
    # (sells credit the full proceeds), so don't add it again.
    portfolio_value = last.cash + last.deployed + last.unrealized
    first_date = (
        min(d for d, _ in deposit_events) if deposit_events else timeline[0].date
    )
    days = (last.date - first_date).days
    cashflows: list[tuple[date, float]] = []
    if deposit_events:
        cashflows.extend((d, -float(amt)) for d, amt in deposit_events if amt != 0)
    else:
        prev_dep = Decimal(0)
        for row in timeline:
            delta = row.total_deposited - prev_dep
            if delta != 0:
                cashflows.append((row.date, -float(delta)))
                prev_dep = row.total_deposited
    cashflows.append((last.date, float(portfolio_value)))
    total_deposited_all = (
        sum((amt for _, amt in deposit_events), Decimal(0))
        if deposit_events
        else last.total_deposited
    )
    rate = _xirr(cashflows) if days >= 1 else None
    cagr = Decimal(str(rate)) if rate is not None else None
    total_pnl = last.unrealized + last.realized
    yesterday_pnl: Decimal | None = None
    if len(timeline) >= 2:  # noqa: PLR2004
        prev = timeline[-2]
        yesterday_pnl = total_pnl - (prev.unrealized + prev.realized)

    def _pnl_delta_since(cutoff: date) -> Decimal:
        """Return total_pnl minus pnl on the latest row ≤ ``cutoff``.

        Falls back to ``total_pnl`` when no row precedes the cutoff
        (i.e. activity began after the cutoff).
        """
        prior = [r for r in timeline if r.date <= cutoff]
        if not prior:
            return total_pnl
        ref = prior[-1]
        return total_pnl - (ref.unrealized + ref.realized)

    pnl_30d = _pnl_delta_since(last.date - timedelta(days=30))
    pnl_ytd = _pnl_delta_since(date(last.date.year - 1, 12, 31))

    daily_returns: list[float] = []
    excess_returns: list[float] = []
    for i in range(1, len(timeline)):
        prev_row, cur_row = timeline[i - 1], timeline[i]
        sod = prev_row.cash + prev_row.deployed + prev_row.unrealized
        if sod > 0:
            delta_pnl = (cur_row.unrealized + cur_row.realized) - (
                prev_row.unrealized + prev_row.realized
            )
            r = float(delta_pnl / sod)
            daily_returns.append(r)
            rf = (rf_returns or {}).get(cur_row.date, 0.0)
            excess_returns.append(r - rf)
    sharpe: Decimal | None = None
    var_99: Decimal | None = None
    min_returns = 5
    if len(daily_returns) >= min_returns:
        mean_e = sum(excess_returns) / len(excess_returns)
        var_e = sum((r - mean_e) ** 2 for r in excess_returns) / (len(excess_returns) - 1)
        sd = var_e**0.5
        if sd > 0:
            sharpe = Decimal(str(mean_e / sd * (252**0.5)))
        sorted_r = sorted(daily_returns)
        pos = 0.01 * (len(sorted_r) - 1)
        lo_i = int(pos)
        hi_i = min(lo_i + 1, len(sorted_r) - 1)
        pct = sorted_r[lo_i] + (sorted_r[hi_i] - sorted_r[lo_i]) * (pos - lo_i)
        var_99 = Decimal(str(abs(pct))) * portfolio_value if pct < 0 else Decimal(0)
    return SummaryStats(
        portfolio_value=portfolio_value,
        cash=last.cash,
        deployed=last.deployed,
        unrealized=last.unrealized,
        realized=last.realized,
        total_invested=total_deposited_all,
        days_invested=days,
        cagr=cagr,
        yesterday_pnl=yesterday_pnl,
        pnl_30d=pnl_30d,
        pnl_ytd=pnl_ytd,
        sharpe=sharpe,
        var_99=var_99,
    )


def _trade_markers(stmt: ET.Element, ticker: str) -> list[dict[str, Any]]:
    """Return per-execution buy/sell markers for ``ticker``.

    Each entry contains the trade date, side (``"buy"`` / ``"sell"``),
    quantity, execution price in instrument currency, FX rate to base,
    and execution price in base currency.
    """
    out: list[dict[str, Any]] = []
    for t in stmt.iter("Trade"):
        if t.get("levelOfDetail") != "EXECUTION":
            continue
        if (t.get("symbol") or "") != ticker:
            continue
        d_str = (t.get("tradeDate") or "")[:8]
        if not d_str:
            continue
        d = datetime.strptime(d_str, "%Y%m%d").replace(tzinfo=UTC).date()
        qty = _to_dec(t.get("quantity"))
        price_local = _to_dec(t.get("tradePrice"))
        fx = _to_dec(t.get("fxRateToBase")) or Decimal(1)
        side = "buy" if qty >= 0 else "sell"
        out.append(
            {
                "date": d.isoformat(),
                "side": side,
                "quantity": float(abs(qty)),
                "price_local": float(price_local),
                "price_base": float(price_local * fx),
            },
        )
    out.sort(key=lambda e: e["date"])
    return out


def _symbol_series(
    ticker: str,
    base_currency: str,
    start: date,
    end: date,
) -> tuple[list[str], list[float], list[float], str]:
    """Return (labels, local_close, base_close, instrument_currency) for ``ticker``."""
    yf_sym, ccy = IBKR_TO_YF.get(ticker, (ticker, base_currency))
    yf_client = CachedYFinanceClient()
    local = yf_client.get_close_series(
        symbol=yf_sym,
        start=str(start),
        end=str(end + timedelta(days=2)),
    )
    base = yf_client.get_close_series(
        symbol=yf_sym,
        start=str(start),
        end=str(end + timedelta(days=2)),
        instrument_currency=ccy,
        base_currency=base_currency,
    )
    for s in (local, base):
        idx: Any = s.index
        if getattr(idx, "tz", None) is not None:
            s.index = idx.tz_localize(None)
    labels = [pd.Timestamp(t).date().isoformat() for t in local.index]
    return labels, [float(v) for v in local.to_numpy()], [float(v) for v in base.to_numpy()], ccy


def _build_app(xml_path: Path, config: DashboardConfig, trailing_days: int) -> FastAPI:  # noqa: PLR0915
    """Construct the FastAPI app with routes preloaded with parsed data."""
    from src.apps.finances_dashboard.ajbell_provider import (
        AJBELL_ACCOUNT_PREFIX,
        load_ajbell_statements,
    )
    from src.apps.finances_dashboard.hl_provider import (
        HL_ACCOUNT_PREFIX,
        load_hl_statements,
    )

    app = FastAPI(title="Finances Dashboard")
    valid_accounts = set(config.all_account_ids())
    hl_statements = load_hl_statements()
    ajbell_statements = load_ajbell_statements()
    synthetic_statements = {**hl_statements, **ajbell_statements}
    synthetic_prefixes = (HL_ACCOUNT_PREFIX, AJBELL_ACCOUNT_PREFIX)
    aggregates: dict[str, tuple[Any, list[str]]] = {}
    for u in config.users:
        ids = [a.id for b in u.brokers for a in b.accounts]
        if len(ids) >= 2:  # noqa: PLR2004
            aggregates[f"all-{_user_slug(u.name)}"] = (u, ids)

    def _resolve(account_id: str) -> tuple[ET.Element, str, bool]:
        if account_id in aggregates:
            _user, ids = aggregates[account_id]
            ibkr_ids = [i for i in ids if not i.startswith(synthetic_prefixes)]
            synth_ids = [i for i in ids if i.startswith(synthetic_prefixes)]
            if ibkr_ids:
                stmt, base = _parse_aggregate(xml_path, ibkr_ids)
            else:
                stmt = ET.Element("AggregateStatement")
                base = "GBP"
            for sid in synth_ids:
                synth_stmt = synthetic_statements.get(sid)
                if synth_stmt is not None:
                    stmt.append(synth_stmt)
            return stmt, base, True
        if account_id.startswith(synthetic_prefixes):
            synth_stmt = synthetic_statements.get(account_id)
            if synth_stmt is None:
                raise RuntimeError(f"Synthetic account not loaded: {account_id}")
            return synth_stmt, "GBP", False
        if account_id in valid_accounts:
            stmt, base = _parse_account(xml_path, account_id)
            return stmt, base, False
        raise RuntimeError(f"Unknown account_id: {account_id}")

    @app.get("/", response_class=HTMLResponse)
    def root() -> RedirectResponse:  # pyright: ignore[reportUnusedFunction]
        """Redirect to the first configured account."""
        return RedirectResponse(url=f"/account/{config.first_account_id()}")

    @app.get("/account/{account_id}", response_class=HTMLResponse)
    def index(account_id: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """Render the dashboard page for ``account_id``."""
        stmt, base, is_agg = _resolve(account_id)
        if is_agg:
            # Build per-leaf positions tagged with their account_id so the
            # aggregate row can preserve its contributors for drill-down.
            leaf_positions: list[PositionRow] = []
            for sub in stmt.findall("FlexStatement"):
                leaf_positions.extend(
                    _open_positions(sub, account_id=sub.get("accountId") or ""),
                )
            positions = _aggregate_position_rows(leaf_positions)
        else:
            positions = _open_positions(stmt)
        closed = _closed_positions(stmt)
        timeline = _build_timeline(
            stmt,
            base,
            max(trailing_days, 1095),
            cache_key=None if is_agg else account_id,
        )
        deposit_events = [
            (d, amt)
            for d, kind, amt, lbl, _pnl in _events(stmt, base)
            if kind == "cash" and lbl == "Deposits/Withdrawals"
        ]
        rf_returns = (
            _risk_free_daily_returns(timeline[0].date, timeline[-1].date)
            if timeline
            else {}
        )
        stats = _summarise(timeline, deposit_events, rf_returns)
        return _render_html(account_id, base, stats, positions, closed, timeline, config)

    cache: dict[str, dict[str, Any]] = {}

    @app.get("/api/{account_id}/portfolio")
    def portfolio(account_id: str) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        """Return daily portfolio value plus all buy/sell trade markers."""
        stmt, base, _is_agg = _resolve(account_id)
        timeline = _build_timeline(
            stmt,
            base,
            max(trailing_days, 1095),
            cache_key=None if _is_agg else account_id,
        )
        labels = [t.date.isoformat() for t in timeline]
        values = [float(t.cash + t.deployed + t.unrealized + t.realized) for t in timeline]
        window: set[str] = set(labels)
        markers: list[dict[str, Any]] = []
        for t in stmt.iter("Trade"):
            if t.get("levelOfDetail") != "EXECUTION":
                continue
            sym = t.get("symbol") or ""
            if "." in sym:
                continue
            d_str = (t.get("tradeDate") or "")[:8]
            if not d_str:
                continue
            d_iso = (
                datetime.strptime(d_str, "%Y%m%d")
                .replace(tzinfo=UTC)
                .date()
                .isoformat()
            )
            if d_iso not in window:
                continue
            qty = _to_dec(t.get("quantity"))
            price_local = _to_dec(t.get("tradePrice"))
            fx = _to_dec(t.get("fxRateToBase")) or Decimal(1)
            idx = labels.index(d_iso)
            markers.append(
                {
                    "date": d_iso,
                    "x": idx,
                    "y": values[idx],
                    "side": "buy" if qty >= 0 else "sell",
                    "ticker": sym,
                    "ccy": t.get("currency") or "",
                    "quantity": float(abs(qty)),
                    "price_local": float(price_local),
                    "price_base": float(price_local * fx),
                },
            )
        markers.sort(key=lambda m: m["date"])
        return JSONResponse(
            {
                "labels": labels,
                "values": values,
                "markers": markers,
                "base_ccy": base,
            },
        )

    @app.get("/api/{account_id}/symbol/{ticker}")
    def symbol(account_id: str, ticker: str) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        """Return JSON price series + trade markers for ``ticker``."""
        stmt, base, _is_agg = _resolve(account_id)
        markers = _trade_markers(stmt, ticker)
        if markers:
            start = date.fromisoformat(markers[0]["date"]) - timedelta(days=14)
        else:
            start = datetime.now(UTC).date() - timedelta(days=181)
        end = datetime.now(UTC).date() - timedelta(days=1)
        cache_key = f"{ticker}|{start}|{end}"
        if cache_key in cache:
            payload = dict(cache[cache_key])
            payload["markers"] = markers
            return JSONResponse(payload)
        labels: list[str] = []
        local: list[float] = []
        base_series: list[float] = []
        ccy = base
        error: str | None = None
        try:
            labels, local, base_series, ccy = _symbol_series(ticker, base, start, end)
        except Exception as exc:
            logger.exception("yfinance fetch failed for %s", ticker)
            error = f"{type(exc).__name__}: {exc}"
        # The Trade row's fxRateToBase is FX into the *statement's* base, which
        # may not match the chart's base (e.g. aggregate "All Accounts" view is
        # GBP but the IBKR statement is USD). Recompute price_base from the
        # local/base close ratio on or before the marker date so the buy/sell
        # markers always live on the same y-scale as the price line.
        if labels and local and base_series:
            label_to_idx = {lab: i for i, lab in enumerate(labels)}
            for m in markers:
                d = m["date"]
                idx = label_to_idx.get(d)
                if idx is None:
                    # Walk back to the nearest prior trading day.
                    candidates = [i for lab, i in label_to_idx.items() if lab <= d]
                    if not candidates:
                        continue
                    idx = max(candidates)
                loc = local[idx]
                bas = base_series[idx]
                if loc:
                    m["price_base"] = float(m["price_local"]) * (bas / loc)
        payload = {
            "ticker": ticker,
            "labels": labels,
            "local": local,
            "base": base_series,
            "markers": markers,
            "ccy": ccy,
            "base_ccy": base,
            "error": error,
        }
        if error is None:
            cache[cache_key] = {k: v for k, v in payload.items() if k != "markers"}
        return JSONResponse(payload)

    return app


def _render_html(
    account_id: str,
    base: str,
    stats: SummaryStats,
    positions: list[PositionRow],
    closed: list[ClosedRow],
    timeline: list[TimelineRow],
    config: DashboardConfig,
) -> str:
    """Render the dashboard HTML with embedded JSON data."""
    total_pnl = stats.unrealized + stats.realized
    pnl_pct = (
        total_pnl / stats.total_invested * Decimal(100)
        if stats.total_invested
        else Decimal(0)
    )
    cagr_str = f"{stats.cagr * Decimal(100):+.1f}%" if stats.cagr is not None else "—"
    cagr_cls = (
        "pos" if stats.cagr is not None and stats.cagr >= 0
        else "neg" if stats.cagr is not None
        else "muted"
    )
    pnl_cls = "pos" if total_pnl >= 0 else "neg"
    unreal_cls = "pos" if stats.unrealized >= 0 else "neg"
    real_cls = "pos" if stats.realized >= 0 else "neg"
    if stats.sharpe is None:
        sharpe_str, sharpe_cls = "—", "muted"
    else:
        sharpe_str = f"{stats.sharpe:+.2f}"
        sharpe_cls = "pos" if stats.sharpe >= 0 else "neg"
    if stats.var_99 is not None:
        var_pct = (stats.var_99 / stats.portfolio_value * 100) if stats.portfolio_value > 0 else Decimal(0)
        var_str = f"−£{stats.var_99:,.0f} ({var_pct:.1f}%)"
    else:
        var_str = "—"
    var_cls = "neg" if stats.var_99 is not None and stats.var_99 > 0 else "muted"
    def _signed_gbp(v: Decimal) -> str:
        sign = "+" if v >= 0 else "-"
        return f"{sign}£{abs(v):,.0f}"

    total_pnl_str = _signed_gbp(total_pnl)
    unreal_str = _signed_gbp(stats.unrealized)
    real_str = _signed_gbp(stats.realized)
    def _pnl_row(label: str, value: Decimal | None) -> str:
        """Render one row inside the combined PnL widget."""
        if value is None:
            return (
                f"<div class='pnl-row'><span class='pnl-k'>{label}</span>"
                f"<span class='pnl-v muted'>—</span></div>"
            )
        cls = "pos" if value >= 0 else "neg"
        return (
            f"<div class='pnl-row'><span class='pnl-k'>{label}</span>"
            f"<span class='pnl-v {cls}'>{_signed_gbp(value)}</span></div>"
        )

    pnl_rows_html = (
        _pnl_row("Total", total_pnl)
        + _pnl_row("YTD", stats.pnl_ytd)
        + _pnl_row("Last 30d", stats.pnl_30d)
        + _pnl_row("Yesterday", stats.yesterday_pnl)
    )
    found = config.find_account(account_id)
    agg_user: UserEntry | None = None
    if account_id.startswith("all-"):
        for u in config.users:
            if f"all-{_user_slug(u.name)}" == account_id:
                agg_user = u
                break
    if agg_user is not None:
        ids = [a.id for b in agg_user.brokers for a in b.accounts]
        parts = [
            "All Accounts",
            f"{len(ids)} accounts",
            agg_user.name,
            f"Base {base}",
        ]
        account_subtitle = " · ".join(p for p in parts if p)
    elif found is not None:
        owner, broker, acct = found
        parts = [
            f"Account {account_id}",
            acct.label,
            acct.description,
            broker.name,
            owner.name,
            f"Base {base}",
        ]
        account_subtitle = " · ".join(p for p in parts if p)
    else:
        account_subtitle = f"Account {account_id} · Base {base}"
    nav_blocks: list[str] = []
    for u in config.users:
        broker_blocks: list[str] = []
        for b in u.brokers:
            items = "".join(
                f'<li><a class="nav-acct{" active" if a.id == account_id else ""}" '
                f'href="/account/{a.id}"><span class="nav-acct-id">{a.id}</span>'
                f'<span class="nav-acct-label">{a.label}</span></a></li>'
                for a in b.accounts
            )
            broker_blocks.append(
                f'<div class="nav-broker"><div class="nav-broker-name">{b.name}</div>'
                f'<ul class="nav-acct-list">{items}</ul></div>',
            )
        agg_id = f"all-{_user_slug(u.name)}"
        agg_count = sum(len(b.accounts) for b in u.brokers)
        agg_html = ""
        if agg_count >= 2:  # noqa: PLR2004
            agg_active = " active" if agg_id == account_id else ""
            agg_html = (
                f'<div class="nav-broker"><div class="nav-broker-name">Aggregate</div>'
                f'<ul class="nav-acct-list"><li>'
                f'<a class="nav-acct{agg_active}" href="/account/{agg_id}">'
                f'<span class="nav-acct-id">All</span>'
                f'<span class="nav-acct-label">All Accounts</span>'
                f"</a></li></ul></div>"
            )
        nav_blocks.append(
            f'<div class="nav-user"><div class="nav-user-name">{u.name}</div>'
            + "".join(broker_blocks)
            + agg_html
            + "</div>",
        )
    nav_html = '<div class="nav-popover" role="menu">' + "".join(nav_blocks) + "</div>"
    widgets_html = f"""
    <div class="widgets">
      <div class="widget">
        <div class="w-label">Portfolio Value</div>
        <div class="w-value">£{stats.portfolio_value:,.0f}</div>
        <div class="w-sub">Total Invested £{stats.total_invested:,.0f}</div>
      </div>
      <div class="widget widget-pnl">
        <div class="w-label">Gross PnL</div>
        <div class="pnl-rows">{pnl_rows_html}</div>
        <div class="w-split">
          <span class="{unreal_cls}">Unreal {unreal_str}</span>
          <span class="{real_cls}">Real {real_str}</span>
        </div>
      </div>
      <div class="widget">
        <div class="w-label">Return</div>
        <div class="w-value {pnl_cls}">{pnl_pct:+.2f}%</div>
        <div class="w-sub">PnL / Total Invested</div>
      </div>
      <div class="widget">
        <div class="w-label">CAGR</div>
        <div class="w-value {cagr_cls}">{cagr_str}</div>
        <div class="w-sub">{stats.days_invested} day{'s' if stats.days_invested != 1 else ''} since first deposit</div>
      </div>
      <div class="widget">
        <div class="w-label">Sharpe</div>
        <div class="w-value {sharpe_cls}">{sharpe_str}</div>
        <div class="w-sub">Annualised, rf=CSH2.L</div>
      </div>
      <div class="widget">
        <div class="w-label">1d 99% VaR</div>
        <div class="w-value {var_cls}">{var_str}</div>
        <div class="w-sub">1-day historical</div>
      </div>
      <div class="widget widget-2col">
        <div>
          <div class="w-label">Capital Deployed</div>
          <div class="w-value">£{stats.deployed:,.0f}</div>
        </div>
        <div>
          <div class="w-label">Cash Available</div>
          <div class="w-value">£{stats.cash:,.0f}</div>
        </div>
      </div>
    </div>
    """
    today_for_cagr = timeline[-1].date if timeline else date.today()  # noqa: DTZ011

    def _open_cagr_cell(p: PositionRow) -> str:
        """Annualised return for an open position, rendered as a coloured ``<td>``."""
        if not p.first_purchase or not p.avg_price:
            return "<td class='num'>—</td>"
        days = (today_for_cagr - p.first_purchase).days
        if days < 30 or p.mark_price <= 0:  # noqa: PLR2004
            return "<td class='num'>—</td>"
        ratio = float(p.mark_price / p.avg_price)
        if ratio <= 0:
            return "<td class='num'>—</td>"
        cagr = ratio ** (365.0 / days) - 1.0
        cls = "pos" if cagr >= 0 else "neg"
        return f"<td class='num {cls}'>{cagr * 100:+.2f}%</td>"

    def _render_pos_row(p: PositionRow, *, is_source: bool = False) -> str:
        """Render one row, either a top-level position or an indented source row."""
        if is_source:
            row_class = "pos-source-row"
            extra_attrs = f" data-parent='{p.ticker}' hidden"
            ticker_cell = (
                f"<td class='source-cell'>"
                f"<span class='source-indent'>↳ {p.account_id or '—'}</span></td>"
            )
        else:
            indicator = "▸ " if p.sources else ""
            row_class = "pos-row" + (" pos-row-agg" if p.sources else "")
            extra_attrs = f" data-aggregated='{'1' if p.sources else '0'}'"
            ticker_cell = f"<td>{indicator}{p.ticker}</td>"
        return (
            f"<tr class='{row_class}' data-ticker='{p.ticker}'"
            f" data-exchange='{p.exchange}' data-currency='{p.currency}'"
            f" data-isin='{p.isin}' data-conid='{p.conid}'"
            f" data-sec-type='{p.sec_type}'"
            f" data-quantity='{p.quantity}'"
            f" data-avg-price='{p.avg_price}' data-mark-price='{p.mark_price}'"
            f" data-value-base='{p.value_base}'"
            f" data-unrealized='{p.unrealized_base}'"
            f" data-last-purchase='{p.last_purchase.isoformat() if p.last_purchase else ''}'"
            f" data-description=\"{p.description}\"{extra_attrs}>"
            + ticker_cell
            + f"<td>{p.currency}</td>"
            f"<td>{p.last_purchase.isoformat() if p.last_purchase else '—'}</td>"
            f"<td class='num'>{p.quantity:,.2f}</td>"
            f"<td class='num'>{p.avg_price:,.2f}</td>"
            f"<td class='num'>{p.mark_price:,.2f}</td>"
            f"<td class='num {'pos' if (p.mark_price - p.avg_price) >= 0 else 'neg'}'>"
            f"{p.mark_price - p.avg_price:+,.2f}</td>"
            f"<td class='num {'pos' if (p.mark_price - p.avg_price) >= 0 else 'neg'}'>"
            f"{((p.mark_price / p.avg_price - 1) * 100) if p.avg_price else 0:+.2f}%</td>"
            f"{_open_cagr_cell(p)}"
            f"<td class='num'>{p.value_base:,.0f}</td>"
            f"<td class='num {'pos' if p.unrealized_base >= 0 else 'neg'}'>"
            f"{p.unrealized_base:+,.0f}</td></tr>"
        )

    def _render_position_block(p: PositionRow) -> str:
        parts = [_render_pos_row(p)]
        parts.extend(_render_pos_row(s, is_source=True) for s in p.sources)
        return "".join(parts)

    pos_rows = "".join(_render_position_block(p) for p in positions)

    def _closed_ret(c: ClosedRow) -> Decimal:
        return (c.close_price / c.open_price - 1) * 100 if c.open_price else Decimal(0)

    def _closed_cagr_cell(c: ClosedRow) -> str:
        """Annualised return from open lot to close trade, rendered as a coloured ``<td>``."""
        if not c.open_date or not c.open_price or c.close_price <= 0:
            return "<td class='num'>—</td>"
        days = (c.close_date - c.open_date).days
        if days < 30:  # noqa: PLR2004
            return "<td class='num'>—</td>"
        ratio = float(c.close_price / c.open_price)
        if ratio <= 0:
            return "<td class='num'>—</td>"
        cagr = ratio ** (365.0 / days) - 1.0
        cls = "pos" if cagr >= 0 else "neg"
        return f"<td class='num {cls}'>{cagr * 100:+.2f}%</td>"

    closed_rows = (
        "".join(
            f"<tr><td>{c.close_date.isoformat()}</td><td>{c.ticker}</td>"
            f"<td>{c.exchange}</td><td>{c.currency}</td>"
            f"<td class='num'>{c.quantity:,.2f}</td>"
            f"<td class='num'>{c.open_price:,.2f}</td>"
            f"<td class='num'>{c.close_price:,.2f}</td>"
            f"<td class='num {'pos' if _closed_ret(c) >= 0 else 'neg'}'>"
            f"{_closed_ret(c):+.2f}%</td>"
            f"{_closed_cagr_cell(c)}"
            f"<td class='num {'pos' if c.realized_base >= 0 else 'neg'}'>"
            f"{c.realized_base:+,.0f}</td></tr>"
            for c in closed
        )
        if closed
        else "<tr><td colspan='10' class='empty'>No closed positions in window.</td></tr>"
    )
    pnl_labels: list[str] = []
    pnl_notional: list[float] = []
    pnl_return: list[float] = []
    pnl_cum_notional: list[float] = []
    pnl_cum_return: list[float] = []
    cum_n = 0.0
    cum_r = 1.0
    for i in range(1, len(timeline)):
        prev, cur = timeline[i - 1], timeline[i]
        delta = (cur.unrealized + cur.realized) - (prev.unrealized + prev.realized)
        sod_value = prev.cash + prev.deployed + prev.unrealized
        r = float(delta / sod_value) if sod_value > 0 else 0.0
        pnl_labels.append(cur.date.isoformat())
        pnl_notional.append(float(delta))
        pnl_return.append(r)
        cum_n += float(delta)
        cum_r *= 1.0 + r
        pnl_cum_notional.append(cum_n)
        pnl_cum_return.append(cum_r - 1.0)
    chart_data = {
        "labels": [t.date.isoformat() for t in timeline],
        "cash": [float(t.cash) for t in timeline],
        "deployed": [float(t.deployed) for t in timeline],
        "unrealized": [float(t.unrealized) for t in timeline],
        "realized": [float(t.realized) for t in timeline],
        "deposited": [float(t.total_deposited) for t in timeline],
        "pnl_labels": pnl_labels,
        "pnl_notional": pnl_notional,
        "pnl_return": pnl_return,
        "pnl_cum_notional": pnl_cum_notional,
        "pnl_cum_return": pnl_cum_return,
    }
    data_json = json.dumps(chart_data)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>IBKR Dashboard — {account_id}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0"></script>
<style>
  :root {{
    --bg:#0b0d10; --panel:#11151a; --panel-2:#161b22; --border:#222933;
    --text:#d7dde5; --muted:#8b95a3; --accent:#e8b341;
    --pos:#3fb27f; --neg:#e0584b;
    --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
         margin: 0; padding: 18px; background:var(--bg); color:var(--text); }}
  header {{ display:flex; align-items:baseline; gap:16px; margin: 0 0 20px;
            border-bottom: 1px solid var(--border); padding-bottom: 14px; }}
  header h1 {{ font-size: 13px; font-weight: 600; letter-spacing: .12em;
               text-transform: uppercase; color: var(--accent); margin:0; }}
  header .sub {{ font-size: 12px; color: var(--muted); font-family: var(--mono); }}
  .grid {{ display:grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  .card {{ background: var(--panel); border:1px solid var(--border); border-radius:4px;
           padding: 12px 14px; height: 380px; display:flex; flex-direction:column;
           overflow: hidden; min-width: 0; }}
  .card > canvas {{ flex:1; height: auto !important; width: 100% !important; min-height: 0; }}
  .card .tables-scroll {{ flex:1; overflow: auto; min-height: 0; }}
  .card h2 {{ font-size: 10.5px; font-weight: 600; letter-spacing: .14em;
              text-transform: uppercase; color: var(--muted); margin: 0 0 8px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 10.75px;
           font-family: var(--mono); table-layout: auto; }}
  th, td {{ text-align:left; padding: 2px 4px; border-bottom: 1px solid var(--border);
            white-space: nowrap; }}
  th:first-child, td:first-child {{ padding-left: 2px; }}
  th:last-child, td:last-child {{ padding-right: 2px; }}
  th {{ font-weight: 600; color: var(--muted); font-size: 9.5px;
        letter-spacing: .06em; text-transform: uppercase; user-select: none; }}
  th.sort-asc::after {{ content: ' ▲'; color: var(--accent); }}
  th.sort-desc::after {{ content: ' ▼'; color: var(--accent); }}
  tr:last-child td {{ border-bottom: none; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.pos {{ color: var(--pos); }} td.neg {{ color: var(--neg); }}
  td.empty {{ color: var(--muted); text-align:center; padding: 14px; font-style: italic; }}
  tr.pos-row {{ cursor:pointer; }}
  tr.pos-row:hover td {{ background: var(--panel-2); }}
  tr.pos-row.selected td {{ background: rgba(232,179,65,0.08); color: var(--accent); }}
  tr.pos-row-agg.expanded td:first-child {{ color: var(--accent); }}
  tr.pos-source-row td {{ background: var(--panel-2); font-size: 12px; color: var(--muted); }}
  tr.pos-source-row .source-cell {{ padding-left: 12px; }}
  tr.pos-source-row .source-indent {{ opacity: 0.75; }}
  .card-head {{ display:flex; align-items:center; gap:10px;
                margin: 0 0 8px; flex-wrap:wrap; }}
  .card-head h2 {{ margin: 0; }}
  .card-head .toolbar {{ margin: 0; }}
  .card-head .spacer {{ flex: 1; }}
  .toolbar {{ display:flex; gap:6px; margin-bottom:14px; }}
  .toolbar button {{ background:transparent; color:var(--muted); border:1px solid var(--border);
                     border-radius:3px; padding:3px 8px; font-family:var(--mono); font-size:9.5px;
                     letter-spacing:.08em; text-transform:uppercase; cursor:pointer; }}
  .toolbar button.active {{ color:var(--accent); border-color:var(--accent); }}
  .toolbar button:disabled {{ opacity:.4; cursor:not-allowed; }}
  .placeholder {{ color: var(--muted); font-family: var(--mono); font-size: 12px;
                  text-align:center; padding: 50px 20px; font-style: italic;
                  border: 1px dashed var(--border); border-radius: 3px; }}
  #detail-canvas {{ width:100% !important; }}
  #pos-tooltip {{ position: fixed; pointer-events: none; z-index: 1000;
                  background: var(--panel-2); border: 1px solid var(--border);
                  padding: 8px 12px; border-radius: 4px;
                  font-family: var(--mono); font-size: 11px;
                  display: none; max-width: 340px;
                  box-shadow: 0 4px 12px rgba(0,0,0,0.4); }}
  #pos-tooltip .row {{ display:flex; justify-content:space-between; gap:16px; line-height:1.5; }}
  #pos-tooltip .k {{ color: var(--muted); text-transform:uppercase;
                     letter-spacing:.08em; font-size:9.5px; }}
  #pos-tooltip .v {{ color: var(--text); font-variant-numeric: tabular-nums; }}
  #pos-tooltip .desc {{ color: var(--text); font-style: italic; margin-bottom:4px;
                        border-bottom: 1px solid var(--border); padding-bottom:4px; }}
  #detail-empty {{ color:var(--muted); font-family:var(--mono); font-size:12px;
                   text-align:center; padding: 80px 20px; font-style: italic; }}
  .widgets {{ display:grid; grid-template-columns: repeat(8, 1fr); gap: 8px; margin-bottom: 10px; }}
  .widget {{ background: var(--panel); border:1px solid var(--border); border-radius:4px;
             padding: 7px 10px; display:flex; flex-direction:column; gap:1px; }}
  .widget.widget-2col {{ flex-direction:row; gap:14px; }}
  .widget.widget-2col > div {{ flex:1; display:flex; flex-direction:column; gap:1px; }}
  .widget.widget-pnl {{ grid-column: span 2; gap:3px; }}
  .pnl-rows {{ display:grid; grid-template-columns: 1fr 1fr; gap:1px 14px;
               font-family: var(--mono); font-size: 11.5px; line-height:1.25; }}
  .pnl-row {{ display:flex; justify-content:space-between; align-items:baseline; }}
  .pnl-k {{ color: var(--muted); font-size: 9.5px; letter-spacing:.06em;
            text-transform:uppercase; }}
  .pnl-v {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
  .w-label {{ font-size:9px; font-weight:600; letter-spacing:.14em;
              text-transform:uppercase; color: var(--muted); }}
  .w-value {{ font-family: var(--mono); font-size: 15px; font-weight: 600;
              font-variant-numeric: tabular-nums; line-height:1.15; }}
  .w-sub {{ font-size: 9.5px; color: var(--muted); font-family: var(--mono); line-height:1.15; }}
  .w-split {{ display:flex; gap:10px; font-family: var(--mono); font-size: 10px; line-height:1.15; }}
  .pos {{ color: var(--pos); }} .neg {{ color: var(--neg); }} .muted {{ color: var(--muted); }}
  .grid {{ gap: 12px !important; }}
  .detail-stats {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
                   gap: 8px 14px; margin: 0 0 10px 0; padding: 8px 10px;
                   background: var(--panel-2); border: 1px solid var(--border);
                   border-radius: 4px; }}
  .detail-stats .ds-cell {{ display:flex; flex-direction:column; gap:1px; }}
  .detail-stats .ds-k {{ font-size: 9px; font-weight:600; letter-spacing:.12em;
                         text-transform: uppercase; color: var(--muted); }}
  .detail-stats .ds-v {{ font-family: var(--mono); font-size: 12.5px; font-weight: 600;
                         font-variant-numeric: tabular-nums; }}
  .detail-stats .ds-desc {{ grid-column: 1 / -1; font-size: 11px; color: var(--muted);
                            font-family: var(--mono); }}
  canvas#chart, canvas#pnl-canvas {{ width: 100% !important; }}
  .nav-anchor {{ position: relative; margin-left:auto; }}
  .nav-trigger {{ background: transparent; color: var(--muted);
                  border: 1px solid var(--border); border-radius: 3px;
                  padding: 4px 10px; font-family: var(--mono); font-size: 10.5px;
                  letter-spacing: .08em; text-transform: uppercase; cursor: pointer; }}
  .nav-trigger:hover {{ color: var(--accent); border-color: var(--accent); }}
  .nav-popover {{ display: none; position: absolute; right: 0; top: calc(100% + 6px);
                  background: var(--panel); border: 1px solid var(--border);
                  border-radius: 4px; padding: 10px 12px; min-width: 220px;
                  z-index: 20; box-shadow: 0 6px 24px rgba(0,0,0,0.45); }}
  .nav-popover.open {{ display: block; }}
  .nav-user {{ margin-bottom: 10px; }}
  .nav-user:last-child {{ margin-bottom: 0; }}
  .nav-user-name {{ font-size: 10.5px; font-weight: 600; letter-spacing:.12em;
                    text-transform: uppercase; color: var(--accent);
                    padding: 0 0 5px 0; border-bottom: 1px solid var(--border);
                    margin-bottom: 4px; }}
  .nav-broker {{ margin: 6px 0 8px 0; }}
  .nav-broker:last-child {{ margin-bottom: 0; }}
  .nav-broker-name {{ font-size: 10px; font-weight: 600; letter-spacing:.1em;
                      text-transform: uppercase; color: var(--muted);
                      margin: 4px 0 3px 0; }}
  .nav-acct-list {{ list-style:none; margin:0; padding:0 0 0 6px; }}
  .nav-acct-list li {{ margin: 1px 0; }}
  .nav-acct {{ display:flex; flex-direction:column; gap:1px;
               padding: 5px 8px; border-radius: 3px;
               text-decoration:none; color: var(--text);
               font-family: var(--mono); font-size: 11px; }}
  .nav-acct:hover {{ background: var(--panel-2); }}
  .nav-acct.active {{ background: rgba(232,179,65,0.08);
                      color: var(--accent); border-left: 2px solid var(--accent);
                      padding-left: 6px; }}
  .nav-acct-id {{ font-size: 11px; }}
  .nav-acct-label {{ font-size: 9.5px; color: var(--muted); }}
  .nav-acct.active .nav-acct-label {{ color: var(--accent); opacity:0.7; }}
</style>
</head>
<body>
<header>
  <h1>IBKR Portfolio</h1>
  <span class="sub">{account_subtitle}</span>
  <span class="nav-anchor">
    <button id="nav-trigger" class="nav-trigger" aria-haspopup="true" aria-expanded="false">Accounts ▾</button>
    {nav_html}
  </span>
</header>
{widgets_html}
<div class="grid">
  <div class="card">
    <h2>Open Positions</h2>
    <div class="tables-scroll">
      <table class="sortable">
        <thead><tr><th>Ticker</th><th>Ccy</th>
          <th>Last Buy</th>
          <th class="num">Qty</th><th class="num">Avg</th><th class="num">Mark</th>
          <th class="num">Δ Unit</th>
          <th class="num">Ret %</th>
          <th class="num">CAGR</th>
          <th class="num">Val ({base})</th><th class="num">Unrl ({base})</th></tr></thead>
        <tbody>{pos_rows}</tbody>
      </table>
      <h2 style="margin-top:18px;">Closed Positions</h2>
      <table class="sortable">
        <thead><tr><th>Date</th><th>Ticker</th><th>Exch</th><th>Ccy</th>
          <th class="num">Qty</th><th class="num">Open</th>
          <th class="num">Close</th><th class="num">Ret %</th>
          <th class="num">CAGR</th>
          <th class="num">Real ({base})</th></tr></thead>
        <tbody>{closed_rows}</tbody>
      </table>
    </div>
  </div>
  <div class="card">
    <div class="card-head">
      <h2>Daily Snapshot · <span id="snapshot-range">Trailing 1M</span></h2>
      <span class="spacer"></span>
      <div class="toolbar">
        <button class="lookback-btn" data-target="snapshot" data-days="30">1M</button>
        <button class="lookback-btn" data-target="snapshot" data-days="90">3M</button>
        <button class="lookback-btn" data-target="snapshot" data-days="365">1Y</button>
        <button class="lookback-btn" data-target="snapshot" data-days="1095">3Y</button>
      </div>
    </div>
    <canvas id="chart"></canvas>
  </div>
</div>
<div class="grid" style="margin-top:20px;">
  <div class="card">
    <div class="card-head">
      <h2 id="detail-title">Symbol Detail</h2>
      <span class="spacer"></span>
      <div class="toolbar">
        <button id="btn-base" class="active" disabled>Base ({base})</button>
        <button id="btn-local" disabled>Local</button>
      </div>
      <div class="toolbar">
        <button class="lookback-btn" data-target="detail" data-days="30">1M</button>
        <button class="lookback-btn" data-target="detail" data-days="90">3M</button>
        <button class="lookback-btn" data-target="detail" data-days="365">1Y</button>
        <button class="lookback-btn" data-target="detail" data-days="1095">3Y</button>
      </div>
    </div>
    <div id="detail-empty">Select a row in Open Positions to view its price history.</div>
    <canvas id="detail-canvas" style="display:none;"></canvas>
  </div>
  <div class="card">
    <div class="card-head">
      <h2 id="pnl-title">Portfolio Daily PnL ({base})</h2>
      <span class="spacer"></span>
      <div class="toolbar">
        <button id="btn-pnl-notional" class="active">Notional ({base})</button>
        <button id="btn-pnl-return">Returns (% SOD)</button>
      </div>
      <div class="toolbar">
        <button class="lookback-btn" data-target="pnl" data-days="30">1M</button>
        <button class="lookback-btn" data-target="pnl" data-days="90">3M</button>
        <button class="lookback-btn" data-target="pnl" data-days="365">1Y</button>
        <button class="lookback-btn" data-target="pnl" data-days="1095">3Y</button>
      </div>
    </div>
    <canvas id="pnl-canvas"></canvas>
  </div>
</div>
<div id="pos-tooltip"></div>
<script>
const data = {data_json};
Chart.defaults.color = '#8b95a3';
Chart.defaults.font.family = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace';
Chart.defaults.font.size = 11;
const grid = 'rgba(255,255,255,0.05)';
function fmtK(v, prefix) {{
  const n = Number(v);
  const a = Math.abs(n);
  const sign = n < 0 ? '-' : '';
  const sym = prefix == null ? '' : prefix;
  if (a >= 1e6) return sign + sym + (a / 1e6).toFixed(1) + 'm';
  if (a >= 1e3) return sign + sym + (a / 1e3).toFixed(1) + 'k';
  return sign + sym + a.toFixed(0);
}}
const ctx = document.getElementById('chart');
let snapshotLookback = 30;
function tail(arr, n) {{ return arr.slice(Math.max(0, arr.length - n)); }}
const snapshotChart = new Chart(ctx, {{
  data: {{
    labels: tail(data.labels, snapshotLookback),
    datasets: [
      {{ type:'bar', label:'Cash', data: tail(data.cash, snapshotLookback), backgroundColor:'#4a4f57', stack:'s', borderWidth:0 }},
      {{ type:'bar', label:'Capital Deployed', data: tail(data.deployed, snapshotLookback), backgroundColor:'#7e858f', stack:'s', borderWidth:0 }},
      {{ type:'bar', label:'Unrealised P&L', data: tail(data.unrealized, snapshotLookback), backgroundColor:'#3fb27f', stack:'s', borderWidth:0 }},
      {{ type:'line', label:'Total Invested Capital', data: tail(data.deposited, snapshotLookback),
         borderColor:'#8b95a3', backgroundColor:'#8b95a3', borderWidth:1.5,
         stepped:'before', pointRadius:0, fill:false, borderDash:[4,3] }}
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    scales: {{
      x: {{ stacked: true, grid: {{ color: grid, display:false }}, ticks: {{ color:'#8b95a3' }} }},
      y: {{ stacked: true, grid: {{ color: grid }}, border: {{ color: grid }},
           ticks: {{ color:'#8b95a3', callback: v => fmtK(v, '£') }} }}
    }},
    plugins: {{
      legend: {{ position:'bottom', labels: {{ color:'#d7dde5', boxWidth:10, boxHeight:10, padding:14 }} }},
      tooltip: {{ backgroundColor:'#161b22', borderColor:'#222933', borderWidth:1,
                  titleColor:'#d7dde5', bodyColor:'#d7dde5' }}
    }},
    onClick(_evt, elements) {{
      if (!elements.length) return;
      const idx = elements[0].datasetIndex;
      const ds = snapshotChart.data.datasets;
      const others = ds.map((_, i) => i).filter(i => i !== idx);
      const isolated = others.every(i => !snapshotChart.isDatasetVisible(i));
      ds.forEach((_, i) => {{
        snapshotChart.setDatasetVisibility(i, isolated ? true : i === idx);
      }});
      snapshotChart.update();
    }}
  }}
}});

// --- Sortable tables ---
function makeSortable(table) {{
  const headers = table.querySelectorAll('thead th');
  const tbody = table.querySelector('tbody');
  headers.forEach((th, i) => {{
    th.style.cursor = 'pointer';
    th.dataset.sortDir = '';
    th.addEventListener('click', () => {{
      const rows = Array.from(tbody.querySelectorAll('tr')).filter(r => !r.querySelector('td.empty'));
      const dir = th.dataset.sortDir === 'asc' ? 'desc' : 'asc';
      headers.forEach(h => {{ h.dataset.sortDir = ''; h.classList.remove('sort-asc','sort-desc'); }});
      th.dataset.sortDir = dir;
      th.classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');
      const parseCell = (tr) => {{
        const td = tr.children[i];
        if (!td) return '';
        const txt = td.textContent.trim();
        const num = parseFloat(txt.replace(/[^0-9eE+\\-.]/g, ''));
        return Number.isFinite(num) && /[0-9]/.test(txt) ? num : txt.toLowerCase();
      }};
      rows.sort((a, b) => {{
        const va = parseCell(a), vb = parseCell(b);
        if (va < vb) return dir === 'asc' ? -1 : 1;
        if (va > vb) return dir === 'asc' ? 1 : -1;
        return 0;
      }});
      rows.forEach(r => tbody.appendChild(r));
    }});
  }});
}}
document.querySelectorAll('table.sortable').forEach(makeSortable);

// --- Daily PnL panel ---
const pnlPos = '#3fb27f', pnlNeg = '#e0584b';
let pnlMode = 'notional';
let pnlLookback = 30;
let pnlSource = {{
  labels: data.pnl_labels,
  notional: data.pnl_notional,
  return: data.pnl_return,
}};
function pnlSeries() {{
  const src = pnlMode === 'notional' ? pnlSource.notional : pnlSource.return;
  return tail(src, pnlLookback);
}}
function computeSymbolPnl(detail) {{
  // Build daily holdings, then PnL = holdings_eod_prev * (price_today - price_yesterday).
  // Realised PnL on sells (sell_price - running avg cost) * sold_qty added to that day's PnL.
  const labels = detail.labels.slice();
  const prices = detail.base.slice();
  const trades = (detail.markers || []).slice().sort((a, b) => (a.date < b.date ? -1 : 1));
  const holdings = labels.map(() => 0);
  const realized = labels.map(() => 0);
  let qty = 0; let avgCost = 0;
  let ti = 0;
  for (let i = 0; i < labels.length; i++) {{
    while (ti < trades.length && trades[ti].date <= labels[i]) {{
      const tr = trades[ti];
      const px = tr.price_base;
      const tq = tr.quantity;
      if (tr.side === 'buy') {{
        const newQty = qty + tq;
        avgCost = newQty > 0 ? (qty * avgCost + tq * px) / newQty : 0;
        qty = newQty;
      }} else {{
        const closed = Math.min(tq, qty);
        realized[i] += (px - avgCost) * closed;
        qty -= tq;
        if (qty <= 0) {{ qty = 0; avgCost = 0; }}
      }}
      ti++;
    }}
    holdings[i] = qty;
  }}
  const notional = labels.map(() => 0);
  const ret = labels.map(() => 0);
  for (let i = 1; i < labels.length; i++) {{
    const prevQty = holdings[i - 1];
    const dPx = prices[i] - prices[i - 1];
    const mtm = prevQty * dPx;
    const day = mtm + realized[i];
    notional[i] = day;
    const sod = prevQty * prices[i - 1];
    ret[i] = sod > 0 ? day / sod : 0;
  }}
  return {{ labels, notional, return: ret }};
}}
function pnlColors() {{ return pnlSeries().map(v => v >= 0 ? pnlPos : pnlNeg); }}
function pnlCumSeries() {{
  const daily = pnlSeries();
  const out = [];
  if (pnlMode === 'notional') {{
    let s = 0; for (const v of daily) {{ s += v; out.push(s); }}
  }} else {{
    let p = 1; for (const v of daily) {{ p *= 1 + v; out.push(p - 1); }}
  }}
  return out;
}}
function pnlFmt(v) {{
  return pnlMode === 'notional'
    ? (v >= 0 ? '+' : '−') + '£' + Math.abs(v).toLocaleString(undefined, {{maximumFractionDigits:0}})
    : (v >= 0 ? '+' : '−') + (Math.abs(v) * 100).toFixed(2) + '%';
}}
const pnlChart = new Chart(document.getElementById('pnl-canvas'), {{
  data: {{
    labels: tail(data.pnl_labels, pnlLookback),
    datasets: [
      {{ type:'bar', label: 'Daily PnL', data: pnlSeries(),
         backgroundColor: pnlColors(), borderWidth: 0, yAxisID: 'y',
         order: 2 }},
      {{ type:'line', label: 'Cumulative', data: pnlCumSeries(),
         borderColor:'#8b95a3', backgroundColor:'#8b95a3',
         borderWidth: 1.5, pointRadius: 0, fill: false,
         tension: 0, yAxisID: 'y1', order: 1 }}
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    interaction: {{ mode:'index', intersect:false, axis:'x' }},
    scales: {{
      x: {{ grid: {{ color: grid, display: false }}, ticks: {{ color: '#8b95a3' }} }},
      y: {{ position: 'left', grid: {{ color: grid }}, ticks: {{ color: '#8b95a3',
           callback: v => pnlMode === 'notional'
             ? fmtK(v, '£')
             : (Number(v) * 100).toFixed(1) + '%' }} }},
      y1: {{ position: 'right', grid: {{ display: false }}, ticks: {{ color: '#8b95a3',
            callback: v => pnlMode === 'notional'
              ? fmtK(v, '£')
              : (Number(v) * 100).toFixed(1) + '%' }} }}
    }},
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ color: '#d7dde5', boxWidth: 10, boxHeight: 10, padding: 14 }} }},
      tooltip: {{ backgroundColor: '#161b22', borderColor: '#222933', borderWidth: 1,
                  titleColor: '#d7dde5', bodyColor: '#d7dde5',
                  callbacks: {{ label: c => c.dataset.label + ': ' + pnlFmt(c.parsed.y) }} }}
    }}
  }}
}});
function setPnlMode(mode) {{
  pnlMode = mode;
  document.getElementById('btn-pnl-notional').classList.toggle('active', mode === 'notional');
  document.getElementById('btn-pnl-return').classList.toggle('active', mode === 'return');
  refreshPnlChart();
}}
document.getElementById('btn-pnl-notional').addEventListener('click', () => setPnlMode('notional'));
document.getElementById('btn-pnl-return').addEventListener('click', () => setPnlMode('return'));

// --- Lookback toolbars ---
function setSnapshotLookback(n) {{
  snapshotLookback = n;
  snapshotChart.data.labels = tail(data.labels, n);
  const keys = ['cash','deployed','unrealized','deposited'];
  keys.forEach((k, i) => {{ snapshotChart.data.datasets[i].data = tail(data[k], n); }});
  snapshotChart.update();
  const lbl = n >= 1095 ? 'Trailing 3Y' : n === 365 ? 'Trailing 1Y' : n === 90 ? 'Trailing 3M' : 'Trailing 1M';
  document.getElementById('snapshot-range').textContent = lbl;
}}
function setPnlLookback(n) {{
  pnlLookback = n;
  refreshPnlChart();
}}
function refreshPnlChart() {{
  pnlChart.data.labels = tail(pnlSource.labels, pnlLookback);
  pnlChart.data.datasets[0].data = pnlSeries();
  pnlChart.data.datasets[0].backgroundColor = pnlColors();
  pnlChart.data.datasets[1].data = pnlCumSeries();
  pnlChart.update();
}}
document.querySelectorAll('.lookback-btn').forEach(btn => {{
  const days = parseInt(btn.dataset.days, 10);
  const target = btn.dataset.target;
  const defaultDays = target === 'detail' ? 365 : 30;
  if (days === defaultDays) btn.classList.add('active');
  btn.addEventListener('click', () => {{
    const t = btn.dataset.target;
    document.querySelectorAll('.lookback-btn[data-target="' + t + '"]')
      .forEach(b => b.classList.toggle('active', b === btn));
    if (t === 'snapshot') setSnapshotLookback(days);
    else if (t === 'pnl') setPnlLookback(days);
    else if (t === 'detail') setDetailLookback(days);
  }});
}});

// --- Symbol detail panel ---
let detailChart = null;
let detailData = null;
let detailMode = 'base';
let detailLookback = 365;
let detailKind = 'portfolio';

function detailLabels() {{
  if (!detailData) return [];
  return tail(detailData.labels, detailLookback);
}}
function detailSeries(arr) {{ return tail(arr, detailLookback); }}
function detailFirstLabel() {{
  const lbls = detailLabels();
  return lbls.length ? lbls[0] : null;
}}
function priceFor(side) {{
  const first = detailFirstLabel();
  return detailData.markers
    .filter(m => m.side === side && (first == null || m.date >= first))
    .map(m => ({{x: m.date, y: detailMode === 'base' ? m.price_base : m.price_local}}));
}}
function ccyLabel() {{ return detailMode === 'base' ? detailData.base_ccy : detailData.ccy; }}
function fmtCcy(v) {{
  const c = ccyLabel();
  const sym = c === 'GBP' ? '£' : c === 'USD' ? '$' : c === 'EUR' ? '€' : '';
  return sym + Number(v).toLocaleString(undefined, {{maximumFractionDigits: 2}});
}}
function applyMode() {{
  if (!detailChart || !detailData) return;
  document.getElementById('btn-base').classList.toggle('active', detailMode === 'base');
  document.getElementById('btn-local').classList.toggle('active', detailMode === 'local');
  detailChart.data.labels = detailLabels();
  const baseSrc = detailMode === 'base' ? detailData.base : detailData.local;
  detailChart.data.datasets[0].data = detailSeries(baseSrc);
  detailChart.data.datasets[1].data = priceFor('buy');
  detailChart.data.datasets[2].data = priceFor('sell');
  detailChart.update();
}}
function setDetailLookback(n) {{
  detailLookback = n;
  if (detailKind === 'symbol') applyMode();
  else if (detailKind === 'portfolio' && detailChart && detailData) {{
    detailChart.data.labels = detailLabels();
    detailChart.data.datasets[0].data = detailSeries(detailData.values);
    const cutoff = Math.max(0, detailData.labels.length - detailLookback);
    const len = detailData.labels.length - cutoff;
    const buyData = Array.from({{length: len}}, () => null);
    const sellData = Array.from({{length: len}}, () => null);
    detailData.markers.forEach(m => {{
      if (m.x < cutoff) return;
      const arr = m.side === 'buy' ? buyData : sellData;
      arr[m.x - cutoff] = m.y;
    }});
    detailChart.data.datasets[1].data = buyData;
    detailChart.data.datasets[2].data = sellData;
    detailChart.update();
  }}
}}
function renderDetailStats(row) {{
  const el = document.getElementById('detail-stats');
  if (!el) return;
  if (!row) {{ el.style.display = 'none'; el.innerHTML = ''; return; }}
  const d = row.dataset;
  const ccy = d.currency || '';
  const sym = ccy === 'GBP' ? '£' : ccy === 'USD' ? '$' : ccy === 'EUR' ? '€' : '';
  const baseSym = '£';
  const num = (v, frac) => Number(v || 0).toLocaleString(undefined, {{
    minimumFractionDigits: frac, maximumFractionDigits: frac,
  }});
  const unreal = Number(d.unrealized || 0);
  const unrealCls = unreal >= 0 ? 'pos' : 'neg';
  const unrealStr = (unreal >= 0 ? '+' : '−') + baseSym + num(Math.abs(unreal), 0);
  const cells = [
    ['Ticker', d.ticker],
    ['Exchange', d.exchange || '—'],
    ['Currency', ccy || '—'],
    ['Quantity', num(d.quantity, 2)],
    ['Avg Px', sym + num(d.avgPrice, 2)],
    ['Mark', sym + num(d.markPrice, 2)],
    ['Value (' + (d.currency ? 'base' : 'GBP') + ')', baseSym + num(d.valueBase, 0)],
    ['Last Buy', d.lastPurchase || '—'],
  ];
  let html = cells
    .map(([k, v]) => '<div class="ds-cell"><span class="ds-k">' + k + '</span>'
      + '<span class="ds-v">' + v + '</span></div>')
    .join('');
  html += '<div class="ds-cell"><span class="ds-k">Unrealised PnL</span>'
    + '<span class="ds-v ' + unrealCls + '">' + unrealStr + '</span></div>';
  if (d.description) {{
    html += '<div class="ds-desc">' + d.description + '</div>';
  }}
  el.innerHTML = html;
  el.style.display = 'grid';
}}
async function loadSymbol(ticker) {{
  let selectedRow = null;
  document.querySelectorAll('tr.pos-row').forEach(r => {{
    const match = r.dataset.ticker === ticker;
    r.classList.toggle('selected', match);
    if (match) selectedRow = r;
  }});
  renderDetailStats(selectedRow);
  const titleEl = document.getElementById('detail-title');
  const emptyEl = document.getElementById('detail-empty');
  const canvasEl = document.getElementById('detail-canvas');
  titleEl.textContent = 'Stock ' + ticker + ' Value · loading…';
  const res = await fetch('/api/{account_id}/symbol/' + encodeURIComponent(ticker));
  if (!res.ok) {{ titleEl.textContent = 'Stock ' + ticker + ' Value · error'; return; }}
  detailData = await res.json();
  detailKind = 'symbol';
  titleEl.textContent = 'Stock ' + ticker + ' Value (' + detailData.base_ccy + ')';
  const pnlTitleEl = document.getElementById('pnl-title');
  if (pnlTitleEl) pnlTitleEl.textContent = 'Stock ' + ticker + ' Daily PnL (' + detailData.base_ccy + ')';
  if (detailData.labels && detailData.labels.length) {{
    pnlSource = computeSymbolPnl(detailData);
    refreshPnlChart();
  }}
  document.getElementById('btn-local').textContent = 'Local (' + detailData.ccy + ')';
  if (detailData.error || !detailData.labels.length) {{
    emptyEl.style.display = 'block';
    emptyEl.textContent = detailData.error
      ? ticker + ': no price data (' + detailData.error + ')'
      : ticker + ': no price data available.';
    canvasEl.style.display = 'none';
    if (detailChart) {{ detailChart.destroy(); detailChart = null; }}
    return;
  }}
  document.getElementById('btn-base').disabled = false;
  document.getElementById('btn-local').disabled = false;
  emptyEl.style.display = 'none';
  canvasEl.style.display = 'block';
  if (detailChart) detailChart.destroy();
  const dctx = canvasEl.getContext('2d');
  detailChart = new Chart(dctx, {{
    data: {{
      labels: detailLabels(),
      datasets: [
        {{ type:'line', label:'Close', data: detailSeries(detailMode === 'base' ? detailData.base : detailData.local),
           borderColor:'#7e858f', backgroundColor:'#7e858f',
           borderWidth:1.5, pointRadius:0, fill:false, tension:0 }},
        {{ type:'scatter', label:'Buy', data: priceFor('buy'),
           backgroundColor:'#3fb27f', borderColor:'#3fb27f',
           pointStyle:'triangle', radius:9, rotation:0 }},
        {{ type:'scatter', label:'Sell', data: priceFor('sell'),
           backgroundColor:'#e0584b', borderColor:'#e0584b',
           pointStyle:'triangle', radius:9, rotation:180 }}
      ]
    }},
    options: {{
      responsive:true, maintainAspectRatio:false,
      scales: {{
        x: {{ type:'category', grid:{{color:grid, display:false}}, ticks:{{color:'#8b95a3', maxTicksLimit:12}} }},
        y: {{ grid:{{color:grid}}, ticks:{{color:'#8b95a3', callback: fmtCcy}} }}
      }},
      plugins: {{
        legend:{{ position:'bottom', labels:{{color:'#d7dde5', boxWidth:10, boxHeight:10, padding:14, usePointStyle:true}} }},
        tooltip:{{ backgroundColor:'#161b22', borderColor:'#222933', borderWidth:1,
                   titleColor:'#d7dde5', bodyColor:'#d7dde5',
                   callbacks: {{ label: c => c.dataset.label + ': ' + fmtCcy(c.parsed.y) }} }}
      }}
    }}
  }});
}}
async function showPortfolio() {{
  document.querySelectorAll('tr.pos-row').forEach(r => r.classList.remove('selected'));
  renderDetailStats(null);
  pnlSource = {{
    labels: data.pnl_labels,
    notional: data.pnl_notional,
    return: data.pnl_return,
  }};
  refreshPnlChart();
  detailData = null;
  document.getElementById('btn-base').disabled = true;
  document.getElementById('btn-local').disabled = true;
  document.getElementById('btn-local').textContent = 'Local';
  const titleEl = document.getElementById('detail-title');
  const emptyEl = document.getElementById('detail-empty');
  const canvasEl = document.getElementById('detail-canvas');
  titleEl.textContent = 'Portfolio Value · loading…';
  const res = await fetch('/api/{account_id}/portfolio');
  if (!res.ok) {{ titleEl.textContent = 'Portfolio Value · error'; return; }}
  const pf = await res.json();
  detailData = pf;
  detailKind = 'portfolio';
  titleEl.textContent = 'Portfolio Value (' + pf.base_ccy + ')';
  const pnlTitleEl = document.getElementById('pnl-title');
  if (pnlTitleEl) pnlTitleEl.textContent = 'Portfolio Daily PnL (' + pf.base_ccy + ')';
  emptyEl.style.display = 'none';
  canvasEl.style.display = 'block';
  if (detailChart) detailChart.destroy();
  const sym = pf.base_ccy === 'GBP' ? '£' : pf.base_ccy === 'USD' ? '$' : pf.base_ccy === 'EUR' ? '€' : '';
  const fmtBase = v => fmtK(v, sym);
  const cutoff = Math.max(0, pf.labels.length - detailLookback);
  const slicedLabels = pf.labels.slice(cutoff);
  const slicedValues = pf.values.slice(cutoff);
  const buyData = slicedLabels.map(() => null);
  const sellData = slicedLabels.map(() => null);
  const buyMeta = slicedLabels.map(() => null);
  const sellMeta = slicedLabels.map(() => null);
  pf.markers.forEach(m => {{
    if (m.x < cutoff) return;
    const arr = m.side === 'buy' ? buyData : sellData;
    const meta = m.side === 'buy' ? buyMeta : sellMeta;
    arr[m.x - cutoff] = m.y;
    meta[m.x - cutoff] = m;
  }});
  detailChart = new Chart(canvasEl.getContext('2d'), {{
    data: {{
      labels: slicedLabels,
      datasets: [
        {{ type:'line', label:'Portfolio Value', data: slicedValues,
           borderColor:'#7e858f', backgroundColor:'rgba(126,133,143,0.15)',
           borderWidth:1.5, pointRadius:0, fill:true, tension:0 }},
        {{ type:'line', label:'Buy', data: buyData, _meta: buyMeta,
           showLine:false, spanGaps:false,
           backgroundColor:'#3fb27f', borderColor:'#3fb27f',
           pointStyle:'triangle', pointRadius:9, pointHoverRadius:11, pointRotation:0 }},
        {{ type:'line', label:'Sell', data: sellData, _meta: sellMeta,
           showLine:false, spanGaps:false,
           backgroundColor:'#e0584b', borderColor:'#e0584b',
           pointStyle:'triangle', pointRadius:9, pointHoverRadius:11, pointRotation:180 }}
      ]
    }},
    options: {{
      responsive:true, maintainAspectRatio:false,
      interaction: {{ mode:'index', intersect:false, axis:'x' }},
      hover: {{ mode:'index', intersect:false, axis:'x' }},
      scales: {{
        x: {{ type:'category', grid:{{color:grid, display:false}}, ticks:{{color:'#8b95a3', maxTicksLimit:12}} }},
        y: {{ grid:{{color:grid}}, ticks:{{color:'#8b95a3', callback: fmtBase}} }}
      }},
      plugins: {{
        legend:{{ position:'bottom', labels:{{color:'#d7dde5', boxWidth:10, boxHeight:10, padding:14, usePointStyle:true}} }},
        tooltip:{{ backgroundColor:'#161b22', borderColor:'#222933', borderWidth:1,
                   titleColor:'#d7dde5', bodyColor:'#d7dde5',
                   callbacks: {{
                     label: c => {{
                       if (c.parsed.y === null || c.parsed.y === undefined) return null;
                       if (c.dataset.label === 'Portfolio Value') return 'Value: ' + fmtBase(c.parsed.y);
                       const m = (c.dataset._meta || [])[c.dataIndex];
                       if (!m) return c.dataset.label;
                       const ccySym = m.ccy === 'GBP' ? '£' : m.ccy === 'USD' ? '$' : m.ccy === 'EUR' ? '€' : '';
                       const px = ccySym + Number(m.price_local).toLocaleString(undefined, {{maximumFractionDigits: 2}});
                       return c.dataset.label + ' ' + m.ticker + ' · ' + m.quantity + ' @ ' + px;
                     }}
                   }} }}
      }}
    }}
  }});
}}
function clearSymbol() {{ showPortfolio(); }}
const posTooltip = document.getElementById('pos-tooltip');
function showPosTooltip(r, ev) {{
  const d = r.dataset;
  const rows = [
    ['Ticker', d.ticker],
    ['Description', d.description],
    ['Exchange', d.exchange],
    ['Currency', d.currency],
    ['ISIN', d.isin],
    ['ConID', d.conid],
    ['Type', d.secType],
  ];
  const html = (d.description ? '<div class="desc">' + d.description + '</div>' : '')
    + rows.filter(([k, v]) => v && k !== 'Description')
          .map(([k, v]) => '<div class="row"><span class="k">' + k + '</span><span class="v">' + v + '</span></div>')
          .join('');
  posTooltip.innerHTML = html;
  posTooltip.style.display = 'block';
  movePosTooltip(ev);
}}
function movePosTooltip(ev) {{
  const pad = 14;
  const tw = posTooltip.offsetWidth;
  const th = posTooltip.offsetHeight;
  let x = ev.clientX + pad;
  let y = ev.clientY + pad;
  if (x + tw > window.innerWidth) x = ev.clientX - tw - pad;
  if (y + th > window.innerHeight) y = ev.clientY - th - pad;
  posTooltip.style.left = x + 'px';
  posTooltip.style.top = y + 'px';
}}
function hidePosTooltip() {{ posTooltip.style.display = 'none'; }}
document.querySelectorAll('tr.pos-row').forEach(r => {{
  r.addEventListener('click', (ev) => {{
    if (ev.metaKey || ev.ctrlKey) {{ clearSymbol(); return; }}
    if (r.dataset.aggregated === '1') {{
      const ticker = r.dataset.ticker;
      const sources = document.querySelectorAll(
        `tr.pos-source-row[data-parent='${{ticker}}']`,
      );
      const willExpand = r.classList.toggle('expanded');
      sources.forEach(s => {{ s.hidden = !willExpand; }});
      const firstCell = r.querySelector('td');
      if (firstCell) firstCell.textContent =
        (willExpand ? '▾ ' : '▸ ') + ticker;
      return;
    }}
    loadSymbol(r.dataset.ticker);
  }});
  r.addEventListener('mouseenter', (ev) => showPosTooltip(r, ev));
  r.addEventListener('mousemove', movePosTooltip);
  r.addEventListener('mouseleave', hidePosTooltip);
}});
showPortfolio();
document.getElementById('btn-base').onclick = () => {{ detailMode = 'base'; applyMode(); }};
document.getElementById('btn-local').onclick = () => {{ detailMode = 'local'; applyMode(); }};

// --- Accounts popover ---
(() => {{
  const trigger = document.getElementById('nav-trigger');
  const popover = document.querySelector('.nav-popover');
  if (!trigger || !popover) return;
  function close() {{
    popover.classList.remove('open');
    trigger.setAttribute('aria-expanded', 'false');
  }}
  trigger.addEventListener('click', (e) => {{
    e.stopPropagation();
    const open = popover.classList.toggle('open');
    trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
  }});
  document.addEventListener('click', (e) => {{
    if (!popover.contains(e.target) && e.target !== trigger) close();
  }});
  document.addEventListener('keydown', (e) => {{ if (e.key === 'Escape') close(); }});
}})();
</script>
</body>
</html>"""


def main() -> int:
    """Run the dashboard server."""
    parser = argparse.ArgumentParser(description="IBKR account dashboard")
    parser.add_argument("--xml", default=str(DEFAULT_XML), help="Saved Flex XML path")
    parser.add_argument(
        "--config", default=str(DEFAULT_CONFIG),
        help="Dashboard YAML config (user/account hierarchy)",
    )
    parser.add_argument("--port", type=int, default=8765, help="HTTP port")
    parser.add_argument("--trailing-days", type=int, default=TRAILING_DAYS)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(message)s")
    config = load_config(Path(args.config))
    ensure_flex_xml(Path(args.xml))
    app = _build_app(Path(args.xml), config, args.trailing_days)
    logger.info("Dashboard at http://127.0.0.1:%d/", args.port)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
