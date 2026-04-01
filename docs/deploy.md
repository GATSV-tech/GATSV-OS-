# GATSV OS — Production Deployment Guide

VPS: `root@185.28.22.133`  
Deploy dir: `/opt/gatsv-os`  
Service: `gatsv-os.service` (systemd → Docker Compose)

---

## Prerequisites

### Local machine
- SSH access to the VPS (`ssh root@185.28.22.133` must succeed)
- Git remote `origin` pointing at GitHub
- `.env` values ready (see [Environment Variables](#environment-variables))

### DNS
Point your domain at `185.28.22.133` before running certbot.  
Wait for propagation (`dig +short YOUR_DOMAIN` should return the VPS IP) — certbot will fail otherwise.

---

## 1. First-Time VPS Setup

Run once on a fresh Ubuntu 22.04/24.04 VPS:

```bash
ssh root@185.28.22.133 'bash -s' < scripts/setup-vps.sh --domain bot.gatsv.com
```

What it does:
1. Updates system packages
2. Installs Docker, nginx, certbot
3. Clones the repo to `/opt/gatsv-os`
4. Writes nginx config (HTTP only, ACME passthrough)
5. Installs and enables `gatsv-os.service` (does **not** start it)

If the repo is private, add a deploy key first:
```bash
# On VPS
ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N ""
cat ~/.ssh/deploy_key.pub   # add to GitHub repo → Settings → Deploy keys

# Then run setup with SSH URL
ssh root@185.28.22.133 'bash -s' < scripts/setup-vps.sh \
  --domain bot.gatsv.com \
  --repo git@github.com:gatsv/gatsv-os.git
```

---

## 2. Environment Variables

On the VPS, create the production env file:

```bash
ssh root@185.28.22.133
cp /opt/gatsv-os/services/control-plane/.env.example \
   /opt/gatsv-os/services/control-plane/.env
nano /opt/gatsv-os/services/control-plane/.env
```

Fill in every value. Key fields:

| Variable | Notes |
|---|---|
| `APP_BASE_URL` | `https://YOUR_DOMAIN` — no trailing slash |
| `SUPABASE_URL` | Production project URL |
| `SUPABASE_SERVICE_KEY` | Service role key (not anon key) |
| `ANTHROPIC_API_KEY` | Production key |
| `SENDBLUE_API_KEY` / `_SECRET` | From Sendblue dashboard |
| `SENDBLUE_WEBHOOK_SECRET` | Random secret you set in the webhook URL |
| `SLACK_BOT_TOKEN` | `xoxb-...` from Slack app |
| `SLACK_SIGNING_SECRET` | From Slack app Basic Information |
| `SLACK_OPS_CHANNEL_ID` | Right-click channel in Slack → Copy Channel ID |
| `JAKE_PHONE_NUMBER` | `+1XXXXXXXXXX` |
| `POSTMARK_INBOUND_WEBHOOK_SECRET` | Random secret for URL token param |
| `TALLY_WEBHOOK_SECRET` | From Tally webhook settings |

The `.env` file must never be committed. It lives only on the VPS.

---

## 3. TLS Certificate

After DNS has propagated:

```bash
ssh root@185.28.22.133
certbot --nginx -d bot.gatsv.com
```

Certbot will:
- Verify the ACME challenge via nginx
- Write the TLS cert to `/etc/letsencrypt/live/bot.gatsv.com/`
- Patch nginx config with the SSL stanzas

Verify nginx is happy:
```bash
nginx -t && systemctl reload nginx
```

Auto-renewal is configured by certbot's systemd timer — no action needed.

---

## 4. Start the Service

```bash
ssh root@185.28.22.133
systemctl start gatsv-os
systemctl status gatsv-os
```

The service runs `docker compose -f /opt/gatsv-os/docker-compose.prod.yml up -d --build` on start. First run takes a few minutes (Docker image build).

Check it's up:
```bash
curl https://bot.gatsv.com/health
# → {"status": "ok", ...}
```

---

## 5. Slack App Configuration

In your Slack app settings (`api.slack.com/apps`):

### Interactivity & Shortcuts
- **Enable Interactivity**: On
- **Request URL**: `https://bot.gatsv.com/slack/interactions`

### Event Subscriptions (if used)
- **Request URL**: `https://bot.gatsv.com/slack/events`

### OAuth Scopes (Bot Token)
Ensure these scopes are added:
- `chat:write` — post messages
- `chat:write.public` — post to channels without joining

Reinstall the app after adding scopes. The new bot token goes in `.env` as `SLACK_BOT_TOKEN`.

---

## 6. External Webhook URLs

Update each service to point at the live domain:

| Service | Webhook URL |
|---|---|
| Sendblue | `https://bot.gatsv.com/inbound/imessage?token=YOUR_SENDBLUE_WEBHOOK_SECRET` |
| Postmark (inbound email) | `https://bot.gatsv.com/inbound/email?token=YOUR_POSTMARK_INBOUND_WEBHOOK_SECRET` |
| Tally | `https://bot.gatsv.com/inbound/form?token=YOUR_TALLY_WEBHOOK_SECRET` |

The `token` query param is verified via HMAC on each endpoint. Use the same value you set in `.env`.

---

## 7. Ongoing Deploys

After any code change, deploy from your local machine:

```bash
./scripts/deploy.sh
```

Or with explicit options:
```bash
./scripts/deploy.sh --host root@185.28.22.133 --health-url https://bot.gatsv.com
```

What it does:
1. Pushes current branch to GitHub (warns if not on `main`)
2. SSHs to VPS: `git pull --ff-only` → `docker compose build --no-cache` → `docker compose up -d`
3. Waits up to 60s for container health check to pass
4. Runs `GET /health` from local machine
5. On failure: prints last 30 log lines and exits 1

Add `--health-url` to enable the live health check in step 4.

---

## 8. Logs

```bash
# Container logs (live)
ssh root@185.28.22.133 \
  "docker compose -f /opt/gatsv-os/docker-compose.prod.yml logs -f"

# Last 100 lines
ssh root@185.28.22.133 \
  "docker compose -f /opt/gatsv-os/docker-compose.prod.yml logs --tail=100"

# systemd journal (service start/stop events)
ssh root@185.28.22.133 "journalctl -u gatsv-os -f"

# Container status
ssh root@185.28.22.133 \
  "docker compose -f /opt/gatsv-os/docker-compose.prod.yml ps"
```

---

## 9. Rollback

If a deploy breaks things:

```bash
ssh root@185.28.22.133

cd /opt/gatsv-os

# Find the last good commit
git log --oneline -10

# Roll back to it
git checkout <SHA>

# Rebuild and restart
docker compose -f docker-compose.prod.yml build --no-cache
docker compose -f docker-compose.prod.yml up -d

# Verify
curl http://localhost:8000/health
```

Then on your local machine, revert the bad commit and push so the next deploy is clean.

---

## 10. Resolving VPS SSH Access

If `ssh root@185.28.22.133` fails with `Permission denied (publickey,password)`:

**Option A — Add your local public key via the VPS console:**
1. Log into the VPS host's web console (Hetzner, DigitalOcean, etc.)
2. Open a console session
3. Run: `echo "YOUR_PUBLIC_KEY" >> /root/.ssh/authorized_keys`
4. Get your public key: `cat ~/.ssh/id_ed25519.pub` (or `id_rsa.pub`)

**Option B — Use a key file:**
```bash
./scripts/deploy.sh --key ~/.ssh/your-vps-key.pem
ssh -i ~/.ssh/your-vps-key.pem root@185.28.22.133
```

**Option C — Enable password auth temporarily (not recommended for prod):**
In `/etc/ssh/sshd_config` set `PasswordAuthentication yes`, then `systemctl restart sshd`.

---

## File Reference

| File | Purpose |
|---|---|
| `docker-compose.prod.yml` | Production Compose config — port binding, healthcheck, log rotation |
| `services/control-plane/.env.example` | Template for `.env` — all required vars documented |
| `deploy/nginx.conf` | nginx reverse proxy + TLS config |
| `deploy/gatsv-os.service` | systemd unit managing Docker Compose lifecycle |
| `scripts/setup-vps.sh` | One-time VPS provisioning script |
| `scripts/deploy.sh` | Ongoing deploy script (push → pull → rebuild → health check) |
