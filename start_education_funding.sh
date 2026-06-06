#!/usr/bin/env bash
# Starts the education-funding web app and exposes it via a Cloudflare Tunnel.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV="/opt/homebrew/bin/uv"
PORT=8000
LOG_DIR="$PROJECT_ROOT/data"
mkdir -p "$LOG_DIR"

cleanup() {
  echo ""
  echo "Shutting down..."
  kill "$UVICORN_PID" "$CLOUDFLARED_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting education-funding on port $PORT..."
cd "$PROJECT_ROOT"
$UV run uvicorn src.apps.education_funding.web.app:app \
  --host 0.0.0.0 --port "$PORT" \
  >> "$LOG_DIR/education_funding.log" 2>&1 &
UVICORN_PID=$!

# Give uvicorn a moment to bind
sleep 1

echo "Starting Cloudflare Tunnel..."
cloudflared tunnel --url "http://localhost:$PORT" \
  --logfile "$LOG_DIR/cloudflared.log" 2>&1 &
CLOUDFLARED_PID=$!

echo ""
echo "Uvicorn PID: $UVICORN_PID  |  cloudflared PID: $CLOUDFLARED_PID"
echo "Waiting for tunnel URL (check output below)..."
echo ""

# Stream cloudflared output so the tunnel URL is visible
tail -f "$LOG_DIR/cloudflared.log" &
TAIL_PID=$!

wait "$UVICORN_PID"
kill "$TAIL_PID" 2>/dev/null || true
