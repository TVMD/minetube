#!/usr/bin/env bash
# Issue and renew a Let's Encrypt certificate for the Invidious stack.
#
# Usage (run as root):
#   sudo ./certs.sh issue    # first-time certificate
#   sudo ./certs.sh renew    # renew if due (safe to run daily from cron)
#   sudo ./certs.sh test     # dry-run renewal, no real certificate issued
#   sudo ./certs.sh status   # show certificates and expiry dates
#
# Config can be set here or passed as env vars:
#   DOMAIN=yt.example.com EMAIL=you@example.com sudo -E ./certs.sh issue

set -euo pipefail

DOMAIN="${DOMAIN:-minetube.ddns.net}"
EMAIL="${EMAIL:-tavanminh97@gmail.com}"

# Cron has a minimal PATH. Add common locations (Linux, Homebrew on Intel/Apple Silicon).
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE=(docker compose -f "$DIR/docker-compose.yml")

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

need_root() {
  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "Run as root (sudo)." >&2
    exit 1
  fi
}

check_config() {
  if [ "$DOMAIN" = "yt.example.com" ] || [ "$EMAIL" = "you@example.com" ]; then
    echo "Set DOMAIN and EMAIL (edit the top of this script or pass env vars)." >&2
    exit 1
  fi
}

# Port 80 must be free for the standalone challenge, so nginx is stopped
# briefly (a few seconds) and started again afterwards.
stop_nginx()  { "${COMPOSE[@]}" stop nginx  >/dev/null 2>&1 || true; }
start_nginx() { "${COMPOSE[@]}" up -d nginx >/dev/null 2>&1 || true; }

cmd="${1:-}"

case "$cmd" in
  issue)
    need_root
    check_config
    if [ -d "/etc/letsencrypt/live/$DOMAIN" ] && [ "${FORCE:-0}" != "1" ]; then
      log "Certificate for $DOMAIN already exists. Use FORCE=1 to reissue, or run: renew"
      exit 0
    fi
    log "Requesting certificate for $DOMAIN"
    stop_nginx
    trap start_nginx EXIT
    certbot certonly --standalone \
      --non-interactive --agree-tos \
      -m "$EMAIL" -d "$DOMAIN"
    log "Certificate issued."
    ;;

  renew)
    need_root
    log "Checking renewal"
    # Hooks only run when a renewal is actually due, so nginx is not
    # touched on the days nothing needs renewing.
    certbot renew --quiet \
      --pre-hook  "docker compose -f $DIR/docker-compose.yml stop nginx" \
      --post-hook "docker compose -f $DIR/docker-compose.yml up -d nginx"
    log "Renewal check finished."
    ;;

  test)
    need_root
    log "Dry-run renewal"
    certbot renew --dry-run \
      --pre-hook  "docker compose -f $DIR/docker-compose.yml stop nginx" \
      --post-hook "docker compose -f $DIR/docker-compose.yml up -d nginx"
    ;;

  status)
    need_root
    certbot certificates
    ;;

  *)
    echo "Usage: sudo $0 {issue|renew|test|status}" >&2
    exit 1
    ;;
esac
