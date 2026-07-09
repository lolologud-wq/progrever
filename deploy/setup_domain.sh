#!/bin/bash
set -eu

DOMAIN="hazenet.today"
APP_DIR="/opt/progrever"

echo "==> Installing nginx + certbot..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx

echo "==> Configuring nginx for $DOMAIN..."
cp "$APP_DIR/deploy/nginx_hazenet.today.conf" /etc/nginx/sites-available/hazenet.today
ln -sf /etc/nginx/sites-available/hazenet.today /etc/nginx/sites-enabled/hazenet.today
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl enable nginx
systemctl reload nginx

if command -v ufw >/dev/null 2>&1; then
  ufw allow 80/tcp >/dev/null 2>&1 || true
  ufw allow 443/tcp >/dev/null 2>&1 || true
fi

echo "==> Trying SSL certificate (needs DNS A-record -> server IP)..."
if certbot --nginx \
    -d "$DOMAIN" -d "www.$DOMAIN" \
    --non-interactive --agree-tos \
    --register-unsafely-without-email \
    --redirect 2>&1; then
  echo "SSL OK: https://$DOMAIN"
else
  echo "SSL skipped — DNS probably not ready yet."
  echo "When A-record points to this server, run:"
  echo "  certbot --nginx -d $DOMAIN -d www.$DOMAIN --non-interactive --agree-tos --register-unsafely-without-email --redirect"
fi

systemctl reload nginx
echo "==> Done. Site: http://$DOMAIN (or https if SSL succeeded)"
