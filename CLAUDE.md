# CLAUDE.md — Fakturant codebase guide

## Project overview

Czech invoicing SPA. Backend: FastAPI + SQLite. Frontend: vanilla JS served by nginx. Two Docker containers, one volume for data persistence.

**Key docs:** [AUTH.md](AUTH.md) — auth & multi-user design · [INVOICE_LIFECYCLE.md](INVOICE_LIFECYCLE.md) — state machine & transition rules · [DEPLOYMENT.md](DEPLOYMENT.md) — Hetzner setup

## Repository layout

```
backend/      Python package — all server-side logic
frontend/     Static SPA — nginx serves, proxies /api/* to backend
```

## How to run locally

```bash
bash backend/start_dev.sh   # loads .env, starts uvicorn with --reload on :8000
```

Full auth + multi-user flow is active. See [AUTH.md](AUTH.md) for the complete auth design.

## Auth in a nutshell

- Multi-user: each user sees only their own invoices (`WHERE user_id = ?`)
- Session: signed cookie (`itsdangerous.TimestampSigner`) containing `user_id`
- Password: Argon2id hash stored in `users` table — not reversible
- Protected: `/api/invoices/*` requires valid session; form/preview/PDF are public
- Config: `SESSION_SECRET` (required ≥32 chars), `ALLOW_SIGNUP` (default `true`)

## How to run tests

```bash
cd backend
uv run pytest            # all tests
uv run pytest -x -q      # fail-fast, quiet
```

Tests use a `tmp_path` SQLite DB injected via `monkeypatch`. No mocking of the DB layer — tests hit a real database. Do not change this.

## Code patterns

### Backend

- **Entry point**: `backend/main.py` — FastAPI app, all route handlers, request/response models
- **Data model**: `backend/models.py` — `InvoiceData` is the single source of truth. It validates input, computes totals, and checks Czech legal requirements
- **Persistence**: `backend/services/db.py` — pure async SQLite functions. No ORM, no FastAPI dependency. Raises `ValueError` on not-found, never `HTTPException` (that's main.py's job)
- **ARES**: `backend/services/ares.py` — thin httpx client for the Czech business registry. Returns `None` on 404, empty list on search failure

### Frontend

- **Single file JS**: `frontend/src/app.js` — no build step, no framework, no bundler
- **Dashboard**: `frontend/src/dashboard.js` — overview screen (KPI cards, charts, aging, recent invoices). Loaded after `app.js`; depends on `apiFetch`, `loadInvoice`, `escHtml`, `fmtNum`, `STATUS_LABELS` being in global scope. Uses Chart.js 4 via CDN.
- **Auto-save**: 1500 ms debounce after any form change
- **Auto-preview**: 400 ms debounce, POSTs current form state to `/preview`, renders result in an iframe
- **State**: in-memory form object + `currentInvoiceId` for persisted invoice
- **View switching**: CSS `.open` class toggle; `openDashboard()` / `closeDashboard()` in `app.js`; dashboard sits at z-index 250 (below drawer at 300)

### Sequence numbering

The counter is in the `sequences` table (year + prefix composite PK). `peek_next_number` reads without committing. `next_number` atomically increments. The counter is committed only when `commit_sequence: true` is passed to `/api/invoices/save` on first save.

## Key invariants to preserve

1. `services/db.py` must not import from `fastapi` — it's a pure data layer
2. `InvoiceData.validation_errors()` returns Czech-language strings — keep them user-facing
3. Credit note items must have `unit_price <= 0` — the `create_credit_note` function negates them
4. `paid_total` is always recalculated from the payments table on every add/delete — never trust the cached value for business logic
5. Sequence gaps are detected client-side and warned; they are not prevented server-side (user may have a legitimate reason to skip)

## Adding a new invoice template

1. Create `backend/templates/invoice_{name}.html` using Jinja2
2. Add `"{name}"` to the `Literal["modern", "classic", "minimal"]` union in `backend/models.py`
3. Add the option to the template selector in `frontend/src/app.js`

## Docker / Hetzner deployment

```bash
docker compose up -d --build
```

- Frontend container: nginx on port 80, proxies `/api/*`, `/preview`, `/validate`, `/generate-pdf`, `/health` to the backend container
- Backend container: uvicorn on port 8000, not exposed externally
- Volume `invoice_data` mounts at `/data` in the backend container; `DB_PATH` env var points there

To change the exposed port: set `FRONTEND_PORT` in `.env`.

## Dashboard

The "Přehled" button in the header opens a full-workspace dashboard view. It fetches `/api/invoices?limit=500` once and aggregates everything client-side — no dedicated stats endpoint exists.

**Metrics computed:**
- KPI cards: outstanding balance, overdue amount, invoiced this month, invoiced YTD (grouped by `issued_at`)
- Monthly revenue bar chart: last 12 months, "Fakturováno" vs "Uhrazeno" series
- Status donut: count + amount per status (invoices only, not credit notes)
- Aging: outstanding invoices bucketed by days past `due_date` (not yet due / 1–30 / 31–60 / 61–90 / 90+)
- Recent: last 8 invoices by `updated_at`; clicking a row opens it in the editor

**Chart library:** Chart.js 4 loaded from CDN (`cdn.jsdelivr.net`). If the CDN is unavailable, charts are silently skipped; KPI cards and tables still render.

**Tests:** `backend/tests/test_dashboard_data.py` — verifies the list endpoint returns all fields the dashboard depends on.

## What's not implemented yet

- Email delivery of invoices
- Alembic-style schema versioning (migrations run inline via `_MIGRATIONS` list in `db.py`)
- Invoice templates `classic` and `minimal` (only `modern` exists; add HTML files to `backend/templates/`)
