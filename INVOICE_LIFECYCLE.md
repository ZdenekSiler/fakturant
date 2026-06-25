# Invoice Lifecycle

## States

An invoice moves through six states. Each state represents a specific stage in the billing process.

```
                    ┌─────────────┐
                    │   KONCEPT   │  draft
                    │   (draft)   │
                    └──────┬──────┘
                           │ user issues the invoice
                           ▼
                    ┌─────────────┐
                    │  VYSTAVENA  │  issued
                    │  (issued)   │◄─────────────────────────────┐
                    └──────┬──────┘                               │
                           │ user marks as sent                   │
                           ▼                                      │
                    ┌─────────────┐                               │
                    │  ODESLÁNO   │  sent                         │
                    │   (sent)    │                               │
                    └──────┬──────┘                               │
                    payment│recorded   due_date                   │
                    covers │total      passed                     │
                           ├──────────────────────┐              │
                           ▼                       ▼              │
                    ┌─────────────┐        ┌──────────────┐      │
                    │ ZAPLACENO   │        │ PO SPLATNOSTI │      │
                    │   (paid)    │◄───────│  (overdue)   │      │
                    └─────────────┘ payment└──────┬───────┘      │
                                    covers        │               │
                                    total         │ storno        │
                                                  ▼               │
                                          ┌──────────────┐        │
                                          │ STORNOVÁNO   │        │
                                          │ (cancelled)  │        │
                                          └──────────────┘        │
                                                  ▲               │
                                                  │ storno        │
                    ┌─────────────────────────────┘               │
                    │                                             │
             (from issued, sent, overdue)              (not from paid or cancelled)
```

---

## State reference

### Koncept (draft)

**What it means:** The invoice is being prepared. It exists in the database but has no legal significance yet.

**How you get here:** Automatically — every new invoice starts as a draft.

**What you can do:**
- Edit all fields freely (supplier, customer, items, dates, bank details)
- Add, edit, or delete line items
- Preview and download PDF
- Delete the invoice entirely

**What you cannot do:**
- Record payments (blocked: "Cannot record payment for a draft invoice")
- The invoice does not appear on any official books

**Auto-save:** Every form change triggers a save after 1.5 seconds. The header shows "Uloženo" when persisted. Requires being logged in — if not logged in, the header shows "Faktura se neukládá — přihlaste se".

**`issued_at`:** Not set.  
**`due_date`:** Can be set freely.

---

### Vystavena (issued)

**What it means:** The invoice has been officially created. `issued_at` is stamped with today's date. This is the legal moment of invoice creation under Czech law.

**How you get here:** Click the status pill → select "Vystavena" from the dropdown.

**What happens automatically:**
- `issued_at` is set to today's date (cannot be changed after this)
- The status pill turns blue

**What you can do:**
- Record payments (partial or full)
- Transition to Odesláno or Stornováno
- Download PDF

**What you cannot do:**
- Go back to Koncept
- Change `issued_at`

**`issued_at`:** Set to today on transition. Preserved through all subsequent transitions.

---

### Odesláno (sent)

**What it means:** The invoice has been sent to the customer. The due date clock is now running.

**How you get here:** From Vystavena — click the status pill → select "Odesláno".

**What you can do:**
- Record payments
- Transition to Zaplaceno, Po splatnosti, or Stornováno

**Overdue scan:** On every server startup, the app runs:
```sql
UPDATE invoices SET status = 'overdue'
WHERE status = 'sent'
  AND due_date IS NOT NULL
  AND due_date < today
```
Any Odesláno invoice whose `due_date` has passed is automatically flipped to Po splatnosti. This also runs via `POST /api/invoices/mark-overdue`.

---

### Po splatnosti (overdue)

**What it means:** The customer has not paid by the due date. The invoice is overdue.

**How you get here:**
- **Automatically** — server startup or manual trigger scans all Odesláno invoices with `due_date < today`
- **Manually** — click the status pill → select "Po splatnosti"

**What you can do:**
- Record payments (payment in full automatically flips to Zaplaceno)
- Transition to Zaplaceno or Stornováno

**What you cannot do:**
- Go back to Odesláno or Vystavena

---

### Zaplaceno (paid)

**What it means:** The invoice is fully settled. This is a terminal state.

**How you get here:**
- **Automatically** — when a payment is recorded and `paid_total ≥ total - 1 Kč` (1 Kč rounding tolerance)
- **Manually** — click the status pill → select "Zaplaceno"

**What you can do:**
- View the invoice and its payment history
- Download PDF

**What you cannot do:**
- Record more payments
- Transition to any other state
- Delete the invoice

**Rounding tolerance:** Payments within 1 Kč of the invoice total are treated as full payment. This handles common bank rounding cases.

---

### Stornováno (cancelled)

**What it means:** The invoice has been voided. This is a terminal state.

**How you get here:** From any state except Zaplaceno — click the status pill → select "Stornováno".

**What you can do:**
- View the invoice
- Download PDF (for your records)

**What you cannot do:**
- Transition to any other state
- Record payments
- Delete the invoice

**Accounting note:** If a Zaplaceno or Odesláno invoice needs to be reversed, the correct approach is to **not cancel it** but instead create a **Dobropis (credit note)** — see Credit Notes below.

