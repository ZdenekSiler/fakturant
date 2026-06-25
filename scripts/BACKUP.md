# Backup & Restore — FA-2026-001

## Why this exists

Invoice **FA-2026-001** was issued and sent to the customer (Nummera s.r.o.) on 2026-05-20.
During active development of the invoicing app the SQLite database can be accidentally wiped,
migrated incorrectly, or the user record can lose its data. This folder contains a point-in-time
snapshot that restores the system to the exact sent state so work can continue safely.

---

## What is backed up

| Item | Value |
|---|---|
| Invoice | FA-2026-001 · 44 600 CZK · status `issued` · due 2026-06-03 |
| Supplier (profile) | Zdeněk Šiler · IČO 11979879 · bank 670100-2212415741/6210 |
| Customer (contact) | Nummera s.r.o. · IČO 08257817 · DIČ CZ08257817 · Slezská 2127, Praha |
| Sequence counter | FA-2026 last\_seq=1 → next new invoice = **FA-2026-002** |
| User | zd.siler@gmail.com · email\_verified=1 |

### Invoice line items (21 total)

| Project | Items | Rate | Hours |
|---|---|---|---|
| Automoto | 3 | 800 CZK/h | 8 h |
| INRA | 5 | 800 CZK/h | 9 h |
| PUFR | 13 | 1 000 CZK/h | 33 h |

> **Not included:** `signature_b64` (large JPEG — re-upload via the UI after restore).

---

## Files

```
scripts/
  restore_fa_2026_001.py   # Python restore script — all snapshot data embedded
  restore.sh               # Shell wrapper for Docker container restore (production)
  BACKUP.md                # This file
```

---

## How to restore (local dev)

Prerequisites: the dev server does **not** need to be running. The user
`zd.siler@gmail.com` must already exist in the database (registered at `/signup`).

```bash
# From the project root:
python3 scripts/restore_fa_2026_001.py

# Or point at a different DB path:
DB_PATH=/data/fakturant.db python3 scripts/restore_fa_2026_001.py
```

Expected output:

```
Restoring snapshot to: /home/.../data/fakturant.db

  user  : id=4 (zd.siler@gmail.com)
  profile: Zdeněk Šiler · IČO 11979879 · účet 670100-2212415741/6210
  invoice: updated  id=6  FA-2026-001  44,600 CZK  [issued]
  contact: updated  Nummera s.r.o. · IČO 08257817
  sequence: FA-2026-001 committed → next will be FA-2026-002

  ✓ Restore complete.
```

The script is **idempotent** — running it multiple times is safe.

---

## How to restore (production Docker)

`restore.sh` replaces the live SQLite file inside the backend container.
Use only when you have a full `.db` file backup (e.g. from `docker cp`).

```bash
# 1. Take a backup of the current DB first
docker compose cp backend:/data/fakturant.db backups/fakturant-$(date +%F).db

# 2. Restore from a backup file
bash scripts/restore.sh backups/fakturant-2026-05-14.db

# 3. Restart backend to pick up changes
docker compose restart backend
```

---

## After any restore — checklist

- [ ] Log in at `/login` as `zd.siler@gmail.com`
- [ ] Open the invoice list → FA-2026-001 visible with status `issued`
- [ ] Click **Profil** in the header → supplier fields pre-filled
- [ ] Click **Kontakty** in the customer section → Nummera s.r.o. present
- [ ] Click **Nová** → new invoice number is FA-2026-002
- [ ] Re-upload signature via the invoice form if needed

---

## Re-running the dev server after restore

```bash
bash backend/start_dev.sh
```

The DB migrations run automatically on startup — the restore script is safe to run
before or after the server starts.
