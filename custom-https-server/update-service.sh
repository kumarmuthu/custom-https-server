#!/bin/bash
# Name    : update-service.sh
# Version : 2026.01.03.01
# Author  : Muthukumar Subramanian
# OS      : Linux / macOS
# Purpose : Update and restart custom-https-server systemd service or launchd agent from config file,
#           ensuring the service port is safely released before restart

set -e

# Detect OS
OS=$(uname)

SERVICE_NAME="custom-https-server"
PYTHON_BIN="$(which python3)"
MAX_WAIT_TIME=30  # seconds to wait for port to free up

# ----------------------------
# Config path (OS-specific)
# ----------------------------
if [[ "$OS" == "Darwin" ]]; then
  CONFIG_FILE="/usr/local/etc/custom-https-server.conf"
  SERVER_SCRIPT="/usr/local/custom_https_server/custom_https_server.py"
elif [[ "$OS" == "Linux" ]]; then
    CONFIG_FILE="/etc/custom-https-server.conf"
    SERVER_SCRIPT="/opt/custom_https_server/custom_https_server.py"
else
    echo "❌ Unsupported OS: $OS"
    exit 1
fi

# ----------------------------
# Validate config
# ----------------------------
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "❌ Config file not found at $CONFIG_FILE"
    exit 1
fi

# Load config values
source "$CONFIG_FILE"

# Set defaults if missing
SERVE_PATH="${SERVE_PATH:-/usr/local/custom_https_server}"
SERVE_PORT="${SERVE_PORT:-8080}"
MODE="${MODE:-read}"
AUTH_USERNAME="${AUTH_USERNAME:-admin}"
AUTH_PASSWORD="${AUTH_PASSWORD:-password}"


if [[ "$OS" == "Darwin" ]]; then
    echo "🍎 macOS detected"
    PLIST_FILE="$HOME/Library/LaunchAgents/com.custom_https_server.plist"

    if [[ ! -f "$PLIST_FILE" ]]; then
        echo "❌ Plist not found at $PLIST_FILE"
        exit 1
    fi

    echo "Updating plist $PLIST_FILE"

    # Update plist arguments
    # Map of argument indices in plist: adjust based on your plist structure
    # 3 = path, 5 = port, 7 = mode, 9 = username, 11 = password
    ARG_INDICES=(3 5 7 9 11)
    ARG_VALUES=("$SERVE_PATH" "$SERVE_PORT" "$MODE" "$AUTH_USERNAME" "$AUTH_PASSWORD")
    for i in ${!ARG_INDICES[@]}; do
        index=${ARG_INDICES[$i]}
        value=${ARG_VALUES[$i]}
        /usr/libexec/PlistBuddy -c "Set :ProgramArguments:$index $value" "$PLIST_FILE" 2>/dev/null || \
        /usr/libexec/PlistBuddy -c "Add :ProgramArguments:$index string $value" "$PLIST_FILE"
    done

    # Determine original user for LaunchAgents
    if [[ -n "$SUDO_USER" ]]; then
        ORIGINAL_USER="$SUDO_USER"
    else
        ORIGINAL_USER=$(stat -f "%Su" "$HOME")
    fi
    ORIGINAL_UID=$(id -u "$ORIGINAL_USER")

    echo "⚠️ Stopping service..."
    # Disable KeepAlive to prevent auto-restart during shutdown
    /usr/libexec/PlistBuddy -c "Set :KeepAlive false" "$PLIST_FILE" 2>/dev/null || true

    # Unload plist first
    sudo -u "$ORIGINAL_USER" launchctl bootout gui/$ORIGINAL_UID "$PLIST_FILE" 2>/dev/null || true

    # Kill any lingering processes
    PIDS=$(pgrep -f "$SERVER_SCRIPT" | tr '\n' ' ')
    if [[ -n "$PIDS" ]]; then
        echo "⚠️ Killing server process(es): $PIDS"
        kill -9 $PIDS 2>/dev/null || true
    fi

    # Wait for port to be released
    echo "⏳ Waiting for port $SERVE_PORT to be released..."
    elapsed=0
    while lsof -i :"$SERVE_PORT" -t >/dev/null 2>&1; do
        if [[ $elapsed -ge $MAX_WAIT_TIME ]]; then
            echo "❌ Port $SERVE_PORT still in use after ${MAX_WAIT_TIME}s. Force killing..."
            PIDS=$(lsof -i :"$SERVE_PORT" -t | tr '\n' ' ')
            if [[ -n "$PIDS" ]]; then
                kill -9 $PIDS 2>/dev/null || true
            fi
            sleep 2
            break
        fi
        sleep 1
        ((elapsed++))
    done

    echo "✅ Port $SERVE_PORT is now free"
    sleep 2  # Extra safety delay

    # Re-enable KeepAlive before reloading
    /usr/libexec/PlistBuddy -c "Set :KeepAlive true" "$PLIST_FILE" 2>/dev/null || true

    # Now reload plist
    echo "ℹ️ Reloading plist as user $ORIGINAL_USER (UID $ORIGINAL_UID)"
    sudo -u "$ORIGINAL_USER" launchctl bootstrap gui/$ORIGINAL_UID "$PLIST_FILE"

    echo "✅ Plist reloaded"
    echo "✅ Service updated and restarted successfully!"

elif [[ "$OS" == "Linux" ]]; then
    echo "🐧 Linux detected"
    SYSTEMD_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

    if [[ ! -f "$SYSTEMD_FILE" ]]; then
        echo "❌ Systemd service not found at $SYSTEMD_FILE"
        exit 1
    fi

    echo "Updating systemd service $SYSTEMD_FILE"
    sed -i.bak -E "s|^ExecStart=.*|ExecStart=$PYTHON_BIN $SERVER_SCRIPT --path $SERVE_PATH --port $SERVE_PORT --mode $MODE --user $AUTH_USERNAME --pass $AUTH_PASSWORD|" "$SYSTEMD_FILE"

    # Stop service first
    echo "⚠️ Stopping service..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true

    # Wait for port to be released
    echo "⏳ Waiting for port $SERVE_PORT to be released..."
    elapsed=0
    while lsof -i :"$SERVE_PORT" -t >/dev/null 2>&1; do
        if [[ $elapsed -ge $MAX_WAIT_TIME ]]; then
            echo "❌ Port $SERVE_PORT still in use after ${MAX_WAIT_TIME}s. Force killing..."
            PIDS=$(lsof -i :"$SERVE_PORT" -t | tr '\n' ' ')
            if [[ -n "$PIDS" ]]; then
                kill -9 $PIDS 2>/dev/null || true
            fi
            sleep 2
            break
        fi
        sleep 1
        ((elapsed++))
    done

    echo "✅ Port $SERVE_PORT is now free"
    sleep 1

    # Reload and restart
    systemctl daemon-reload
    systemctl restart "$SERVICE_NAME"
    echo "✅ systemd service reloaded and restarted"
    echo "✅ Service updated successfully!"
else
    echo "❌ Unsupported OS: $OS"
    exit 1
fi