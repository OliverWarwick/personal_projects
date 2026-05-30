# Finances Dashboard — Open Issues

Tick-list of known bugs and feature requests, in priority order. Each item
should become a PR; close by ticking the box and (optionally) linking the
commit.

## Bugs

- [x] **HL GBp/GBP 100× inflation.**
- [x] **U20984787 phantom cash.**
- [ ] **U20984788 gross PnL widget.** Downstream of the truncation below —
      no closing trades in the XML to sum. Resolves once the Flex Query
      Period is widened in IBKR Account Management.
- [ ] **IBKR Flex Query history truncation.** Configure-only — set Period
      to "Year to Date" or "Last 365 Days" in IBKR Account Management →
      Flex Queries → Edit. Detector now in smoke test #14.
- [x] **FCBR / BMBL1G8 etc. log noise.**

## Features

- [x] **Return column on Closed Positions.**
- [x] **CAGR column on Open Positions.**
- [x] **CAGR column on Closed Positions.**

## Done

- [x] LITE / All-Accounts buy-marker GBP scale fix.
- [x] CAGR cells coloured green/red.
- [x] Position grids tightened (font, padding, header labels).
- [x] HL/AJB negative-cash plug (synthetic deposit on first business day).
- [x] AJ Bell closed-position Open Px (origTradePrice via
      `compute_realized_detailed`).
- [x] `_snapshot` average-cost replay + OpenPosition anchor; deployed cost
      now matches HL/AJB CSV snapshot totals exactly.
- [x] T-1 cutoff (dashboard never displays today's partial data).
- [x] HL synth: deposit `CashTransaction` rows emitted so cash doesn't go
      negative on sells.
- [x] HL synth: `proceeds`, `origTradePrice`, `fifoPnlRealized` on closing
      trades so closed positions have non-zero open price + realised PnL.
- [x] AMZN 20:1 split adjustment for HL pre-split rows.
- [x] yfinance on-disk parquet cache with TTL-gated tail refetch + persistent
      negative cache.
- [x] Sortable table columns + click-to-isolate bar chart segments.
- [x] `.L` mappings for HL UK ETFs (CTY, IUKD, FEQP, VHYG, ISPE) + LGEN +
      AMAZON / ASML.AS / NBIS / VUSA.L description-string mappings.
- [x] `.env` auto-load via python-dotenv so IBKR creds resolve at startup.
- [x] `ensure_flex_xml()` auto-refreshes `/tmp/ibkr.xml` when missing/stub.
- [x] Parquet cache returns tz-naive `DatetimeIndex` so slicing doesn't blow
      up with `tz-naive vs tz-aware` TypeError.
- [x] Timeline anchored at first event date (not `today − trailing_days`)
      so HL accounts with pre-window deposits get correct `total_deposited`,
      cash, and CAGR.
