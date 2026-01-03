#!/bin/bash
# Name    : install.sh
# Version : 2026.01.03.01
# Author  : Muthukumar Subramanian
# OS      : Linux / macOS
# Purpose : Install and configure custom-https-server as a systemd service or launchd agent

set -e

# -------------------------------
# Defaults
# -------------------------------
DEFAULT_TARGET_DIR="/usr/local/custom_https_server"
DEFAULT_CONFIG_PATH="/usr/local/etc/custom-https-server.conf"

DEFAULT_PATH=""
DEFAULT_PORT=""
DEFAULT_MODE=""
DEFAULT_USERNAME=""
DEFAULT_PASSWORD=""
FINAL_PYTHON=""
USE_VENV="false"

# -------------------------------
# Detect OS
# -------------------------------
OS_NAME="$(uname)"

# -------------------------------
# Parse CLI args (macOS/Linux)
# -------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    -path)
      DEFAULT_PATH="$2"
      shift 2
      ;;
    -port)
      DEFAULT_PORT="$2"
      shift 2
      ;;
    -mode)
      DEFAULT_MODE="$2"
      shift 2
      ;;
    -user)
      DEFAULT_USERNAME="$2"
      shift 2
      ;;
    -pass)
      DEFAULT_PASSWORD="$2"
      shift 2
      ;;
    -venv)
      case "$2" in
        true|false)
          USE_VENV="$2"
          ;;
        *)
          echo "❌ Invalid value for -venv (use true or false)"
          exit 1
          ;;
      esac
      shift 2
      ;;
    *)
      echo "❌ Unknown argument: $1"
      exit 1
      ;;
  esac
done

USE_VENV="${USE_VENV:-false}"

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
    # Fallback if root
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
# Log directory + files
# -------------------------------
LOG_DIR="$USER_HOME/custom_https_server_log/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/custom_https_server.log"
ERR_FILE="$LOG_DIR/custom_https_server.err"

# Create empty files if they don't exist
touch "$LOG_FILE" "$ERR_FILE"

# Set permissions so the real user can write
chown -R "$REAL_USER" "$USER_HOME/custom_https_server_log"

echo "✅ Logs directory created:"
echo "   LOG_DIR = $LOG_DIR"
echo "   LOG_FILE = $LOG_FILE"
echo "   ERR_FILE = $ERR_FILE"

# -------------------------------
# Platform-specific paths
# -------------------------------
TARGET_DIR="/opt/custom_https_server"
CONFIG_PATH="/etc/custom-https-server.conf"
SERVICE_PATH="/etc/systemd/system/custom-https-server.service"

if [[ "$OS_NAME" == "Darwin" ]]; then
  TARGET_DIR="$DEFAULT_TARGET_DIR"
  CONFIG_PATH="$DEFAULT_CONFIG_PATH"
fi

# -------------------------------
# Detect Python
# -------------------------------
PYTHON_BIN="$(command -v python3 || true)"

if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
    echo "❌ python3 not found"
    exit 1
fi
echo "Detected Python: $PYTHON_BIN"

# -------------------------------
# Helper: check psutil
# -------------------------------
has_psutil() {
    "$1" - <<'EOF' >/dev/null 2>&1
import psutil
EOF
}

# =========================================================
# 🐧 LINUX
# =========================================================
if [[ "$OS_NAME" == "Linux" ]]; then
    echo "🐧 Linux detected"

    if [[ "$USE_VENV" == "true" ]]; then
        echo "🐍 VENV enabled — using virtual environment on Linux"

        VENV_DIR="$HOME/custom_https_server_venv"

        if [[ ! -d "$VENV_DIR" ]]; then
            echo "Creating virtual environment at $VENV_DIR"
            "$PYTHON_BIN" -m venv "$VENV_DIR"
        fi

        # shellcheck disable=SC1091
        source "$VENV_DIR/bin/activate"

        pip install --upgrade pip psutil

        FINAL_PYTHON="$VENV_DIR/bin/python"
    else
        if has_psutil "$PYTHON_BIN"; then
            echo "✅ psutil already available (system Python)"
            FINAL_PYTHON="$PYTHON_BIN"
        else
            echo "⚠️ psutil missing, installing via package manager..."

            if command -v apt >/dev/null 2>&1; then
                sudo apt update
                sudo apt install -y python3-psutil python3-venv
            elif command -v dnf >/dev/null 2>&1; then
                sudo dnf install -y python3-psutil python3-venv
            elif command -v yum >/dev/null 2>&1; then
                sudo yum install -y python3-psutil python3-venv
            elif command -v pacman >/dev/null 2>&1; then
                sudo pacman -S --noconfirm python-psutil python-virtualenv
            else
                echo "⚠️ Package manager not found, using pip --user"
                "$PYTHON_BIN" -m pip install --user psutil virtualenv
            fi

            if has_psutil "$PYTHON_BIN"; then
                echo "✅ psutil installed successfully (system Python)"
                FINAL_PYTHON="$PYTHON_BIN"
            else
                echo "❌ Failed to install psutil on Linux"
                exit 1
            fi
        fi
    fi
