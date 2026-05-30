#!/usr/bin/env bash
# Issue + install Let's Encrypt cert for tg-lcloud.duckdns.org and append the
# TLS server block to shop-nginx's config. Idempotent: safe to re-run.
#
# Prerequisites (HUMAN action required first):
#   1. tg-lcloud.duckdns.org A-record points to THIS server's public IP
#      Verify: dig +short tg-lcloud.duckdns.org   →   $(curl -s https://api.ipify.org)
#   2. Port 80 reachable from the public internet (ufw already allows it).
#
# What this script does:
#   1. Sanity-check DNS resolves to this server
#   2. certbot --webroot --webroot-path=/root/luxiva/nginx/certbot
#   3. Append HTTPS server block to /root/luxiva/nginx/default.conf
#   4. docker exec shop-nginx nginx -t  →  reload
#   5. Restart LCloud so it picks up LC_HOST=0.0.0.0 + Secure cookies

set -euo pipefail

DOMAIN="tg-lcloud.duckdns.org"
NGINX_CONF="/root/luxiva/nginx/default.conf"
WEBROOT="/root/luxiva/nginx/certbot"
LCLOUD_UPSTREAM="http://172.18.0.1:8787"   # host bridge gateway from luxiva_default

if [[ "${EUID}" -ne 0 ]]; then
  echo "must be run as root" >&2
  exit 1
fi

if ! command -v certbot >/dev/null 2>&1; then
  echo "certbot not installed" >&2
  exit 2
fi

# ---- 1. DNS sanity check ---------------------------------------------------
my_ip=$(curl -s --max-time 5 https://api.ipify.org)
domain_ip=$(getent hosts "$DOMAIN" | awk 'NR==1 {print $1}' || true)
if [[ -z "$domain_ip" ]]; then
  echo "DNS for $DOMAIN does not resolve at all yet" >&2
  exit 3
fi
if [[ "$domain_ip" != "$my_ip" ]]; then
  echo "DNS for $DOMAIN resolves to $domain_ip but this server is $my_ip" >&2
  echo "Update DuckDNS A-record first." >&2
  exit 4
fi
echo "✓ DNS resolves to this server ($my_ip)"

# ---- 2. Issue cert via webroot challenge ----------------------------------
if [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
  echo "✓ cert already issued for $DOMAIN; skipping certbot"
else
  certbot certonly \
    --webroot --webroot-path="$WEBROOT" \
    --non-interactive --agree-tos \
    --email "admin@${DOMAIN}" \
    -d "$DOMAIN"
  echo "✓ cert issued for $DOMAIN"
fi

# ---- 3. Append HTTPS server block (if not already present) ----------------
if grep -q "server_name $DOMAIN" "$NGINX_CONF" \
     && grep -q "listen 443.*$DOMAIN\|# >>> tg-lcloud TLS" "$NGINX_CONF"; then
  echo "✓ HTTPS block already present; skipping"
else
  cp "$NGINX_CONF" "${NGINX_CONF}.bak.$(date +%s)"
  cat >> "$NGINX_CONF" <<EOF

# >>> tg-lcloud TLS (managed by /root/LCloud/scripts/setup-tls.sh)
server {
  listen 443 ssl;
  listen [::]:443 ssl;
  http2 on;
  server_name ${DOMAIN};

  ssl_certificate     /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
  ssl_protocols TLSv1.2 TLSv1.3;
  ssl_ciphers HIGH:!aNULL:!MD5;
  ssl_session_cache shared:SSL_lcloud:10m;
  ssl_session_timeout 1d;

  add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
  add_header X-Content-Type-Options nosniff always;
  add_header X-Frame-Options DENY always;
  add_header Referrer-Policy strict-origin-when-cross-origin always;

  # Allow up to ~1 GiB upload (LC_MAX_FILE_BYTES + multipart slack)
  client_max_body_size 1100m;

  # Long timeouts for streaming uploads/downloads of multi-GB blobs
  client_body_timeout 3600s;
  proxy_read_timeout  3600s;
  proxy_send_timeout  3600s;
  send_timeout        3600s;

  # Stream uploads through to the backend rather than spooling to nginx disk
  proxy_request_buffering off;
  proxy_buffering         off;

  proxy_http_version 1.1;
  proxy_set_header Host \$host;
  proxy_set_header X-Real-IP \$remote_addr;
  proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto https;
  proxy_set_header Connection "";

  location / {
    proxy_pass ${LCLOUD_UPSTREAM};
  }
}
# <<< tg-lcloud TLS
EOF
  echo "✓ HTTPS block appended to $NGINX_CONF"
fi

# ---- 4. Validate + reload nginx -------------------------------------------
docker exec shop-nginx nginx -t
docker exec shop-nginx nginx -s reload
echo "✓ shop-nginx reloaded"

# ---- 5. (Re)start LCloud --------------------------------------------------
if systemctl list-unit-files lcloud.service >/dev/null 2>&1; then
  systemctl restart lcloud
  echo "✓ lcloud.service restarted"
else
  echo "ℹ lcloud.service not installed yet."
  echo "  Run /root/LCloud/scripts/install-systemd.sh && systemctl start lcloud"
fi

echo
echo "All set. Open https://${DOMAIN}/ and complete the Telegram login."
