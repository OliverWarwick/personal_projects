# ibkr-sync

Pull portfolio and transaction data out of an Interactive Brokers (IBKR) account
via the **Flex Web Service** and persist it to a local SQLite database.

The Flex Web Service is IBKR's REST endpoint for downloading pre-defined "Flex
Queries" — XML reports containing trades, open positions, cash transactions,
and account summaries. It needs no running gateway or daemon: just a token, a
query ID, and HTTPS. The third-party [`ibflex`](https://pypi.org/project/ibflex/)
package handles the two-step request/poll flow and parses the XML into typed
Python objects.

---

## One-time IBKR-side setup

This is the only part that needs a human in the IBKR UI.

1. **Generate a Flex Web Service token.**
   - Log in at <https://www.interactivebrokers.com>.
   - Go to **Performance & Reports → Flex Queries**.
   - Click the gear / **Configure** icon next to **Flex Web Service**.
   - Enable the service and copy the generated token. Treat it like a password.
   - **Token activation lag**: tokens typically take ~24 hours to activate and
     expire after ~1 year of inactivity.

2. **Create two Flex Queries.** From the same Flex Queries page, click **Create**
   under **Activity Flex Query** and configure two queries — note their numeric
   IDs after saving:

   - **Activity query** — used by `sync-activity`. Tick at minimum:
     - `Trades`
     - `Cash Transactions`
     - Period: `Last N Calendar Days` (e.g. 30) or `Custom Date Range`
     - Format: `XML`

   - **Portfolio query** — used by `sync-portfolio`. Tick at minimum:
     - `Open Positions`
     - `Equity Summary in Base`
     - Period: `Last Business Day`
     - Format: `XML`

   You can collapse these into one query if you prefer; the CLI also accepts
   `--query-id` to override at the command line.

---

## Setting up on a new machine

Prerequisites:
- Python 3.14+
- [`uv`](https://docs.astral.sh/uv/) (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- `git`

Steps:

```bash
# 1. Clone the repo
git clone <repo-url> personal_projects
cd personal_projects

# 2. Install all dependencies (creates .venv automatically)
uv sync --all-extras

# 3. Configure credentials. EITHER export environment variables...
export IBKR_FLEX_TOKEN='...'                    # from step 1 above
export IBKR_FLEX_ACTIVITY_QUERY='123456'        # numeric Flex Query ID
export IBKR_FLEX_PORTFOLIO_QUERY='123457'       # numeric Flex Query ID
# Optional: pick a non-default DB location
export IBKR_DATABASE_URL='sqlite:////absolute/path/to/ibkr_data.db'

# ...OR copy the YAML template and edit it
cp src/personal_project/config/ibkr_sync.yaml ~/ibkr_sync.local.yaml
$EDITOR ~/ibkr_sync.local.yaml
# then pass --config ~/ibkr_sync.local.yaml to the CLI
```

Environment variables take precedence over the YAML file, so it is fine to
keep non-secret defaults in YAML and supply the token via the environment.

A reasonable shell-rc snippet for persistence:

```bash
# ~/.zshrc or ~/.bashrc
export IBKR_FLEX_TOKEN="$(security find-generic-password -s ibkr-flex -w)"  # macOS keychain
export IBKR_FLEX_ACTIVITY_QUERY='123456'
export IBKR_FLEX_PORTFOLIO_QUERY='123457'
```

---

## Running

The console script is registered as `ibkr-sync`:

```bash
# Sync trades + cash transactions
uv run ibkr-sync sync-activity

# Sync open positions + NAV/cash snapshot
uv run ibkr-sync sync-portfolio

# Run both
uv run ibkr-sync sync-all

# Save the raw XML for a query without touching the DB (handy for debugging)
uv run ibkr-sync download --query-id 123456 --output /tmp/ibkr.xml

# Use a YAML config instead of env vars
uv run ibkr-sync --config ~/ibkr_sync.local.yaml sync-all

# Verbose
uv run ibkr-sync --debug sync-activity
```

---

## Where the data lives

By default the SQLite file is written to `./ibkr_data.db` in the working
directory. Tables created:

| Table                     | Key                  | Contents                                          |
| ------------------------- | -------------------- | ------------------------------------------------- |
| `ibkr_trades`             | `trade_id`           | One row per executed trade                        |
| `ibkr_cash_transactions`  | `transaction_id`     | Dividends, taxes, deposits, withdrawals, fees     |
| `ibkr_positions`          | autoincrement        | One row per open position per snapshot date       |
| `ibkr_account_snapshots`  | autoincrement        | One row per `EquitySummaryInBase` entry per run   |

Quick inspection:

```bash
sqlite3 ibkr_data.db '.tables'
sqlite3 ibkr_data.db 'SELECT trade_date, symbol, buy_sell, quantity, price FROM ibkr_trades ORDER BY trade_date DESC LIMIT 10;'
```

---

## Troubleshooting

- **`Token has expired or is not yet activated`** — newly generated tokens
  take ~24 hours to activate. Wait, then retry.
- **`No statements available`** — the Flex Query has no rows in its configured
  period (e.g. an Activity query for "yesterday" on a non-trading day). Widen
  the period in IBKR Account Management.
- **Empty tables after a successful run** — the Flex Query is missing the
  required sections. Re-edit it and tick `Trades`, `Cash Transactions`,
  `Open Positions`, `Equity Summary in Base` as appropriate.
- **`ibflex.client.ResponseCodeError`** — IBKR returned an error. The token
  may be wrong, the query ID may be wrong, or the account does not have
  permission to run the query. Re-copy both values from Account Management.
