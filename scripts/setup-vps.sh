#!/usr/bin/env bash
# setup-vps.sh — one-time VPS provisioning for GATSV OS Control Plane
#
# Run this once on a fresh Ubuntu 22.04/24.04 VPS:
#   ssh root@185.28.22.133 'bash -s' < scripts/setup-vps.sh
#
# What it does:
#   1. Updates system packages
#   2. Installs Docker, nginx, certbot
#   3. Clones the repo to /opt/gatsv-os
#   4. Installs the systemd service
#   5. Prints next steps (env file, TLS, DNS)
#
# Does NOT start the service — you must populate .env first.

set -euo pipefail

REPO_URL="https://github.com/gatsv/gatsv-os.git"   # update if private
DEPLOY_DIR="/opt/gatsv-os"
DOMAIN=""   # set via --domain flag or prompted below

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --domain) DOMAIN="$2"; shift 2 ;;
    --repo)   REPO_URL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$DOMAIN" ]; then
  read -rp "Enter your domain (e.g. bot.gatsv.com): " DOMAIN
fi

echo "==> GATSV OS VPS setup — domain: $DOMAIN"
echo "==> Deploy dir: $DEPLOY_DIR"

# ── 1. System update ──────────────────────────────────────────────────────────
echo ""
echo "── [1/6] Updating system packages ──"
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq curl git ufw

# ── 2. Docker ─────────────────────────────────────────────────────────────────
echo ""
echo "── [2/6] Installing Docker ──"
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
else
  echo "    Docker already installed: $(docker --version)"
fi

# Ensure docker compose plugin is available
docker compose version &>/dev/null || apt-get install -y docker-compose-plugin

# ── 3. nginx + certbot ────────────────────────────────────────────────────────
echo ""
echo "── [3/6] Installing nginx and certbot ──"
apt-get install -y -qq nginx certbot python3-certbot-nginx
systemctl enable nginx

# ── 4. Firewall ───────────────────────────────────────────────────────────────
echo ""
echo "── [4/6] Configuring firewall ──"
ufw allow OpenSSH
ufw allow "Nginx Full"
ufw --force enable
ufw status

# ── 5. Clone repo ─────────────────────────────────────────────────────────────
echo ""
echo "── [5/6] Cloning repository ──"
if [ -d "$DEPLOY_DIR/.git" ]; then
  echo "    Repo already exists at $DEPLOY_DIR — pulling latest"
  git -C "$DEPLOY_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$DEPLOY_DIR"
fi

# ── 6. nginx config + systemd ─────────────────────────────────────────────────
echo ""
echo "── [6/6] Installing nginx config and systemd service ──"

# nginx — substitute domain placeholder
sed "s/YOUR_DOMAIN_HERE/$DOMAIN/g" "$DEPLOY_DIR/deploy/nginx.conf" \
  > /etc/nginx/sites-available/gatsv-os

# Enable site, remove default if present
ln -sf /etc/nginx/sites-available/gatsv-os /etc/nginx/sites-enabled/gatsv-os
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx

# systemd
cp "$DEPLOY_DIR/deploy/gatsv-os.service" /etc/systemd/system/gatsv-os.service
systemctl daemon-reload
systemctl enable gatsv-os

# ── Done — print next steps ───────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo " VPS setup complete. Next steps:"
echo "════════════════════════════════════════════════════════"
echo ""
echo "  1. Create the production env file:"
echo "     cp $DEPLOY_DIR/services/control-plane/.env.example \\"
echo "        $DEPLOY_DIR/services/control-plane/.env"
echo "     nano $DEPLOY_DIR/services/control-plane/.env"
echo ""
echo "  2. Point DNS: $DOMAIN → 185.28.22.133"
echo "     (wait for propagation before running certbot)"
echo ""
echo "  3. Issue TLS certificate:"
echo "     certbot --nginx -d $DOMAIN"
echo ""
echo "  4. Start the service:"
echo "     systemctl start gatsv-os"
echo "     systemctl status gatsv-os"
echo ""
echo "  5. Tail logs:"
echo "     journalctl -u gatsv-os -f"
echo "     docker compose -f $DEPLOY_DIR/docker-compose.prod.yml logs -f"
echo ""
echo "  6. Configure your Slack app:"
echo "     Interactivity Request URL: https://$DOMAIN/slack/interactions"
echo "     Sendblue webhook URL:      https://$DOMAIN/inbound/imessage?token=YOUR_SECRET"
echo "     Postmark inbound URL:      https://$DOMAIN/inbound/email?token=YOUR_SECRET"
echo ""
