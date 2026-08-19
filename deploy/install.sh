#!/usr/bin/env sh
# ServerPinger installer for Linux and macOS.
#
#   sudo ./deploy/install.sh                 # install and enable the service
#   SERVERPINGER_PORT=9000 ./deploy/install.sh
#
# Creates a venv, installs pinned requirements, initialises the database and
# (on Linux) installs + enables the systemd unit.
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VENV="$APP_DIR/.venv"
PORT="${SERVERPINGER_PORT:-8282}"
HOST="${SERVERPINGER_HOST:-0.0.0.0}"
SERVICE_NAME=serverpinger

# Run the service as the invoking user, not as root, even under sudo.
RUN_USER="${SUDO_USER:-$(id -un)}"
RUN_GROUP=$(id -gn "$RUN_USER")

case "$(uname -s)" in
  Linux)  DEFAULT_DATA="$(eval echo ~"$RUN_USER")/.local/share/serverpinger" ;;
  Darwin) DEFAULT_DATA="$(eval echo ~"$RUN_USER")/.local/share/serverpinger" ;;
  *)      DEFAULT_DATA="$APP_DIR/data" ;;
esac
DATA_DIR="${SERVERPINGER_DATA:-$DEFAULT_DATA}"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: $PYTHON not found. Install Python 3.9 or newer." >&2
  exit 1
fi

echo "==> App directory:  $APP_DIR"
echo "==> Data directory: $DATA_DIR"
echo "==> Service user:   $RUN_USER:$RUN_GROUP"

echo "==> Creating virtualenv"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

echo "==> Initialising database"
mkdir -p "$DATA_DIR"
SERVERPINGER_DATA="$DATA_DIR" "$VENV/bin/python" "$APP_DIR/run.py" --init-db

# Anything created while running under sudo must end up owned by the service user.
if [ "$(id -u)" -eq 0 ]; then
  chown -R "$RUN_USER:$RUN_GROUP" "$DATA_DIR" "$VENV" 2>/dev/null || true
fi

if [ "$(uname -s)" = "Linux" ] && command -v systemctl >/dev/null 2>&1; then
  if [ "$(id -u)" -ne 0 ]; then
    echo
    echo "Not running as root, so the systemd unit was not installed."
    echo "Re-run with sudo to install it, or start ServerPinger manually:"
    echo "  SERVERPINGER_DATA=$DATA_DIR $VENV/bin/python $APP_DIR/run.py"
  else
    echo "==> Installing systemd unit"
    sed -e "s|__APPDIR__|$APP_DIR|g" \
        -e "s|__DATADIR__|$DATA_DIR|g" \
        -e "s|__USER__|$RUN_USER|g" \
        -e "s|__GROUP__|$RUN_GROUP|g" \
        -e "s|__HOST__|$HOST|g" \
        -e "s|__PORT__|$PORT|g" \
        "$APP_DIR/deploy/serverpinger.service" > "/etc/systemd/system/$SERVICE_NAME.service"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    sleep 2
    systemctl --no-pager --lines=0 status "$SERVICE_NAME" || true
    echo
    echo "Logs: journalctl -u $SERVICE_NAME -f"
  fi
else
  echo
  echo "No systemd here (macOS or a non-systemd Linux). Start it manually with:"
  echo "  SERVERPINGER_DATA=$DATA_DIR $VENV/bin/python $APP_DIR/run.py"
  echo "or wrap that command in a launchd plist / your init system of choice."
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "${IP:-}" ] || IP=$(hostname)
echo
echo "ServerPinger is available at:"
echo "  http://localhost:$PORT/"
echo "  http://$IP:$PORT/"
echo
echo "If you cannot reach it from another machine, open the port, e.g.:"
echo "  sudo ufw allow $PORT/tcp"
echo "  sudo firewall-cmd --add-port=$PORT/tcp --permanent && sudo firewall-cmd --reload"
