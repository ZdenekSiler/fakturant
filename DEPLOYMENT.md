# Deployment Guide — Hetzner + Docker Compose

Complete walkthrough from a blank Hetzner server to a live HTTPS invoice app.

## Architecture

```
Internet  →  Caddy (443/80, TLS)  →  nginx/frontend (SPA)  →  FastAPI/backend
                                                             →  SQLite volume
```

Three Docker containers, one named volume for data persistence. Caddy handles
Let's Encrypt certificates automatically — no manual cert management needed.

---

## 1. Provision a Hetzner server

1. Log in to [console.hetzner.cloud](https://console.hetzner.cloud)
2. Create a new server:
   - **Type:** CX22 (2 vCPU, 4 GB RAM) — sufficient for single-user/small-team
   - **Image:** Ubuntu 24.04
   - **SSH key:** add your public key
   - **Firewall:** create one (see step 3)
3. Note the server's public IPv4 address

---

## 2. Point DNS at the server

At your DNS provider, add an A record:

```
invoice.example.com  →  <server IPv4>
```

Wait for propagation (usually < 5 min). Caddy won't get a cert until DNS resolves.

---

## 3. Harden the server

SSH in as root, then:

```bash
# Update packages
apt update && apt upgrade -y

# Create a non-root user (optional but recommended)
adduser deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy/

# Firewall: allow only SSH, HTTP, HTTPS
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp   # HTTP/3
ufw --force enable

# Disable root SSH login
sed -i 's/^PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl reload sshd

# Install fail2ban to block brute-force SSH
apt install -y fail2ban
systemctl enable --now fail2ban
```

---

## 4. Install Docker

```bash
# Official Docker Engine repo (not the Ubuntu snap version)
curl -fsSL https://get.docker.com | sh

# Add your user to the docker group so you don't need sudo
usermod -aG docker $USER
newgrp docker

# Verify
docker compose version   # must be v2.x
```

---

## 5. Deploy the app

```bash
# Clone the repo
git clone <your-repo-url> /opt/fakturant
cd /opt/fakturant

# Configure environment
cp .env.example .env
nano .env
```

Fill in `.env`:

```bash
DB_PATH=/data/fakturant.db
LOG_LEVEL=warning
DOMAIN=invoice.example.com          # ← your real domain
LETSENCRYPT_EMAIL=you@example.com   # ← for cert expiry emails
ALLOWED_ORIGIN=https://invoice.example.com
```

First deploy:

```bash
make deploy
```

This runs:
```
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Caddy will automatically obtain a Let's Encrypt certificate on first startup
(takes ~10–30 seconds). Watch it happen:

```bash
make logs   # Ctrl-C to stop following
```

Verify:

```bash
curl https://invoice.example.com/health
# → {"status":"ok","version":"4.0.0"}
```

---

## 6. Schedule daily backups

```bash
crontab -e
```

Add:

```cron
0 3 * * * /opt/fakturant/scripts/backup.sh >> /opt/fakturant/backups/backup.log 2>&1
```

Backups land in `/opt/fakturant/backups/` and are rotated — the last 30 days
are kept automatically.

---

## 7. Routine operations

### Check status
```bash
make ps
make logs
docker stats   # live CPU/memory per container
```

### Deploy a code update
```bash
cd /opt/fakturant
make update   # git pull + rebuild + prune old images
```

### Manual backup
```bash
make backup
ls backups/
```

### Restore from a backup
```bash
make restore FILE=backups/fakturant-2026-05-14.db
docker compose restart backend
```

### Open a shell inside the backend container
```bash
make shell
```

---

## 8. HTTPS certificate details

Caddy renews certificates automatically (well before expiry). No action needed.

If you change the domain:
1. Update `DOMAIN` in `.env`
2. Run `make deploy`
3. Caddy obtains a new certificate for the new domain

To force certificate renewal:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec caddy \
    caddy reload --config /etc/caddy/Caddyfile
```

---

## 9. Disaster recovery

### Database corrupted or accidentally deleted

1. Stop the app: `docker compose down`
2. Find most recent backup: `ls -lt backups/`
3. Restore: `make restore FILE=backups/fakturant-<date>.db`
4. Start: `make deploy`

### Server lost / migrating to new server

1. Download the latest backup from the old server:
   ```bash
   scp deploy@old-server:/opt/fakturant/backups/fakturant-latest.db ./
   ```
2. Provision a new server (steps 1–5 above)
3. Copy the backup onto the new server and restore

---

## 10. Estimated cost (Hetzner, 2026)

| Resource | Monthly |
|---|---|
| CX22 server (2 vCPU, 4 GB) | ~€4.35 |
| IPv4 address | ~€0.60 |
| Bandwidth (20 TB included) | €0 |
| **Total** | **~€5/mo** |

Upgrade path: CX32 (4 vCPU, 8 GB, ~€8.96/mo) if the app grows.

---

## 11. Local development (no TLS)

```bash
# Start backend with frontend served on the same origin
cd backend
FRONTEND_DIR=../frontend uv run uvicorn main:app --reload

# Or run with Docker (port 80, no HTTPS)
docker compose up --build
```

The `docker-compose.prod.yml` override is only applied by `make deploy` /
`make dev` — never in plain `docker compose up`.
