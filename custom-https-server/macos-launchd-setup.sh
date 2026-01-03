#!/bin/bash
# Name    : macos-launchd-setup.sh
# Version : 2026.01.03.01
# Author  : Muthukumar Subramanian
# OS      : macOS
# Purpose : Set up and register custom-https-server as a launchd agent on macOS

set -e

# -------------------------------
# Parse command-line arguments
# -------------------------------
PYTHON_PATH=""
SERVE_PATH=""
PORT=""
MODE=""
USERNAME=""
PASSWORD=""
LOG_DIR=""
LOG_FILE=""
ERR_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -python)
      PYTHON_PATH="$2"
      shift 2
      ;;
    -path)
      SERVE_PATH="$2"
      shift 2
      ;;
    -port)
      PORT="$2"
      shift 2
      ;;
    -mode)
      MODE="$2"
      shift 2
      ;;
    -user)
      USERNAME="$2"
      shift 2
      ;;
    -pass)
      PASSWORD="$2"
      shift 2
      ;;
        -logdir)
      LOG_DIR="$2"
      shift 2
      ;;
    -logfile)
      LOG_FILE="$2"
      shift 2
      ;;
    -errfile)
      ERR_FILE="$2"
      shift 2
      ;;
    *)
      echo "❌ Unknown option: $1"
      exit 1
      ;;
  esac
done

# -------------------------------
# Validate required arguments
# -------------------------------
if [[ -z "$SERVE_PATH" || -z "$PORT" ]]; then
  echo "❌ Error: --path and --port must be specified"
  exit 1
fi

# -------------------------------
# Define paths and Python interpreter
# -------------------------------
PLIST_PATH="$HOME/Library/LaunchAgents/com.custom_https_server.plist"
SCRIPT_PATH="/usr/local/custom_https_server/custom_https_server.py"

# -------------------------------
# Launchpad Debug Output
# -------------------------------
echo "🛠️  DEBUG (launchd): Python Path   = $PYTHON_PATH"
echo "🛠️  DEBUG (launchd): Script Path   = $SCRIPT_PATH"
echo "🛠️  DEBUG (launchd): Serve Path    = $SERVE_PATH"
echo "🛠️  DEBUG (launchd): Port          = $PORT"
echo "🛠️  DEBUG (launchd): Mode          = $MODE"
echo "🛠️  DEBUG (launchd): Username      = $USERNAME"
echo "🛠️  DEBUG (launchd): Password      = ********"


mkdir -p "$(dirname "$PLIST_PATH")"

# -------------------------------
# Create the launchd plist
# -------------------------------
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.custom_https_server.python</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_PATH</string>
    <string>$SCRIPT_PATH</string>
    <string>--path</string>
    <string>$SERVE_PATH</string>
    <string>--port</string>
    <string>$PORT</string>
EOF

if [[ -n "$MODE" ]]; then
  echo "    <string>--mode</string>" >> "$PLIST_PATH"
  echo "    <string>$MODE</string>" >> "$PLIST_PATH"
fi

if [[ -n "$USERNAME" ]]; then
  echo "    <string>--user</string>" >> "$PLIST_PATH"
  echo "    <string>$USERNAME</string>" >> "$PLIST_PATH"
fi

if [[ -n "$PASSWORD" ]]; then
  echo "    <string>--pass</string>" >> "$PLIST_PATH"
  echo "    <string>$PASSWORD</string>" >> "$PLIST_PATH"
fi

cat >> "$PLIST_PATH" <<EOF
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>WorkingDirectory</key>
  <string>/usr/local/custom_https_server</string>
  <key>StandardOutPath</key>
  <string>$LOG_FILE</string>
  <key>StandardErrorPath</key>
  <string>$ERR_FILE</string>
</dict>
</plist>
EOF

# -------------------------------
# Load the plist
# -------------------------------
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

#echo "🔎 launchd (system domain) — summary:"
#launchctl print system | grep custom_https_server || echo "   (not visible in system summary)"
#
#echo
#echo "🔎 launchd (system domain) — full service:"
#sudo launchctl print system/com.custom_https_server.python \
#  || echo "   ❌ Service not registered in system launchd"
#
#echo
#echo "🔎 Process check:"
#ps aux | grep '[c]ustom_https_server'
#
#echo
#echo "📄 Logs:"
#echo "   tail -f /tmp/custom_https_server.log"
#echo "   tail -f /tmp/custom_https_server.err"

echo
echo "🌐 Port binding check (${PORT}):"
sudo lsof -nP -iTCP:${PORT} -sTCP:LISTEN \
  || echo "   ❌ Nothing listening on port ${PORT}, Try manually:  sudo lsof -nP -iTCP:${PORT} -sTCP:LISTEN"

echo
echo "🔎 launchctl legacy list:"
sudo launchctl list | grep custom_https_server \
  || echo "   (not listed — expected on newer macOS)"
