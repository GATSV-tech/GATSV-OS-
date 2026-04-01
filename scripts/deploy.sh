#!/usr/bin/env bash
# deploy.sh — push latest code to VPS and restart the service
#
# Usage (from your local machine):
#   ./scripts/deploy.sh
#   ./scripts/deploy.sh --host root@185.28.22.133
#   ./scripts/deploy.sh --host root@185.28.22.133 --key ~/.ssh/your-key
#
# What it does:
#   1. Pushes your local main branch to GitHub (if not already pushed)
#   2. SSHs to the VPS, pulls latest, rebuilds Docker image, restarts service
#   3. Runs a health check against the live /health endpoint
#   4. Prints the last 20 log lines on failure

set -euo pipefail

# ── Defaults (override with flags) ────────────────────────────────────────────
VPS_HOST="root@185.28.22.133"
SSH_KEY=""
DEPLOY_DIR="/opt/gatsv-os"
HEALTH_URL=""        # set after domain is known, or leave empty to skip check
COMPOSE_FILE="docker-compose.prod.yml"

while [[ $# -gt 0 ]]; do
  case $1 in
    --host)        VPS_HOST="$2"; shift 2 ;;
    --key)         SSH_KEY="$2"; shift 2 ;;
    --deploy-dir)  DEPLOY_DIR="$2"; shift 2 ;;
    --health-url)  HEALTH_URL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=15"
if [ -n "$SSH_KEY" ]; then
  SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
fi

ssh_run() {
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "$VPS_HOST" "$@"
}

# ── 1. Push to git remote (ensure VPS can pull) ───────────────────────────────
echo "==> [1/3] Pushing local main to remote..."
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
  echo "  WARNING: you are on branch '$CURRENT_BRANCH', not main."
  read -rp "  Deploy anyway? [y/N] " confirm
  [[ "$confirm" =~ ^[Yy]$ ]] || exit 0
fi
git push origin "$CURRENT_BRANCH"
LOCAL_SHA=$(git rev-parse --short HEAD)
echo "  Pushed $LOCAL_SHA"

# ── 2. Remote: pull + rebuild + restart ──────────────────────────────────────
echo ""
echo "==> [2/3] Deploying on VPS ($VPS_HOST)..."
ssh_run bash -s << REMOTE
set -euo pipefail

cd "$DEPLOY_DIR"

echo "  Pulling latest..."
git pull --ff-only

echo "  Building Docker image..."
docker compose -f "$COMPOSE_FILE" build --no-cache

echo "  Restarting service..."
docker compose -f "$COMPOSE_FILE" up -d

echo "  Waiting for container to pass health check..."
for i in \$(seq 1 12); do
  STATUS=\$(docker compose -f "$COMPOSE_FILE" ps --format json 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Health','unknown'))" 2>/dev/null || echo "unknown")
  if [ "\$STATUS" = "healthy" ]; then
    echo "  Container healthy after \${i}x5s"
    break
  fi
  echo "  [\$i/12] Status: \$STATUS — waiting 5s..."
  sleep 5
done

echo ""
echo "  Deployed commit: \$(git log --oneline -1)"
docker compose -f "$COMPOSE_FILE" ps
REMOTE

# ── 3. Health check from local machine ────────────────────────────────────────
echo ""
echo "==> [3/3] Health check..."
if [ -n "$HEALTH_URL" ]; then
  HTTP_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$HEALTH_URL/health" || echo "000")
  if [ "$HTTP_STATUS" = "200" ]; then
    echo "  ✓ $HEALTH_URL/health → 200 OK"
  else
    echo "  ✗ Health check failed: $HEALTH_URL/health → $HTTP_STATUS"
    echo ""
    echo "  Last 30 lines of container logs:"
    ssh_run "docker compose -f $DEPLOY_DIR/$COMPOSE_FILE logs --tail=30"
    exit 1
  fi
else
  echo "  (Skipped — set --health-url https://YOUR_DOMAIN to enable)"
fi

echo ""
echo "✓ Deploy complete — $LOCAL_SHA is live."