fi

# =========================================================
# 🍎 macOS (Darwin)
# =========================================================
if [[ "$OS_NAME" == "Darwin" ]]; then
    echo "🍎 macOS detected"

    if [[ "$USE_VENV" == "true" ]]; then
        echo "🐍 VENV enabled — using virtual environment"

        VENV_DIR="$HOME/custom_https_server_venv"

        if [[ ! -d "$VENV_DIR" ]]; then
            echo "Creating virtual environment at $VENV_DIR"
            "$PYTHON_BIN" -m venv "$VENV_DIR"
        fi

        # shellcheck disable=SC1091
        source "$VENV_DIR/bin/activate"

        pip install --upgrade pip psutil

        FINAL_PYTHON="$VENV_DIR/bin/python"
    else
        echo "ℹ️ VENV disabled — using system Python"
        FINAL_PYTHON="$PYTHON_BIN"
    fi
fi


# -------------------------------
# Truth source
# -------------------------------
echo "✅ Final Python used: $FINAL_PYTHON"

# -------------------------------
# Install files
# -------------------------------
sudo mkdir -p "$TARGET_DIR"
sudo mkdir -p "$(dirname "$CONFIG_PATH")"
sudo cp custom_https_server.py "$TARGET_DIR/"

if [[ -f default-config.conf ]]; then
  sudo cp default-config.conf "$CONFIG_PATH"
fi

# -------------------------------
# Load config properly
# -------------------------------
if [[ -f "$CONFIG_PATH" ]]; then
    set -a
    while IFS='=' read -r k v; do
      # Trim leading/trailing whitespace
      k="${k#"${k%%[![:space:]]*}"}"
      k="${k%"${k##*[![:space:]]}"}"
      v="${v#"${v%%[![:space:]]*}"}"
      v="${v%"${v##*[![:space:]]}"}"

      # skip empty lines and comments
      [[ -z "$k" || "$k" =~ ^# ]] && continue

      case "$k" in
        SERVE_PATH|SERVE_PORT|MODE|AUTH_USERNAME|AUTH_PASSWORD)
          export "$k=$v"
          ;;
        LINUX_SERVE_PATH) [[ -n "$v" ]] && LINUX_SERVE_PATH="$v" ;;
        MAC_SERVE_PATH) [[ -n "$v" ]] && MAC_SERVE_PATH="$v" ;;
        LOG_DIR) [[ -n "$v" ]] && LOG_DIR="$v" ;;
        LOG_FILE) [[ -n "$v" ]] && LOG_FILE="$v" ;;
        ERR_FILE) [[ -n "$v" ]] && ERR_FILE="$v" ;;
      esac
    done < "$CONFIG_PATH"
    set +a
else
    echo "❌ Config file not found: $CONFIG_PATH"
    exit 1
fi

# -------------------------------
# Resolve SERVE_PATH (priority: CLI -> OS-specific -> generic fallback)
# -------------------------------
if [[ -n "$DEFAULT_PATH" ]]; then
    SERVE_PATH="$DEFAULT_PATH"
