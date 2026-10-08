#!/usr/bin/env bash
# Read-only check of why an invoice does / does not get a payment QR code in production.
# Usage: scripts/debug-qr.sh FA-2026-008 [ssh-alias]
# See docs/DEBUG_QR_PAYMENT.md for the background.
set -euo pipefail

NUMBER="${1:?usage: $0 <INVOICE_NUMBER> [ssh-alias]}"
HOST="${2:-zdenovo}"

[[ "$NUMBER" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "invalid invoice number: $NUMBER" >&2; exit 1; }

ssh -o ConnectTimeout=10 "$HOST" NUMBER="$NUMBER" bash -s <<'EOF'
set -e
C=$(docker ps --format '{{.Names}}' | grep -i 'faktur.*backend' | head -1)
[ -n "$C" ] || { echo "no fakturant backend container running" >&2; exit 1; }
echo "container: $C"
docker ps --format '{{.Names}} | {{.Status}}' | grep -i faktur
# Only this last exec gets -i: an earlier `docker exec -i` would swallow the rest of this script from stdin.
docker exec -i -e NUMBER="$NUMBER" -w /app "$C" python - <<'PY'
import json, os, sqlite3, sys
sys.path.insert(0, ".")
from models import InvoiceData
from services.qr import build_spd, czech_account_to_iban, generate_qr_b64

try:
    import segno
    print("segno:", segno.__version__)
except ImportError:
    print("segno: NOT INSTALLED -> no QR for any invoice")

db = sqlite3.connect(os.environ.get("DB_PATH", "/data/fakturant.db"))
rows = db.execute("SELECT user_id, status, updated_at, data FROM invoices WHERE invoice_number=?",
                  (os.environ["NUMBER"],)).fetchall()
if not rows:
    sys.exit(f"invoice {os.environ['NUMBER']} not found")
for user_id, status, updated_at, data in rows:
    d = json.loads(data)
    inv = InvoiceData(**d)
    print(f"\nuser={user_id} status={status} updated={updated_at}")
    print("bank_account:", repr(inv.bank_account), "-> IBAN:", czech_account_to_iban(inv.bank_account) if inv.bank_account else None)
    print("iban        :", repr(inv.iban))
    print("grand_total :", inv.grand_total())
    print("SPD         :", build_spd(inv))
    print("QR          :", "OK" if generate_qr_b64(inv) else "MISSING")
    if hasattr(inv, "validation_warnings"):
        for w in inv.validation_warnings():
            print("warning     :", w)
PY
EOF
