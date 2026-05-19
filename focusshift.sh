#!/usr/bin/env bash
# Manage the FocusShift background service.
# Usage: ./focusshift.sh <command>
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.focusshift/venv"
APP_DIR="$HOME/.focusshift/app"
PLIST="$HOME/Library/LaunchAgents/com.focusshift.app.plist"
LOG_DIR="$HOME/.focusshift/logs"
LABEL="com.focusshift.app"

_require_install() {
    if [ ! -f "$PLIST" ]; then
        echo "FocusShift is not installed. Run ./install.sh first."
        exit 1
    fi
}

_is_loaded() {
    launchctl list "$LABEL" &>/dev/null
}

case "${1:-help}" in
    start)
        _require_install
        if _is_loaded; then
            echo "FocusShift is already running."
        else
            launchctl load "$PLIST"
            echo "FocusShift started."
        fi
        ;;

    stop)
        _require_install
        if _is_loaded; then
            launchctl unload "$PLIST"
            echo "FocusShift stopped. (Will restart on next login — use 'uninstall' to remove.)"
        else
            echo "FocusShift is not running."
        fi
        ;;

    restart)
        _require_install
        launchctl unload "$PLIST" 2>/dev/null || true
        sleep 0.5
        launchctl load "$PLIST"
        echo "FocusShift restarted."
        ;;

    status)
        if _is_loaded; then
            launchctl list "$LABEL"
        else
            echo "FocusShift is not running."
        fi
        ;;

    logs)
        echo "Streaming logs (Ctrl-C to stop)..."
        tail -f "$LOG_DIR/focusshift.log" "$LOG_DIR/focusshift.err" 2>/dev/null \
            || echo "No logs yet — start FocusShift first."
        ;;

    reset-camera)
        echo "=== Re-selecting camera ==="
        "$VENV/bin/python" "$APP_DIR/run.py" --select-camera
        echo ""
        if _is_loaded; then
            launchctl unload "$PLIST" 2>/dev/null || true
            sleep 0.5
            launchctl load "$PLIST"
            echo "FocusShift restarted with new camera."
        fi
        ;;

    uninstall)
        if _is_loaded; then
            launchctl unload "$PLIST" 2>/dev/null || true
        fi
        rm -f "$PLIST"
        echo "FocusShift uninstalled. Run ./install.sh to reinstall."
        ;;

    help|--help|-h|*)
        echo "Usage: ./focusshift.sh <command>"
        echo ""
        echo "Commands:"
        echo "  start          Start FocusShift"
        echo "  stop           Stop FocusShift (restarts on next login)"
        echo "  restart        Restart FocusShift"
        echo "  status         Show running status"
        echo "  logs           Stream live logs"
        echo "  reset-camera   Re-run camera selection and restart"
        echo "  uninstall      Remove the LaunchAgent"
        ;;
esac
