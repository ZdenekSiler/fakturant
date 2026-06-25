#!/usr/bin/env bash
# Restore the SQLite database into the running backend container.
# Usage: scripts/restore.sh backups/fakturant-2026-05-14.db
# WARNING: This replaces the live database. Stop traffic first if possible.

set -euo pipefail

FILE="${1:-}"
if [[ -z "$FILE" ]]; then
    echo "Usage: $0 <backup-file.db>"
    exit 1
fi

if [[ ! -f "$FILE" ]]; then
    echo "Error: file not found: $FILE"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

echo "→ Restoring from $FILE …"
docker compose -f "$ROOT/docker-compose.yml" cp "$FILE" backend:/data/restore-tmp.db

docker compose -f "$ROOT/docker-compose.yml" exec -T backend \
    sqlite3 /data/fakturant.db ".restore /data/restore-tmp.db"

docker compose -f "$ROOT/docker-compose.yml" exec -T backend \
    rm -f /data/restore-tmp.db

echo "✓ Restore complete. Restart the backend to pick up changes:"
echo "  docker compose restart backend"
