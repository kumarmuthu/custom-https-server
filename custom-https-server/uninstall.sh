#!/bin/bash
# Name    : uninstall.sh
# Version : 2026.01.03.01
# Author  : Muthukumar Subramanian
# OS      : Linux / macOS
# Purpose : Uninstall custom-https-server service cleanly

set -e

OS_NAME="$(uname)"

# -------------------------------
# Detect real user
# -------------------------------
REAL_USER="${SUDO_USER:-$USER}"
if [[ -z "$REAL_USER" ]]; then
    REAL_USER="$(whoami)"
fi

# -------------------------------
# Get real user's home directory
# -------------------------------
if [[ "$REAL_USER" == "root" ]]; then
    USER_HOME="/tmp"
else
    if command -v getent >/dev/null 2>&1; then
        USER_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
    else
        # macOS fallback
        USER_HOME="$(dscl . -read /Users/"$REAL_USER" NFSHomeDirectory | awk '{print $2}')"
    fi
fi

# -------------------------------
# Log paths (v2)
# -------------------------------
LOG_BASE="$USER_HOME/custom_https_server_log"
LOG_DIR="$LOG_BASE/logs"

if [[ "$OS_NAME" == "Linux" ]]; then
  echo "Stopping custom-https-server service on Linux..."
  sudo systemctl stop custom-https-server || true
  sudo systemctl disable custom-https-server || true

  echo "Killing any running Python server process..."
  sudo pkill -f "/opt/custom-https-server/custom_https_server.py" || true

  echo "Removing systemd service file..."
  sudo rm -f /etc/systemd/system/custom-https-server.service

  echo "Removing config file..."
  sudo rm -f /etc/custom-https-server.conf

  echo "Removing installed files..."
  sudo rm -rf /opt/custom-https-server

  echo "Cleaning up log files..."
  rm -rf "$LOG_BASE" || true

  echo "Reloading systemd daemon..."
  sudo systemctl daemon-reload
  systemctl reset-failed custom-https-server.service

  echo "✅ Uninstall complete on Linux."

elif [[ "$OS_NAME" == "Darwin" ]]; then
  echo "Stopping custom-https-server (com.custom_https_server.python) service on macOS..."

  PLIST="$USER_HOME/Library/LaunchAgents/com.custom_https_server.plist"
  TARGET_DIR="/usr/local/custom_https_server"
  CONFIG_PATH="/usr/local/etc/custom-https-server.conf"

  if [[ -f "$PLIST" ]]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed plist: $PLIST"
  fi

  echo "Removing installed script and config..."
  sudo rm -f "$TARGET_DIR/custom_https_server.py" || true
  sudo rm -f "$CONFIG_PATH" || true
  sudo rmdir "$TARGET_DIR" 2>/dev/null || true

  echo "Cleaning up log files..."
  rm -rf "$LOG_BASE" || true

  echo "Killing any remaining server process..."
  sudo pkill -f "/usr/local/custom_https_server/custom_https_server.py" || true

  echo "Verifying removal..."
  launchctl list | grep custom_https_server || echo "✅ Service fully removed."

  echo "✅ Uninstall complete on macOS."

else
  echo "Unsupported OS: $OS_NAME"
  exit 1
fi
