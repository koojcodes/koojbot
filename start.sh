#!/bin/bash
# MaxTheTrader — single launch script
# Usage: ./start.sh [paper|live]
#
# Starts everything in one command:
#   - Trading engine (Kalshi, paper mode by default)
#   - Telegram bot
#   - REST API (port 8000)
#   - Day trader pipeline (BTC 15-min)
#   - Next.js dashboard (port 3000)

MODE=${1:-paper}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/logs"

# Keep the stack alive if the launching shell exits (e.g. backgrounded start).
# INT/TERM still shut down cleanly via the cleanup trap below.
trap '' HUP

port_in_use() {
    lsof -ti:"$1" >/dev/null 2>&1
}

# ── Locate npm via nvm (nvm doesn't load in non-interactive shells) ────────────
NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
NPM=""

# Try nvm node versions directory directly
if [ -d "$NVM_DIR/versions/node" ]; then
    # Use the default alias if set, otherwise pick the newest version
    DEFAULT_ALIAS="$NVM_DIR/alias/default"
    if [ -f "$DEFAULT_ALIAS" ]; then
        NVM_DEFAULT=$(cat "$DEFAULT_ALIAS")
        # Resolve lts/* or similar aliases to actual version
        if [[ "$NVM_DEFAULT" == lts/* || "$NVM_DEFAULT" == node ]]; then
            NVM_VERSION=$(ls "$NVM_DIR/versions/node" | sort -V | tail -1)
        else
            NVM_VERSION="$NVM_DEFAULT"
        fi
    else
        NVM_VERSION=$(ls "$NVM_DIR/versions/node" | sort -V | tail -1)
    fi
    CANDIDATE="$NVM_DIR/versions/node/$NVM_VERSION/bin/npm"
    if [ -f "$CANDIDATE" ]; then
        NPM="$CANDIDATE"
        NODE_BIN_DIR="$NVM_DIR/versions/node/$NVM_VERSION/bin"
    fi
fi

# Fallback: check common install locations
if [ -z "$NPM" ]; then
    for candidate in /usr/local/bin/npm /opt/homebrew/bin/npm; do
        if [ -f "$candidate" ]; then
            NPM="$candidate"
            NODE_BIN_DIR="$(dirname "$candidate")"
            break
        fi
    done
fi

if [ -z "$NPM" ]; then
    echo "⚠️  npm not found — dashboard will not start."
    echo "   Install Node.js from https://nodejs.org or via nvm."
    DASHBOARD_AVAILABLE=false
else
    DASHBOARD_AVAILABLE=true
    export PATH="$NODE_BIN_DIR:$PATH"
fi

# ── Banner ─────────────────────────────────────────────────────────────────────
echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║       MaxTheTrader  —  Starting      ║"
echo "  ╚══════════════════════════════════════╝"
echo ""
echo "  Mode:      $MODE"
echo "  API:       http://localhost:8000"
if [ "$DASHBOARD_AVAILABLE" = true ]; then
echo "  Dashboard: http://localhost:3000"
echo "  Live Logs: http://localhost:3000/logs"
fi
echo ""

cd "$SCRIPT_DIR"

# ── Start dashboard in background ─────────────────────────────────────────────
if [ "$DASHBOARD_AVAILABLE" = true ]; then
    echo "▶ Starting dashboard..."
    cd "$SCRIPT_DIR/dashboard"
    "$NPM" run dev > "$SCRIPT_DIR/logs/dashboard.log" 2>&1 &
    DASHBOARD_PID=$!
    cd "$SCRIPT_DIR"
    echo "  Dashboard PID: $DASHBOARD_PID (logs → logs/dashboard.log)"
    echo ""
fi

# ── Trap Ctrl-C to kill dashboard too ─────────────────────────────────────────
cleanup() {
    echo ""
    echo "Shutting down..."
    if [ -n "$DASHBOARD_PID" ]; then
        kill "$DASHBOARD_PID" 2>/dev/null
    fi
    exit 0
}
trap cleanup INT TERM

# ── Start bot (foreground — all output goes to terminal + log file) ────────────
echo "▶ Starting bot engine..."
echo ""

# Guard: refuse to start if a bot.main process is already running.
# Uses "bot.main" as the signal — reliable on all platforms because
# macOS Python may run as "Python" (capital P), not "python3".
if pgrep -f "bot.main" > /dev/null 2>&1; then
    echo "⚠️  A bot.main process is already running (PID: $(pgrep -f 'bot.main'))."
    echo "   Kill it first:  kill \$(pgrep -f 'bot.main')"
    echo "   Then re-run:    ./start.sh"
    exit 1
fi

if port_in_use 8000; then
    echo "⚠️  Port 8000 is already in use."
    echo "   Free it first: kill -9 \$(lsof -ti:8000)"
    exit 1
fi

if [ "$DASHBOARD_AVAILABLE" = true ] && port_in_use 3000; then
    echo "⚠️  Port 3000 is already in use."
    echo "   Free it first: kill -9 \$(lsof -ti:3000)"
    exit 1
fi

source "$SCRIPT_DIR/venv/bin/activate" 2>/dev/null || true

python3 -m bot.main \
    --exchange kalshi \
    --mode "$MODE" \
    --telegram \
    --api \
    --day-trader
