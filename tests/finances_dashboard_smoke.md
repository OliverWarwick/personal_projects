# Finances Dashboard — Smoke Tests

A manual checklist captured from recent debugging. Each item is a concrete
command + expected outcome. Convert to automated tests later under
`tests/apps/finances_dashboard/`.

## 0. Setup

```bash
# .env at the project root must contain IBKR creds:
#   IBKR_FLEX_TOKEN=...
#   IBKR_FLEX_ACTIVITY_QUERY=...
#   IBKR_FLEX_PORTFOLIO_QUERY=...
# IBKRSyncConfig auto-loads this via python-dotenv on import.
test -s .env && grep -q IBKR_FLEX_TOKEN .env && echo "env ok"
```

## 1. IBKR Flex XML auto-refresh

```bash
# Force an empty stub then verify the dashboard re-downloads on startup.
echo '<?xml version="1.0"?><FlexQueryResponse/>' > /tmp/ibkr.xml
uv run python -c "
from pathlib import Path
from personal_project.apps.finances_dashboard.run import ensure_flex_xml
ensure_flex_xml(Path('/tmp/ibkr.xml'))
print('size:', Path('/tmp/ibkr.xml').stat().st_size)
"
# Expect: size > 100_000 (real Flex XML, not the stub).
```

## 2. Manual `ibkr-sync download`

```bash
set -a; source .env; set +a
uv run ibkr-sync download --query-id "$IBKR_FLEX_ACTIVITY_QUERY" --output /tmp/ibkr.xml
# Expect: "Wrote NNNNNN bytes to /tmp/ibkr.xml" with NNNNNN ~ 300_000+.
```

## 3. Statements load for all three brokers

```bash
uv run python -c "
import xml.etree.ElementTree as ET
from personal_project.apps.finances_dashboard.warm_cache import _statements
from pathlib import Path
stmts = _statements(Path('/tmp/ibkr.xml'))
print(len(stmts), 'statements')
for aid, _, base in stmts:
    print(' ', aid, base)
"
# Expect: U20984787, U20984788 (IBKR), HL-ISA / HL-SIPP / HL-SHARES, AJB-*.
```

## 4. Dashboard server boots + binds 8765

```bash
kill $(lsof -ti:8765) 2>/dev/null; sleep 1
uv run finances-dashboard &
sleep 5
curl -sf http://127.0.0.1:8765/ > /dev/null && echo "dashboard up"
```

## 5. IBKR accounts return non-empty portfolio JSON

```bash
for aid in U20984787 U20984788; do
  n=$(curl -s "http://127.0.0.1:8765/api/$aid/portfolio" \
        | python3 -c "import sys,json; print(len(json.load(sys.stdin)['labels']))")
  echo "$aid: $n labels"
done
# Expect: both accounts > 200 labels (about a year of business days).
```

## 6. HL accounts render

```bash
for aid in HL-ISA HL-SIPP HL-SHARES; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8765/account/$aid")
  echo "$aid: HTTP $code"
done
# Expect: all 200.
```

## 7. T-1 cutoff — no future dates in timeline

```bash
uv run python -c "
import json, urllib.request, datetime
d = json.loads(urllib.request.urlopen('http://127.0.0.1:8765/api/U20984788/portfolio').read())
last = datetime.date.fromisoformat(d['labels'][-1])
today = datetime.date.today()
print('last label:', last, '  today:', today, '  delta:', (today - last).days)
assert last < today, 'timeline includes today or future'
"
# Expect: delta >= 1.
```

## 8. AMZN 20:1 split applied to HL pre-split rows

```bash
uv run python -c "
from personal_project.apps.finances_dashboard.hl_provider import _apply_split
from datetime import date
from decimal import Decimal
# 1 share at £2400 booked pre-split → 20 shares at £120 post-split.
q, p = _apply_split('AMAZON-COM-INC-COM-STK-U', date(2021, 1, 1), Decimal(1), Decimal('2400'))
assert q == Decimal('20') and p == Decimal('120'), (q, p)
# Post-split trade is left alone.
q, p = _apply_split('AMAZON-COM-INC-COM-STK-U', date(2023, 1, 1), Decimal(20), Decimal('120'))
assert q == Decimal('20') and p == Decimal('120'), (q, p)
print('ok')
"
```

## 9. Parquet cache index is tz-naive (no comparison errors)

```bash
# Tail the dashboard log while loading a few accounts in the UI.
# Expect: no occurrences of 'Cannot compare tz-naive and tz-aware'.
tail -200 /tmp/finances_dashboard.log 2>/dev/null \
  | grep -c 'tz-naive and tz-aware' \
  | tee /dev/stderr | grep -q '^0$' && echo "ok"
```

## 10. `finances-cache-warm` runs end-to-end

```bash
uv run finances-cache-warm --bust-timeline 2>&1 | tail -20
# Expect: a per-symbol status table and a "N/M symbols cached" summary;
# rate-limited symbols should report a clear error (not truncated).
```

## 11. Non-margin accounts never go negative on cash

