#!/usr/bin/env bash
# One-time setup: venv, deps, camera selection, LaunchAgent install.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.focusshift/venv"
APP_DIR="$HOME/.focusshift/app"
LOG_DIR="$HOME/.focusshift/logs"
PLIST="$HOME/Library/LaunchAgents/com.focusshift.app.plist"

echo "=== FocusShift Install ==="
echo "Repo: $REPO"
echo ""

# 1. Virtual environment
if [ ! -d "$VENV" ]; then
    echo "→ Creating virtual environment..."
    python3 -m venv "$VENV"
fi

echo "→ Installing dependencies..."
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$REPO/requirements.txt"

# 2. Copy source to ~/.focusshift/app (launchd can't read ~/Desktop without FDA)
mkdir -p "$APP_DIR" "$LOG_DIR"
cp "$REPO"/__init__.py "$REPO"/main.py "$REPO"/tracker.py \
   "$REPO"/switcher.py "$REPO"/monitor_check.py "$REPO"/run.py \
   "$APP_DIR/"
echo "→ Source copied to $APP_DIR"

# 3. Camera selection (interactive, only if no saved config)
CONFIG="$HOME/.focusshift/config.json"
if [ ! -f "$CONFIG" ]; then
    echo ""
    echo "=== Camera Setup (runs once) ==="
    "$VENV/bin/python" "$APP_DIR/run.py" --select-camera
    echo ""
fi

# 4. Write LaunchAgent plist with the real paths baked in
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.focusshift.app</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV/bin/python</string>
        <string>$APP_DIR/run.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/focusshift.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/focusshift.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
PLIST_EOF

# 5. Load the agent (unload first in case of reinstall)
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "✓ FocusShift is installed and running in the background."
echo ""
echo "  ./focusshift.sh start|stop|restart|status|logs|reset-camera|uninstall"
