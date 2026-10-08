# Debugging a missing payment QR code

This guide covers why an invoice can come out without the "QR platba" code, and how to check a specific invoice in production.

## How the QR code is built

`backend/main.py` → `_render_invoice()` calls `services/qr.generate_qr_b64(data)`. The template `invoice_modern.html` renders the QR block only `{% if qr_b64 %}`. If the QR code can't be built, the block is left out and **no error is shown**.

`generate_qr_b64()` returns `None` when any of these is true:

| Cause | Where |
|---|---|
| No IBAN, and the account can't be converted to one | `build_spd()` → `czech_account_to_iban()` → `services/bank.parse_czech_account()` |
| The account has no bank code (`670100-2212415741` instead of `670100-2212415741/6210`) | the same pattern requires `/dddd` |
| `grand_total() <= 0` (credit notes, empty invoice) | `build_spd()` |
| `segno` is missing or throws | `except Exception: return None` |

Since `feature/bank-validation-profile`, the first two cases appear as warnings. They show next to the bank-account field and in the "Zkontrolovat" banner (`InvoiceData.validation_warnings()`, `/validate` → `warnings`).

## Quick check

```bash
scripts/debug-qr.sh FA-2026-008          # uses the ssh alias "zdenovo"
```

The script is read-only. It prints the container status, the segno version, the stored bank account and IBAN, the SPD payment string, and whether the QR code was generated.

## Doing it by hand, step by step

**1. Find the QR code.**
```bash
grep -rniE "qr|spayd" backend --include=*.py --include=*.html
```

**2. Containers on the Hetzner host.** The ssh alias comes from `~/.ssh/config` inside WSL; see the zdenovo `docs/deployment.md`.
```bash
ssh zdenovo 'docker ps --format "{{.Names}} | {{.Image}} | {{.Status}}" | grep -i faktur'
```

**3. Rule out the library.**
```bash
ssh zdenovo 'docker exec fakturant-backend-1 python -c "import segno; print(segno.__version__)"'
```

**4. Look at the stored invoice data (read-only).**
```bash
ssh zdenovo bash -s <<'EOF'
docker exec -i fakturant-backend-1 python - <<'PY'
import sqlite3, json
c = sqlite3.connect("/data/fakturant.db")
for n, d in c.execute("SELECT invoice_number, data FROM invoices ORDER BY id DESC LIMIT 3"):
    d = json.loads(d); print(n, repr(d["bank_account"]), repr(d["iban"]))
PY
EOF
```

**5. Reproduce locally.**
```bash
cd backend && python3 -c "from services.qr import czech_account_to_iban as f; print(f('670100-2212415741'), f('670100-2212415741/6210'))"
# None CZ0362106701002212415741
```

## Gotchas

- **Quote the heredoc** (`<<'EOF'`) so your local shell doesn't expand `$` or `{{ }}`.
- **`docker exec -i` reads stdin.** In a script piped over ssh, an earlier `docker exec -i` swallows every line after it. Use `-i` only on the exec that actually needs stdin.
- **From Windows PowerShell,** nested quotes break. Put the commands in a `.sh` file and run `wsl.exe -d Ubuntu-24.04 bash /mnt/c/path/script.sh`. The ssh alias lives in WSL's `~/.ssh/config`.
- **Container names** carry a compose suffix (`fakturant-backend-1`). Look them up with `docker ps | grep` rather than hard-coding them.