```bash
uv run python -c "
import logging; logging.disable(logging.CRITICAL)
from personal_project.apps.finances_dashboard.run import _build_timeline
from personal_project.apps.finances_dashboard.hl_provider import load_hl_statements
from personal_project.apps.finances_dashboard.ajbell_provider import load_ajbell_statements
fails = []
for aid, stmt in {**load_hl_statements(), **load_ajbell_statements()}.items():
    tl = _build_timeline(stmt, 'GBP', 30)
    if not tl: continue
    mn = min(float(t.cash) for t in tl)
    # Allow a £10 rounding tolerance (fees on days with no prior buffer).
    if mn < -10:
        fails.append(f'{aid}: min_cash={mn:.2f}')
print('FAIL' if fails else 'ok')
for f in fails: print(' ', f)
"
# Expect: 'ok'. HL ISA/SIPP/SHARES and AJ Bell can't take leverage; any
# negative trace means the synthetic deposit plug in hl_provider/
# ajbell_provider didn't engage (e.g. plug-date landed off a business day).
```

## 12. Dashboard deployed cost matches HL / AJ Bell snapshot CSVs

```bash
uv run python -c "
import logging; logging.disable(logging.CRITICAL)
from personal_project.apps.finances_dashboard.run import _build_timeline
from personal_project.apps.finances_dashboard.hl_provider import load_hl_statements
from personal_project.apps.finances_dashboard.ajbell_provider import load_ajbell_statements
from personal_project.clients.hl.loader import discover_accounts as hl_disc
from personal_project.clients.ajbell.loader import discover_accounts as ajb_disc
csv = {f'HL-{a.kind.value}': float(sum(h.cost_gbp for h in a.summary.holdings)) for a in hl_disc()}
csv.update({f'AJB-{a.portfolio.account_id}': float(sum(h.cost_gbp for h in a.portfolio.holdings)) for a in ajb_disc()})
fails = []
for aid, stmt in {**load_hl_statements(), **load_ajbell_statements()}.items():
    tl = _build_timeline(stmt, 'GBP', 30)
    if not tl or aid not in csv: continue
    diff = float(tl[-1].deployed) - csv[aid]
    if abs(diff) > 50:  # £50 tolerance for rounding
        fails.append(f'{aid}: dash_dep={float(tl[-1].deployed):,.0f} csv_cost={csv[aid]:,.0f} diff={diff:+,.0f}')
print('FAIL' if fails else 'ok')
for f in fails: print(' ', f)
"
# Expect: 'ok'. Any drift means _snapshot's cost-anchor against
# OpenPosition rows isn't engaging on the latest snapshot — usually a
# qty/costBasisPrice unit mismatch in hl_provider / ajbell_provider.
```

## 13. AJ Bell Closed Positions have non-zero Open Px

```bash
uv run python -c "
import logging; logging.disable(logging.CRITICAL)
from personal_project.apps.finances_dashboard.ajbell_provider import load_ajbell_statements
fails = []
for aid, stmt in load_ajbell_statements().items():
    for t in stmt.iter('Trade'):
        if t.get('openCloseIndicator') != 'C': continue
        if not float(t.get('origTradePrice') or 0):
            fails.append(f'{aid}/{t.get(\"symbol\")} on {t.get(\"tradeDate\")}: origTradePrice=0')
print('FAIL' if fails else 'ok')
for f in fails[:5]: print(' ', f)
"
# Expect: 'ok'. Means compute_realized_detailed surfaced the running-avg
# cost as origTradePrice on every SELL row.
```

## 14. IBKR Flex Query truncation detector

```bash
uv run python -c "
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
tree = ET.parse(Path('/tmp/ibkr.xml'))
warns = []
for stmt in tree.iter('FlexStatement'):
    aid = stmt.get('accountId') or ''
    trades = [t for t in stmt.iter('Trade') if t.get('levelOfDetail') == 'EXECUTION']
    if not trades:
        continue
    dates = sorted((t.get('tradeDate') or '')[:8] for t in trades)
    span_days = (date.fromisoformat(f'{dates[-1][:4]}-{dates[-1][4:6]}-{dates[-1][6:]}')
                 - date.fromisoformat(f'{dates[0][:4]}-{dates[0][4:6]}-{dates[0][6:]}')).days
    closes = [t for t in trades if (t.get('openCloseIndicator') or '').upper().startswith('C') or float(t.get('fifoPnlRealized') or 0)]
    if span_days < 90 or not closes:
        warns.append(f'{aid}: {len(trades)} trades, span={span_days}d, closes={len(closes)} -> widen Flex Query Period in IBKR Account Management')
print('ok' if not warns else 'TRUNCATED')
for w in warns: print(' ', w)
"
# Expect: 'ok' for accounts with sufficient history. A TRUNCATED warning
# is config-only — no code fix; reconfigure the Flex Query's Period
# field to 'Year to Date' or 'Last 365 Days'.
```

## Known open issues (do NOT yet pass)

- **U20984787 phantom cash.** Forex `Trade` rows (symbol `GBP.USD` etc.) are
  filtered out by the `"." in sym` check in `run.py:_events`, so currency
  conversions before USD stock buys aren't deducted from GBP cash. Cash for
  that account currently shows ~£29k when it should be near zero.
- **U20984788 gross PnL widget.** Realised PnL from the Flex `Trade` rows
  is summing to 0 for this account; widget appears broken. Likely the
  `fifoPnlRealized` attribute is empty on the rows the dashboard sums —
  needs the same realised-PnL path used by the HL synth provider.
