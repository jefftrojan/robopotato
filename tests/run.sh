#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ── Check ANTHROPIC_API_KEY ───────────────────────────────────────────────────
if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "Error: ANTHROPIC_API_KEY is not set."
  echo "  export ANTHROPIC_API_KEY=sk-ant-..."
  exit 1
fi

# ── Start robopotato in background if not already running ─────────────────────
if ! curl -sf http://127.0.0.1:7878/health > /dev/null 2>&1; then
  echo "Starting robopotato..."
  cd "$PROJECT_DIR"
  ROBOPOTATO_SECRET=test-secret-do-not-use-in-prod cargo build --release -q
  ROBOPOTATO_SECRET=test-secret-do-not-use-in-prod ./target/release/robopotato &
  ROBOPOTATO_PID=$!
  echo "Robopotato PID: $ROBOPOTATO_PID"
  sleep 1

  # Wait for it to be ready
  for i in {1..10}; do
    if curl -sf http://127.0.0.1:7878/health > /dev/null 2>&1; then
      echo "Robopotato ready ✓"
      break
    fi
    sleep 0.5
  done
else
  echo "Robopotato already running ✓"
  ROBOPOTATO_PID=""
fi

# ── Run comparison ─────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"
python3 compare.py

# ── Stop robopotato if we started it ──────────────────────────────────────────
if [ -n "$ROBOPOTATO_PID" ]; then
  echo "Stopping robopotato (PID $ROBOPOTATO_PID)..."
  kill "$ROBOPOTATO_PID" 2>/dev/null || true
fi
