#!/usr/bin/env bash
# Run e2e remote tests inside a Playwright Docker container.
# Works on any host OS — avoids Playwright's OS support restrictions.
#
# Usage:
#   scripts/run-e2e.sh                          # against production
#   E2E_BASE_URL=http://localhost:8000 scripts/run-e2e.sh  # against local

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

E2E_BASE_URL="${E2E_BASE_URL:-https://fakturant.zdenovo.com}"
E2E_EMAIL="${E2E_EMAIL:-zd.siler@gmail.com}"
E2E_PASSWORD="${E2E_PASSWORD:-Test1234!}"

echo "→ Running e2e tests against $E2E_BASE_URL"

docker run --rm \
  -v "$ROOT/backend":/app \
  -w /app \
  -e E2E_BASE_URL="$E2E_BASE_URL" \
  -e E2E_EMAIL="$E2E_EMAIL" \
  -e E2E_PASSWORD="$E2E_PASSWORD" \
  mcr.microsoft.com/playwright/python:v1.52.0-noble \
  bash -c "pip install pytest pytest-playwright --quiet && pytest tests/test_e2e_remote.py -v --tb=short -m e2e"
