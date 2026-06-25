#!/usr/bin/env bash
# Backup the SQLite database out of the running backend container.
# Creates: backups/fakturant-YYYY-MM-DD.db
# Safe to run while the app is live — SQLite's .backup command is online-safe.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$ROOT/backups"
DATE=$(date +%F)
DEST="$BACKUP_DIR/fakturant-$DATE.db"

mkdir -p "$BACKUP_DIR"

echo "→ Backing up to $DEST …"
docker compose -f "$ROOT/docker-compose.yml" exec -T backend \
    sqlite3 /data/fakturant.db ".backup /data/backup-tmp.db"

docker compose -f "$ROOT/docker-compose.yml" cp \
    backend:/data/backup-tmp.db "$DEST"

docker compose -f "$ROOT/docker-compose.yml" exec -T backend \
    rm -f /data/backup-tmp.db

echo "✓ Backup saved: $DEST ($(du -h "$DEST" | cut -f1))"

# Keep only the 30 most recent backups
ls -t "$BACKUP_DIR"/fakturant-*.db 2>/dev/null | tail -n +31 | xargs -r rm --
