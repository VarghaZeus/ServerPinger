#!/usr/bin/env sh
# Update ServerPinger in place: pull, reinstall requirements, migrate, restart.
#
#   ./deploy/update.sh
#
# Deliberately manual - there is no self-update button in the web UI.
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VENV="$APP_DIR/.venv"
SERVICE_NAME=serverpinger

cd "$APP_DIR"

echo "==> Current version: $(cat VERSION 2>/dev/null || echo unknown)"

echo "==> git pull"
git pull --ff-only

if [ ! -x "$VENV/bin/python" ]; then
  echo "error: no virtualenv at $VENV. Run deploy/install.sh first." >&2
  exit 1
fi

echo "==> Installing requirements"
"$VENV/bin/python" -m pip install -r requirements.txt

echo "==> Applying database migrations"
"$VENV/bin/python" run.py --init-db

echo "==> New version: $(cat VERSION 2>/dev/null || echo unknown)"

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q "^$SERVICE_NAME.service"; then
  echo "==> Restarting $SERVICE_NAME"
  if [ "$(id -u)" -eq 0 ]; then
    systemctl restart "$SERVICE_NAME"
    systemctl --no-pager --lines=0 status "$SERVICE_NAME" || true
  else
    sudo systemctl restart "$SERVICE_NAME"
  fi
else
  echo "==> No systemd unit installed; restart ServerPinger yourself."
fi
