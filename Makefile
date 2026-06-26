# Fakturant — deployment helpers

COMPOSE_BASE = docker compose -f docker-compose.yml
COMPOSE_PROD = $(COMPOSE_BASE) -f docker-compose.prod.yml

.PHONY: dev deploy update logs backup restore shell ps clean test

## ── Local dev ────────────────────────────────────────────────────────────────

dev:
	$(COMPOSE_BASE) up --build

test:
	cd backend && uv run pytest -x -q

## ── Production (Hetzner — run on the server) ────────────────────────────────

deploy:
	@echo "→ Backing up database before deploy..."
	@/opt/fakturant/scripts/backup.sh || (echo "ERROR: backup failed — aborting" && exit 1)
	git pull --ff-only
	$(COMPOSE_PROD) up -d --build --remove-orphans

update: deploy
	docker image prune -f

logs:
	$(COMPOSE_PROD) logs -f --tail=150

ps:
	$(COMPOSE_PROD) ps

## ── Backup & restore ─────────────────────────────────────────────────────────

backup:
	@mkdir -p backups
	$(COMPOSE_PROD) exec -T backend sqlite3 /data/fakturant.db ".backup /tmp/backup.db"
	$(COMPOSE_PROD) cp backend:/tmp/backup.db backups/fakturant-$$(date +%Y-%m-%d).db
	@echo "✓ Saved to backups/fakturant-$$(date +%Y-%m-%d).db"

# Usage: make restore FILE=backups/fakturant-2026-05-14.db
restore:
	@test -n "$(FILE)" || (echo "Usage: make restore FILE=backups/<name>.db" && exit 1)
	$(COMPOSE_PROD) cp "$(FILE)" backend:/data/restore-tmp.db
	$(COMPOSE_PROD) exec -T backend sqlite3 /data/fakturant.db ".restore /data/restore-tmp.db"
	$(COMPOSE_PROD) exec -T backend rm /data/restore-tmp.db
	$(COMPOSE_PROD) restart backend
	@echo "✓ Restored from $(FILE)"

## ── Misc ─────────────────────────────────────────────────────────────────────

shell:
	$(COMPOSE_PROD) exec backend /bin/bash

clean:
	$(COMPOSE_PROD) down --remove-orphans
	docker volume rm fakturant_invoice_data 2>/dev/null || true
