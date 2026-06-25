# Fakturant

Czech invoicing web app for freelancers and small businesses. Creates legally-compliant invoices (§ 29 zákona č. 235/2004 Sb.), tracks payments, and generates PDFs.

## Features

- Sequential invoice numbering with gap detection (FA-2025-001 format)
- 6-state invoice lifecycle: draft → issued → sent → paid / overdue / cancelled
- Payment tracking with auto-settlement detection (within 1 Kč rounding tolerance)
- Credit notes (dobropisy) with automatic DD-prefix numbering
- Three invoice templates: Modern, Classic, Minimal
- Live split-pane preview — 400 ms debounced re-render on every keystroke
- PDF generation via weasyprint (pixel-perfect from the same Jinja2 templates)
- Czech ARES company registry lookup by IČO or name
- Logo upload (drag-and-drop, base64-embedded)
- Dark UI with Czech localization

## Requirements

**Development**
- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (package manager)

**Production (Docker)**
- Docker 24+
- Docker Compose v2

## Project Structure

```
invoice_app/
├── backend/                    # FastAPI API server
│   ├── main.py                 # App entry point, all route handlers
│   ├── models.py               # Pydantic InvoiceData model + validation
│   ├── services/
│   │   ├── db.py               # SQLite persistence layer (aiosqlite)
│   │   └── ares.py             # Czech ARES registry HTTP client
│   ├── templates/              # Jinja2 invoice render templates
│   │   ├── invoice_modern.html
│   │   ├── invoice_classic.html
│   │   └── invoice_minimal.html
│   ├── tests/
│   │   ├── conftest.py
│   │   └── test_invoicing_features.py
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/                   # Static SPA (nginx)
│   ├── index.html              # SPA shell
│   ├── src/
│   │   ├── app.js              # Vanilla JS — no framework
│   │   └── app.css             # Design system
│   ├── nginx.conf
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── CLAUDE.md                   # AI assistant guidelines
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API framework | FastAPI 0.115+ |
| Runtime | Python 3.13, uvicorn |
| Database | SQLite + aiosqlite (async) |
| Validation | Pydantic v2 |
| Templating | Jinja2 |
| PDF | weasyprint |
| ARES client | httpx |
| Frontend | Vanilla JS + HTML/CSS (no framework) |
| Reverse proxy | nginx 1.27 |
| Container | Docker + Compose |

## Local Development

```bash
cd backend
uv sync
uv run uvicorn main:app --reload
```

Open the frontend directly in a browser (or serve with any static file server):
```bash
cd frontend
python -m http.server 3000
```

Run tests:
```bash
cd backend
uv run pytest
```

## Deployment (Hetzner)

The app ships as two Docker containers behind nginx. A CX11 (2 vCPU, 2 GB RAM) is sufficient for single-user or small-team use.

**1. Configure environment**

```bash
cp .env.example .env
# edit .env if needed (defaults are fine for Hetzner)
```

**2. Build and start**

```bash
docker compose up -d --build
```

The app is available on port 80. Invoice data persists in a named Docker volume (`invoice_data`).

**3. HTTPS (recommended)**

Put a Caddy or nginx reverse proxy in front with a Let's Encrypt certificate:

```bash
# Example Caddyfile
yourdomain.com {
    reverse_proxy localhost:80
}
```

**Backup**

```bash
# Copy the SQLite file out of the volume
docker compose exec backend cp /data/fakturant.db /tmp/backup.db
docker compose cp backend:/tmp/backup.db ./backup-$(date +%F).db
```

## API Reference

Interactive docs at `/api/docs` (Swagger UI) and `/api/redoc` when the backend is running.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness check |
| GET | `/api/invoices` | List invoices (paginated) |
| GET | `/api/invoices/{id}` | Get invoice with payments |
| POST | `/api/invoices/save` | Create or update invoice |
| PATCH | `/api/invoices/{id}/status` | Transition status |
| POST | `/api/invoices/{id}/payments` | Record payment |
| DELETE | `/api/invoices/{id}/payments/{pid}` | Remove payment |
| POST | `/api/invoices/{id}/credit-note` | Prepare credit note draft |
| GET | `/api/sequence/next` | Peek next invoice number |
| GET | `/api/sequence/check` | Check for sequence gap |
| GET | `/api/ares/ico/{ico}` | ARES lookup by IČO |
| GET | `/api/ares/search?q=` | ARES search by name |
| POST | `/preview` | Render invoice HTML |
| POST | `/validate` | Validate against Czech law |
| POST | `/generate-pdf` | Download invoice PDF |

## Database Schema

SQLite database at the path set by `DB_PATH` (default `/data/fakturant.db` in Docker).

```sql
invoices   (id, invoice_number, doc_type, status, credit_note_for,
            created_at, updated_at, issued_at, due_date, total, paid_total, data)
payments   (id, invoice_id, paid_on, amount, note)
sequences  (year, prefix, last_seq)  -- per-year per-prefix counters
```

`data` is a JSON blob containing the full `InvoiceData` structure. Denormalised columns (`invoice_number`, `status`, `due_date`, `total`, `paid_total`) exist for efficient listing and overdue queries without deserialising the blob.

## Invoice Status Lifecycle

```
draft → issued → sent → paid
                   ↓      ↑  (payment recorded)
                 overdue ──┘  (due_date passed)
         ↓
      cancelled  (any state except paid)
```

Overdue scanning runs at startup and is available via `POST /api/invoices/mark-overdue`.