else
    if [[ "$OS_NAME" == "Linux" ]]; then
        if [[ -d "/root" ]]; then
            SERVE_PATH="/root"
        else
            SERVE_PATH="$LINUX_SERVE_PATH"
        fi
    elif [[ "$OS_NAME" == "Darwin" ]]; then
        if [[ -d "/root" ]]; then
            SERVE_PATH="/root"
        else
            SERVE_PATH="$MAC_SERVE_PATH"
        fi
    else
        SERVE_PATH="/root"
    fi
fi

# -------------------------------
# Validate serve path
# -------------------------------
if [[ ! -d "$SERVE_PATH" ]]; then
    echo "❌ Serve path does not exist: $SERVE_PATH"
    exit 1
fi

# -------------------------------
# Apply other CLI overrides
# -------------------------------
[[ -n "$DEFAULT_PORT" ]] && SERVE_PORT="$DEFAULT_PORT"
[[ -n "$DEFAULT_MODE" ]] && MODE="$DEFAULT_MODE"
[[ -n "$DEFAULT_USERNAME" ]] && AUTH_USERNAME="$DEFAULT_USERNAME"
[[ -n "$DEFAULT_PASSWORD" ]] && AUTH_PASSWORD="$DEFAULT_PASSWORD"


# -------------------------------
# Persist effective config
# -------------------------------
sudo tee "$CONFIG_PATH" >/dev/null <<EOF
# -------------------------------
# Generic server
# -------------------------------
SERVE_PATH=$SERVE_PATH
SERVE_PORT=$SERVE_PORT
MODE=$MODE
# -------------------------------
# Auth
# -------------------------------
AUTH_USERNAME=$AUTH_USERNAME
AUTH_PASSWORD=$AUTH_PASSWORD
# -------------------------------
# Logs
# -------------------------------
CFG_LOG_DIR=$LOG_DIR
CFG_LOG_FILE=$LOG_FILE
CFG_ERR_FILE=$ERR_FILE
EOF


# -------------------------------
# Debug output (truth source)
# -------------------------------
echo "🛠️  DEBUG (install file): Config file    = $CONFIG_PATH"
echo "🛠️  DEBUG (install file): Serve Path     = $SERVE_PATH"
echo "🛠️  DEBUG (install file): Port           = $SERVE_PORT"
echo "🛠️  DEBUG (install file): Mode           = $MODE"
echo "🛠️  DEBUG (install file): Username       = $AUTH_USERNAME"
echo "🛠️  DEBUG (install file): Password       = ********"


# -------------------------------
# Service setup
# -------------------------------
if [[ "$OS_NAME" == "Linux" ]]; then
  echo "✅ Installing on Linux"
  # Generate service file with correct python

    sudo tee "$SERVICE_PATH" > /dev/null <<EOF
[Unit]
Description=Custom HTTP Server
After=network.target

[Service]
EnvironmentFile=${CONFIG_PATH}
ExecStart=$FINAL_PYTHON ${TARGET_DIR}/custom_https_server.py \
  --path="\${SERVE_PATH}" \
  --port="\${SERVE_PORT}" \
  --mode="\${MODE}" \
  --user="\${AUTH_USERNAME}" \
  --pass="\${AUTH_PASSWORD}"
WorkingDirectory=${TARGET_DIR}
Restart=on-failure
User=root
Group=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable custom-https-server
  sudo systemctl restart custom-https-server
  echo "Logs: journalctl -u custom-https-server -f"

elif [[ "$OS_NAME" == "Darwin" ]]; then
  echo "✅ Installing on macOS"
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  chmod +x "$SCRIPT_DIR/macos-launchd-setup.sh"

  LAUNCHD_ARGS=(
    -python "$FINAL_PYTHON"
    -path "$SERVE_PATH"
    -port "$SERVE_PORT"
    -mode "$MODE"
    -user "$AUTH_USERNAME"
    -pass "$AUTH_PASSWORD"
    -logdir "$LOG_DIR"
    -logfile "$LOG_FILE"
    -errfile "$ERR_FILE"
  )

  # echo "launchd args: ${LAUNCHD_ARGS[*]}"
  bash "$SCRIPT_DIR/macos-launchd-setup.sh" "${LAUNCHD_ARGS[@]}"
else
  echo "❌ Unsupported OS: $OS_NAME"
  exit 1
fi

echo "✅ Installation completed"
