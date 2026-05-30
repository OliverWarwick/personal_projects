# Finances Dashboard

FastAPI app that renders a unified view of brokerage accounts across **IBKR**,
**Hargreaves Lansdown**, and **AJ Bell**. One sidebar, one URL per account, the
same chart and tables everywhere.

## What it shows

Per account:

- **Open positions** table — quantity, cost basis, mark, unrealised P&L, CAGR.
- **Closed positions** table — FIFO-matched realised P&L per lot.
- **Timeline chart** — stacked bars of cash / deployed cost / unrealised /
  realised over the trailing window, with a grey step line for cumulative
  deposits.
- **Per-symbol drill-in** — price series with trade markers.

There is also an **All accounts** aggregate view that sums everything in the
base currency.

## Architecture

The dashboard speaks one internal data model: an **IBKR Flex XML statement**.
IBKR statements are used as-is; HL and AJ Bell are bridged by synthesising
Flex-shaped `FlexStatement` elements from their CSV exports.

```
clients/ibkr/      ── Flex Query download         ┐
clients/hl/        ── HL CSV parse + price scrape ├─► dict[account_id, FlexStatement]
clients/ajbell/    ── AJ Bell CSV parse           ┘
                                                       │
                                          src/apps/finances_dashboard/
                                          ├── hl_provider.py     (HL → Flex)
                                          ├── ajbell_provider.py (AJB → Flex)
                                          ├── run.py             (FastAPI + render)
                                          ├── _realized.py       (FIFO matcher)
                                          ├── timeline_cache.py  (disk cache)
                                          └── config.py          (YAML users/brokers/accounts)
```

The renderer (`run.py`) only ever sees `FlexStatement` XML. It does not know
which broker an account came from.

### Accounting model

Closed-loop, per account:

```
total_value = cash + deployed_cost + unrealised_pnl
            (+ realised_pnl shown separately, already folded into cash)
```

`deployed_cost` and `unrealised_pnl` are derived by **FIFO replay** of every
`Trade` row up to the snapshot date:

- Each BUY pushes a `(qty, unit_cost_in_base_ccy)` lot.
- Each SELL consumes lots from the front; the matched cost basis flows out of
  `deployed`, the proceeds-vs-cost delta becomes `realised`.

FIFO (not weighted-average) is used because that matches UK broker / HMRC CGT
convention, so the replay's residual cost basis agrees with the broker's
authoritative figure without needing a final-day anchor.

### Marks

For unrealised P&L the dashboard needs a daily close per holding:

1. **yfinance** via `IBKR_TO_YF` mapping (`{ibkr_symbol: (yf_symbol, currency)}`).
2. **Fallback: `OpenPosition.markPrice`** from the Flex statement when yfinance
   has no series (typical for HL OEICs, gilts, AJB tickers).
3. **Final-day override**: on the most recent snapshot date, the broker
   `markPrice` is preferred over yfinance even when yfinance has data, since
   the broker's bid is authoritative for the headline total.

### HL / AJ Bell bridge

`hl_provider.synthesize_flex_statement()` and `ajbell_provider.synthesize_flex_statement()`
build a `FlexStatement` with:

- One `OpenPosition` per holding (carrying `markPrice` so the fallback works).
- One `Trade` per BUY/SELL/REDEMPTION ledger row.
- One `CashTransaction` per cash movement (deposit, interest, fee).
- One synthetic **opening row** dated one business day before the earliest
  ledger event, sized so the replay's closing cash matches the snapshot's
  reported cash balance.

The opening row exists because CSV exports only cover a finite window — without
it, the FIFO replay starts from zero cash with no holdings, even though the
account has years of pre-window history. The opening row is labelled
`"Deposits/Withdrawals"` so it counts toward the **Total Invested** metric as
one visible contribution rather than as a synthetic plug.

## Configuration

`src/config/finances_dashboard.yaml`:

