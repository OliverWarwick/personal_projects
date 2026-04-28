#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# deploy.sh — local deployment pipeline
#
# Runs lint, type checks, and tests, then syncs the venv and installs cron
# jobs. All steps must pass before the crontab is touched.
#
# Usage:
#   ./deploy.sh            # full deploy (lint + typecheck + test + cron)
#   ./deploy.sh --check    # run checks only, do not update cron
# -----------------------------------------------------------------------------
set -euo pipefail

# ---- config -----------------------------------------------------------------
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV="/opt/homebrew/bin/uv"
CRON_FILE="$PROJECT_ROOT/cron_entries.txt"
CRON_BEGIN="# ---- BEGIN rightmove_scrapper ----"
CRON_END="# ---- END rightmove_scrapper ----"
CHECK_ONLY=false

# ---- colours ----------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

# ---- helpers ----------------------------------------------------------------
step()  { echo -e "\n${BLUE}${BOLD}[$1/$STEPS] $2${NC}"; }
pass()  { echo -e "${GREEN}  ✓ $1${NC}"; }
fail()  { echo -e "${RED}  ✗ $1${NC}"; exit 1; }
warn()  { echo -e "${YELLOW}  ! $1${NC}"; }
info()  { echo -e "  $1"; }

# ---- args -------------------------------------------------------------------
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=true ;;
    *) echo "Unknown argument: $arg"; exit 1 ;;
  esac
done

if $CHECK_ONLY; then
  STEPS=4
else
  STEPS=5
fi

echo -e "\n${BOLD}========================================${NC}"
echo -e "${BOLD}  Rightmove — local deploy${NC}"
echo -e "${BOLD}  $(date '+%Y-%m-%d %H:%M:%S')${NC}"
echo -e "${BOLD}  Project: $PROJECT_ROOT${NC}"
$CHECK_ONLY && echo -e "${YELLOW}  Mode: checks only (--check)${NC}"
echo -e "${BOLD}========================================${NC}"

# ---- step 1: sync deps ------------------------------------------------------
step 1 "Syncing dependencies (uv sync)"
cd "$PROJECT_ROOT"
$UV sync --quiet
pass "Dependencies up to date"

# ---- step 2: lint -----------------------------------------------------------
step 2 "Lint (ruff check)"
if $UV run ruff check src tests; then
  pass "No lint errors"
else
  fail "Lint failed — fix errors above before deploying"
fi

# ---- step 3: type check -----------------------------------------------------
step 3 "Type check (pyright)"
if $UV run pyright src 2>&1 | tee /tmp/pyright_out.txt | grep -q "^0 errors"; then
  pass "No type errors"
else
  # Print errors and fail
  cat /tmp/pyright_out.txt
  fail "Type check failed — fix errors above before deploying"
fi

# ---- step 4: tests ----------------------------------------------------------
step 4 "Tests (pytest)"
if $UV run pytest tests -q --tb=short; then
  pass "All tests passed"
else
  fail "Tests failed — fix failures above before deploying"
fi

# ---- step 5: update crontab -------------------------------------------------
if $CHECK_ONLY; then
  echo -e "\n${YELLOW}Skipping crontab update (--check mode)${NC}"
  echo -e "\n${GREEN}${BOLD}All checks passed.${NC}\n"
  exit 0
fi

step 5 "Updating crontab"

# Create data dir for logs if it doesn't exist
mkdir -p "$PROJECT_ROOT/data"
info "data/ directory ready"

# Remove the existing rightmove_scrapper block from the crontab (if present),
# then append the current cron_entries.txt block.
CURRENT_CRON=$(crontab -l 2>/dev/null || true)

# Strip everything between (and including) the BEGIN/END markers
STRIPPED=$(echo "$CURRENT_CRON" | awk "
  /^# ---- BEGIN rightmove_scrapper ----/ { skip=1 }
  !skip { print }
  /^# ---- END rightmove_scrapper ----/   { skip=0 }
")

# Build the new crontab: preserved entries + our block
NEW_CRON=$(printf '%s\n%s\n' "$STRIPPED" "$(cat "$CRON_FILE")")

# Remove consecutive blank lines and install
echo "$NEW_CRON" | cat -s | crontab -

pass "Crontab updated"
info "Installed entries:"
crontab -l | grep -A3 "BEGIN rightmove_scrapper" | sed 's/^/    /'

echo -e "\n${GREEN}${BOLD}========================================${NC}"
echo -e "${GREEN}${BOLD}  Deploy complete.${NC}"
echo -e "${GREEN}${BOLD}========================================${NC}"
echo -e "  Poll log:    $PROJECT_ROOT/data/poll.log"
echo -e "  Notify log:  $PROJECT_ROOT/data/notify.log"
echo -e "  View cron:   crontab -l\n"
