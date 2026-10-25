#!/bin/bash
# Install a daily launchd job to run the P&L report on macOS.
#
# Usage:
#   ./scripts/install_daily_report_schedule.sh          # default 9:00 UTC
#   ./scripts/install_daily_report_schedule.sh 14       # 14:00 UTC
#
# Uninstall:
#   launchctl unload ~/Library/LaunchAgents/com.maxthetrader.daily-pnl.plist

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOUR_UTC="${1:-9}"
LABEL="com.maxthetrader.daily-pnl"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python3"
REPORT_SCRIPT="$PROJECT_DIR/scripts/run_daily_pnl_report.py"
LOG_DIR="$PROJECT_DIR/logs/reports"

if [ ! -f "$VENV_PYTHON" ]; then
  echo "venv not found at $VENV_PYTHON — create it first."
  exit 1
fi

mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${VENV_PYTHON}</string>
    <string>${REPORT_SCRIPT}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJECT_DIR}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>${HOUR_UTC}</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/launchd_stdout.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/launchd_stderr.log</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
EOF

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "Installed daily P&L report schedule:"
echo "  Plist:  $PLIST_PATH"
echo "  Runs:   every day at ${HOUR_UTC}:00 UTC"
echo "  Logs:   $LOG_DIR"
echo ""
echo "Test now:  $VENV_PYTHON $REPORT_SCRIPT"
echo "Uninstall: launchctl unload $PLIST_PATH"
