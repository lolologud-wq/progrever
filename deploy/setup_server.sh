#!/bin/bash
set -eu

APP_DIR="/opt/progrever"

apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv rsync

mkdir -p "$APP_DIR/sessions" "$APP_DIR/media"

cd "$APP_DIR"

if [ ! -d venv ]; then
  python3 -m venv venv
fi

./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -r requirements.txt -q

chmod +x session_login.py 2>/dev/null || true

# Create .env from example if missing
if [ ! -f .env ]; then
  cp .env.example .env
  echo "WARNING: .env created from template — fill in BOT_TOKEN, API_ID, API_HASH, ADMIN_IDS"
fi

cp deploy/progrever.service /etc/systemd/system/progrever.service
systemctl daemon-reload
systemctl enable progrever
systemctl restart progrever

sleep 2
systemctl status progrever --no-pager || true
