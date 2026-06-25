#!/usr/bin/env bash
# Dev startup — loads credentials from ../.env, overrides Docker-specific paths.
set -e
cd "$(dirname "$0")"

REPO_ROOT="$(cd .. && pwd)"

# Load .env if it exists (for SESSION_SECRET, ALLOW_SIGNUP etc.)
ENV_FILE="$REPO_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
  set -o allexport
  source "$ENV_FILE"
  set +o allexport
fi

# Always use a local DB path in dev — the .env has the Docker container path
export DB_PATH="$REPO_ROOT/data/fakturant.db"
mkdir -p "$REPO_ROOT/data"

# Warn if SESSION_SECRET is missing or too short
if [ "${#SESSION_SECRET}" -lt 32 ]; then
  echo "⚠️  SESSION_SECRET not set or too short — generating a temporary one"
  export SESSION_SECRET=$(.venv/bin/python3 -c 'import secrets; print(secrets.token_hex(32))')
  echo "   (not persisted — set SESSION_SECRET in .env for a stable session)"
fi

export FRONTEND_DIR="../frontend"
export ALLOW_SIGNUP="${ALLOW_SIGNUP:-true}"

pkill -f 'uvicorn main:app' 2>/dev/null || true
sleep 1

echo "DB:      $DB_PATH"
echo "Signup:  $ALLOW_SIGNUP"
echo "Visit:   http://localhost:8000"
exec .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