```yaml
users:
  - name: Oliver
    brokers:
      - name: IBKR
        accounts:
          - { id: U20984788, label: GIA }
      - name: Hargreaves Lansdown
        accounts:
          - { id: HL-ISA, label: ISA }
          - { id: HL-SIPP, label: SIPP }
          - { id: HL-SHARES, label: Fund & Share }
      - name: AJ Bell
        accounts:
          - { id: AJB-ABBXDDL, label: ISA }
```

Account ids:

- **IBKR**: real `accountId` from the Flex XML.
- **HL**: `HL-ISA`, `HL-SIPP`, `HL-SHARES` (the three account kinds).
- **AJ Bell**: `AJB-<account-number>` prefix.

Data directories are picked up via env vars:

- `IBKR_FLEX_TOKEN` + `IBKR_FLEX_QUERY_ID` — auto-download Flex XML.
- `HL_DATA_DIR` — directory of HL CSV exports.
- `AJBELL_DATA_DIR` — directory of AJ Bell CSV exports.

## Running

```bash
uv run python -m src.apps.finances_dashboard.run
# → http://127.0.0.1:8765
```

Timeline computation is cached to disk in `timeline_cache/` keyed by a hash of
the trade rows; cache invalidates automatically when the underlying CSV/XML
changes.

## Gotchas

**HL gilt maturities.** HL exports a `RDP CR` ("redemption credit") row when a
gilt matures: cash arrives, the position disappears from the snapshot, but
there is no `S`-prefix sell row. A naive FIFO replay never consumes those
units and leaves a phantom position worth `mark × qty`. Handled by classifying
`RDP CR` as `REDEMPTION` in `clients/hl/parser.py` and emitting a synthetic
SELL in `hl_provider.py` after the normal trade loop.

**yfinance vs. broker bid drift.** yfinance closes can differ from
HL / AJ Bell bid quotes by tens to thousands of pounds on a large account.
Mitigated by the final-day `OpenPosition.markPrice` override. Historical
snapshots still use yfinance — broker marks are only point-in-time, so the
chart trajectory would be wrong if we substituted them everywhere.

**Symbols yfinance doesn't cover.** HL OEICs (e.g. `BYX5P48`, `BMBL1G8`) and
some IBKR tickers (`HY9H` on XETRA, US tickers needing currency hints) require
explicit entries in `IBKR_TO_YF` or fall through to `OpenPosition.markPrice`.
Missing entries show up as `"no price data for X; using OpenPosition.markPrice"`
warnings — add the mapping if you want a historical chart for that symbol.

**Currency on US tickers booked through IBKR.** Without an `IBKR_TO_YF` entry,
yfinance returns USD closes that get treated as the account's base currency
(GBP). Always add a `(symbol, "USD")` or `(symbol, "EUR")` mapping for
non-base-currency symbols.

**Weekend ledger events.** Some brokers book cash transactions on Saturdays
(e.g. interest accruals). The dashboard iterates `pd.bdate_range`, so weekend
events would be summed into totals but never appear on the timeline.
`_inject_opening_balances` filters Sat/Sun events when computing the opening
cash plug to keep the arithmetic consistent with the chart.

**CSV preamble encoding.** HL exports are `cp1252` with a free-form preamble
of `label,value` rows before the actual header. Both the account-summary and
portfolio-summary parsers skip until they hit the known header marker
(`Code` and `Trade date` respectively).

**CAGR needs a buy date.** When the export window does not contain the
original BUY for an open position, CAGR falls back to `openDateTime` /
`holdingPeriodDateForGains` on the `OpenPosition` element. HL/AJB synthetic
statements don't set these, so legacy holdings on those accounts show no CAGR.

**Don't amend the opening row.** The opening row is sized against the
**current** snapshot's cash balance. If you re-export the CSVs with a newer
snapshot, the opening row will be recomputed; do not persist or hand-tweak it.