---

## Allowed transitions table

| From \ To | Koncept | Vystavena | Odesláno | Zaplaceno | Po splatnosti | Stornováno |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Koncept** | — | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Vystavena** | ❌ | — | ✅ | ❌ | ❌ | ✅ |
| **Odesláno** | ❌ | ❌ | — | ✅ | ✅ | ✅ |
| **Po splatnosti** | ❌ | ❌ | ❌ | ✅ | — | ✅ |
| **Zaplaceno** | ❌ | ❌ | ❌ | — | ❌ | ❌ |
| **Stornováno** | ❌ | ❌ | ❌ | ❌ | ❌ | — |

---

## Automatic transitions

| Trigger | Condition | Transition |
|---|---|---|
| Payment recorded | `paid_total ≥ total - 1 Kč` | any → **Zaplaceno** |
| Server startup | `status = 'sent' AND due_date < today` | Odesláno → **Po splatnosti** |
| Manual API call | `POST /api/invoices/mark-overdue` | Odesláno → **Po splatnosti** |

---

## Payments and the lifecycle

Payments can be recorded on any invoice in **Vystavena**, **Odesláno**, or **Po splatnosti**.

```
Record payment
      ↓
paid_total recalculated from SUM of all payments
      ↓
paid_total ≥ total - 1 Kč?
      ├── YES → status → Zaplaceno (automatically)
      └── NO  → status unchanged, paid_total updated
```

**Partial payments** accumulate. You can record multiple payments against one invoice. Each payment stores `amount`, `paid_on` date, and an optional `note`.

**Deleting a payment** recalculates `paid_total` from scratch. If the invoice was Zaplaceno and a payment is deleted such that `paid_total < total`, the status does NOT automatically revert — you would need to manually transition it back to Odesláno.

---

## Credit notes (Dobropisy)

When an issued or paid invoice needs to be partially or fully reversed, use a **Dobropis** (credit note) instead of cancelling.

**How to create:**
1. Open the original invoice (must be saved, any status)
2. Click the **"Dobropis"** button in the header
3. A new draft is pre-filled with:
   - All items from the original, with **negated unit prices** (e.g. 1000 Kč → -1000 Kč)
   - A new invoice number with prefix **DD** (e.g. DD-2026-001)
   - A reference to the original invoice number
4. Review, adjust amounts if needed (partial credit)
5. Issue the credit note through the normal lifecycle

**The credit note is a separate invoice** that goes through the same lifecycle independently. The original invoice is not modified.

---

## `issued_at` vs `due_date`

| Field | Set when | Editable after issue |
|---|---|---|
| `issued_at` | Transition to Vystavena | ❌ Never |
| `due_date` | Any time in Koncept | ❌ Not after issue |
| `created_at` | Invoice first saved | ❌ Never |
| `updated_at` | Any save or status change | Automatic |

---

## Lifecycle in the database

The `invoices` table stores both the lifecycle state and the full invoice data:

```sql
invoices
  status          TEXT   -- 'draft'|'issued'|'sent'|'paid'|'overdue'|'cancelled'
  issued_at       TEXT   -- YYYY-MM-DD, set once on → issued
  due_date        TEXT   -- YYYY-MM-DD, denormalized for overdue queries
  total           REAL   -- grand total (denormalized, recalculated on save)
  paid_total      REAL   -- sum of payments (denormalized, updated on payment)
  data            TEXT   -- full JSON blob (all fields including items)
```

The `status` column is the single source of truth for lifecycle state. The `data` JSON blob also contains `_status` as a redundant field (used by the frontend).

---

## API endpoints for lifecycle management

| Method | Endpoint | Description |
|---|---|---|
| `PATCH` | `/api/invoices/{id}/status` | Transition to any valid next state |
| `POST` | `/api/invoices/mark-overdue` | Scan and flip overdue invoices |
| `POST` | `/api/invoices/{id}/payments` | Record a payment |
| `DELETE` | `/api/invoices/{id}/payments/{pid}` | Remove a payment |
| `POST` | `/api/invoices/{id}/credit-note` | Prepare a credit note draft |

**Status transition request body:**
```json
{ "status": "issued" }
```

Valid values: `draft`, `issued`, `sent`, `paid`, `overdue`, `cancelled`.

Attempting an invalid value returns `422 Unprocessable Entity`.

---

## Common scenarios

### Standard successful invoice
```
Koncept → Vystavena → Odesláno → Zaplaceno
```
Fill in the form → issue it → send to customer → customer pays → record payment → auto-flips to paid.

### Invoice not paid on time
```
Odesláno → [auto] Po splatnosti → Zaplaceno
```
Due date passes → server flags as overdue on startup → customer eventually pays → record payment.

### Cancelled before sending
```
Koncept → Stornováno
  or
Vystavena → Stornováno
```
Invoice was created by mistake or customer cancelled before receiving it.

### Partial reversal of a paid invoice
```
Original: Odesláno → Zaplaceno
Credit note: Koncept → Vystavena → Odesláno → Zaplaceno
```
Create a Dobropis for the amount to reverse. Issue and send it. Customer receives the credit.
