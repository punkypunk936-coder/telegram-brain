#!/usr/bin/env bash
set -euo pipefail

LABEL="com.manvinder.telegram-brain"
ROOT="$(cd "$(dirname "$0")" && pwd -P)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$ROOT/data/logs"
DOMAIN="gui/$(id -u)"

case "$ROOT" in
  "$HOME/Desktop/"*|"$HOME/Documents/"*|"$HOME/Downloads/"*)
    echo "Move Telegram Brain outside Desktop, Documents or Downloads before enabling autostart."
    echo "macOS blocks background launch services from those protected folders."
    exit 1
    ;;
esac

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$ROOT/run.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/dashboard.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/dashboard-error.log</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST" >/dev/null
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$DOMAIN/$LABEL"
launchctl kickstart -k "$DOMAIN/$LABEL"

echo "Telegram Brain is managed by $LABEL"
echo "Dashboard: http://127.0.0.1:8501"
