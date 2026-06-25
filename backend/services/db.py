"""
services/db.py — SQLite persistence for Fakturant.

Schema
──────
users
    id            INTEGER  PK AUTOINCREMENT
    email         TEXT     UNIQUE (case-insensitive)
    password_hash TEXT     Argon2id hash
    created_at    TEXT     ISO-8601 UTC

invoices
    id              INTEGER  PK AUTOINCREMENT
    user_id         INTEGER  FK → users.id  (NULL = unclaimed legacy)
    invoice_number  TEXT     denormalised for quick listing
    doc_type        TEXT     'invoice' | 'credit_note'
    status          TEXT     'draft'|'issued'|'sent'|'paid'|'overdue'|'cancelled'
    credit_note_for INTEGER  FK → invoices.id  (credit notes only)
    created_at      TEXT     ISO-8601 UTC
    updated_at      TEXT     ISO-8601 UTC
    issued_at       TEXT     date string YYYY-MM-DD, set when status→issued
    due_date        TEXT     YYYY-MM-DD  (denormalised for overdue queries)
    total           REAL     grand total incl. VAT (denormalised)
    paid_total      REAL     sum of all payments (denormalised, updated on payment)
    data            TEXT     full InvoiceData JSON blob

payments
    id              INTEGER  PK AUTOINCREMENT
    invoice_id      INTEGER  FK → invoices.id
    paid_on         TEXT     YYYY-MM-DD
    amount          REAL
    note            TEXT

sequences
    user_id         INTEGER  part of composite PK (0 = legacy/test)
    year            INTEGER  part of composite PK
    prefix          TEXT     e.g. 'FA'  (invoices) or 'DD' (credit notes)
    last_seq        INTEGER  last used sequential number for that user+year+prefix
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from services.queries import (
    CONTACT_DELETE,
    CONTACT_GET,
    CONTACT_INSERT,
    CONTACT_LIST,
    CONTACT_UPDATE,
    DDL_CREATE_SCHEMA,
    INVOICE_CHECK_DUPLICATE,
    INVOICE_CHECK_DUPLICATE_EXCL,
    SEQ_ADVANCE,
    DDL_MIGRATIONS,
    DDL_SEQ_REBUILD,
    INVOICE_DELETE,
    INVOICE_GET,
    INVOICE_GET_TOTAL,
    INVOICE_INSERT,
    INVOICE_LIST_ALL,
    INVOICE_LIST_BY_DOCTYPE,
    INVOICE_UPDATE,
    INVOICE_UPDATE_PAID_STATUS,
    INVOICE_UPDATE_PAID_TOTAL,
    PAYMENT_DELETE,
    PAYMENT_INSERT,
    PAYMENT_SELECT_FOR_INVOICE,
    PAYMENT_SUM,
    PROFILE_GET,
    PROFILE_SAVE,
    SEQ_INCREMENT,
    SEQ_INSERT_OR_IGNORE,
    SEQ_SELECT,
    STATUS_MARK_OVERDUE,
    STATUS_UPDATE,
    STATUS_UPDATE_ISSUED,
    USER_GET_BY_EMAIL,
    USER_GET_BY_ID,
    USER_GET_BY_RESET_TOKEN,
    USER_GET_BY_VERIFICATION_TOKEN,
    USER_INSERT,
    USER_RESET_PASSWORD,
    USER_SET_RESET_TOKEN,
    USER_SET_VERIFICATION_TOKEN,
    USER_VERIFY_EMAIL,
)

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).parent.parent.parent / "data" / "fakturant.db"


def _db_path() -> Path:
    return Path(os.environ.get("DB_PATH", str(_DEFAULT_DB)))


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def init_db() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.executescript(DDL_CREATE_SCHEMA)
        for sql in DDL_MIGRATIONS:
            try:
                await db.execute(sql)
            except aiosqlite.OperationalError:
                pass  # already applied
            except Exception:
                logger.exception("Unexpected migration error: %s", sql[:80])
        # Rebuild sequences table to include user_id in the PK (idempotent)
        try:
            await db.executescript(DDL_SEQ_REBUILD)
        except Exception:
            pass
        await _claim_legacy_invoices(db)
        await db.commit()
    logger.info("Database ready: %s", _db_path())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return date.today().isoformat()


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    d = dict(row)
    d["data"] = json.loads(d["data"])
    return d


def _parse_number(number: str) -> tuple[str, int, int] | None:
    """
    Parse an invoice number like 'FA-2025-042' or 'DD-2025-001'.
    Returns (prefix, year, seq) or None if not parseable.
    """
    m = re.match(r"^([A-Z]+)-(\d{4})-(\d+)$", number.strip().upper())
    if not m:
        return None
    return m.group(1), int(m.group(2)), int(m.group(3))


async def _claim_legacy_invoices(db: aiosqlite.Connection) -> None:
    """Assign user_id=NULL invoices to the first registered user (if one exists)."""
    async with db.execute("SELECT id FROM users ORDER BY id LIMIT 1") as cur:
        row = await cur.fetchone()
    if row is None:
        return
    first_user_id = row[0]
    await db.execute(
        "UPDATE invoices SET user_id=? WHERE user_id IS NULL",
        (first_user_id,),
    )


# ── Users ─────────────────────────────────────────────────────────────────────

async def create_user(email: str, password_hash: str) -> int | None:
    """
    Insert a new user. Returns the new user_id, or None on duplicate email.
    On first user creation, claims all unclaimed (NULL user_id) invoices.
    """
    now = _now()
    async with aiosqlite.connect(_db_path()) as db:
        try:
            cur = await db.execute(USER_INSERT, (email, password_hash, now))
            user_id = cur.lastrowid
        except aiosqlite.IntegrityError:
            return None
        await _claim_legacy_invoices(db)
        await db.commit()
    return user_id


async def get_user_by_email(email: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(USER_GET_BY_EMAIL, (email,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(USER_GET_BY_ID, (user_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


# ── Email verification ────────────────────────────────────────────────────────

async def set_verification_token(user_id: int, token: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(USER_SET_VERIFICATION_TOKEN, (token, user_id))
        await db.commit()


async def get_user_by_verification_token(token: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(USER_GET_BY_VERIFICATION_TOKEN, (token,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def verify_email(user_id: int) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(USER_VERIFY_EMAIL, (user_id,))
        await db.commit()


# ── Password reset ────────────────────────────────────────────────────────────

async def set_reset_token(user_id: int, token: str, expires_at: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(USER_SET_RESET_TOKEN, (token, expires_at, user_id))
        await db.commit()


async def get_user_by_reset_token(token: str) -> dict[str, Any] | None:
    """Return user if token exists and has not expired."""
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(USER_GET_BY_RESET_TOKEN, (token,)) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    expires = row["reset_token_expires"]
    if not expires or _now() > expires:
        return None
    return dict(row)


async def reset_password(user_id: int, new_hash: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(USER_RESET_PASSWORD, (new_hash, user_id))
        await db.commit()


# ── User profile ──────────────────────────────────────────────────────────────

async def get_user_profile(user_id: int) -> dict[str, Any]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(PROFILE_GET, (user_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return {}
    return json.loads(row["profile"] or "{}")


async def save_user_profile(user_id: int, profile: dict[str, Any]) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(PROFILE_SAVE, (json.dumps(profile, ensure_ascii=False), user_id))
        await db.commit()


# ── Contacts ──────────────────────────────────────────────────────────────────

async def list_contacts(user_id: int) -> list[dict[str, Any]]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(CONTACT_LIST, (user_id,)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def upsert_contact(
    user_id: int,
    data: dict[str, Any],
    contact_id: int | None = None,
) -> dict[str, Any]:
    now = _now()
    name    = data.get("name", "")
    ico     = data.get("ico", "")
    dic     = data.get("dic", "")
    address = data.get("address", "")
    email   = data.get("email", "")
    phone   = data.get("phone", "")

    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        if contact_id is None:
            cur = await db.execute(
                CONTACT_INSERT, (user_id, name, ico, dic, address, email, phone, now, now)
            )
            contact_id = cur.lastrowid
        else:
            await db.execute(
                CONTACT_UPDATE, (name, ico, dic, address, email, phone, now, contact_id, user_id)
            )
        await db.commit()
        async with db.execute(CONTACT_GET, (contact_id, user_id)) as cur:
            row = await cur.fetchone()
    if row is None:
        raise ValueError(f"Contact {contact_id} not found after save")
    return dict(row)


async def delete_contact(user_id: int, contact_id: int) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(CONTACT_DELETE, (contact_id, user_id))
        await db.commit()


# ── Sequence ──────────────────────────────────────────────────────────────────

async def next_number(
    prefix: str = "FA",
    year: int | None = None,
    user_id: int = 0,
) -> str:
    """Atomically increment the counter and return the next invoice number."""
    y = year or date.today().year
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(SEQ_INSERT_OR_IGNORE, (user_id, y, prefix))
        await db.execute(SEQ_INCREMENT, (user_id, y, prefix))
        async with db.execute(SEQ_SELECT, (user_id, y, prefix)) as cur:
            row = await cur.fetchone()
        await db.commit()
    return f"{prefix}-{y}-{row[0]:03d}"


async def peek_next_number(
    prefix: str = "FA",
    year: int | None = None,
    user_id: int = 0,
) -> str:
    """Return what the next number WOULD be without incrementing."""
    y = year or date.today().year
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(SEQ_SELECT, (user_id, y, prefix)) as cur:
            row = await cur.fetchone()
    seq = (row[0] if row else 0) + 1
    return f"{prefix}-{y}-{seq:03d}"


async def check_sequence_gap(number: str, user_id: int = 0) -> dict[str, Any]:
    """Check whether a given invoice number would create a sequence gap."""
    parsed = _parse_number(number)
    if parsed is None:
        return {"ok": True, "expected": None, "provided": number, "gap": 0}
    prefix, year, seq = parsed
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(SEQ_SELECT, (user_id, year, prefix)) as cur:
            row = await cur.fetchone()
    last = row[0] if row else 0
    expected = last + 1
    gap = seq - expected
    return {
        "ok": gap == 0,
        "expected": f"{prefix}-{year}-{expected:03d}",
        "provided": number,
        "gap": gap,
    }


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def list_invoices(
    limit: int = 100,
    offset: int = 0,
    doc_type: str | None = None,
    user_id: int = 0,
) -> list[dict[str, Any]]:
    """Summary rows (no data blob), ordered by updated_at DESC."""
    if doc_type:
        sql    = INVOICE_LIST_BY_DOCTYPE
        params: list[Any] = [user_id, doc_type, limit, offset]
    else:
        sql    = INVOICE_LIST_ALL
        params = [user_id, limit, offset]
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
    result = []
    for r in rows:
        row = dict(r)
        row["tags"] = json.loads(row.get("tags") or "[]")
        result.append(row)
    return result


async def get_invoice(invoice_id: int, user_id: int = 0) -> dict[str, Any] | None:
    """Full invoice row including data blob and payments list."""
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(INVOICE_GET, (invoice_id, user_id)) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        result = _row_to_dict(row)
        async with db.execute(PAYMENT_SELECT_FOR_INVOICE, (invoice_id,)) as cur:
            prows = await cur.fetchall()
        result["payments"] = [dict(p) for p in prows]
    return result


async def save_invoice(
    data_dict: dict[str, Any],
    invoice_id: int | None = None,
    doc_type: str = "invoice",
    credit_note_for: int | None = None,
    user_id: int = 0,
) -> dict[str, Any]:
    """Upsert an invoice. Does NOT bump the sequence counter."""
    now    = _now()
    number = data_dict.get("invoice_number", "")
    status = data_dict.get("_status", "draft")
    due    = data_dict.get("due_date", "") or None
    total  = _compute_total(data_dict)
    tags   = json.dumps(data_dict.get("tags", []), ensure_ascii=False)
    blob   = json.dumps(data_dict, ensure_ascii=False)

    async with aiosqlite.connect(_db_path()) as db:
        if invoice_id is None:
            cur = await db.execute(
                INVOICE_INSERT,
                (user_id, number, doc_type, status, credit_note_for, now, now, due, total, tags, blob),
            )
            await db.commit()
            row_id = cur.lastrowid
        else:
            await db.execute(
                INVOICE_UPDATE,
                (number, doc_type, status, due, now, total, tags, blob, invoice_id, user_id),
            )
            await db.commit()
            row_id = invoice_id

    result = await get_invoice(row_id, user_id)
    if result is None:
        raise RuntimeError(f"Invoice {row_id} not found after save")
    return result


async def update_status(
    invoice_id: int,
    new_status: str,
    user_id: int = 0,
) -> dict[str, Any]:
    """Transition invoice to a new status. Sets issued_at when transitioning to 'issued'."""
    valid = {"draft", "issued", "sent", "paid", "overdue", "cancelled"}
    if new_status not in valid:
        raise ValueError(f"Invalid status: {new_status}")

    now = _now()
    async with aiosqlite.connect(_db_path()) as db:
        if new_status == "issued":
            await db.execute(STATUS_UPDATE_ISSUED, (new_status, _today(), now, invoice_id, user_id))
        else:
            await db.execute(STATUS_UPDATE, (new_status, now, invoice_id, user_id))
        await db.commit()

    result = await get_invoice(invoice_id, user_id)
    if result is None:
        raise ValueError(f"Invoice {invoice_id} not found after status update")
    return result


async def mark_overdue() -> int:
    """Scan all 'sent' invoices past their due_date and flip them to 'overdue'."""
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(STATUS_MARK_OVERDUE, (_now(), _today()))
        await db.commit()
        return cur.rowcount


# ── Payments ──────────────────────────────────────────────────────────────────

async def add_payment(
    invoice_id: int,
    amount: float,
    paid_on: str,
    note: str = "",
) -> dict[str, Any]:
    """Record a payment. Recalculates paid_total; auto-transitions to 'paid' when settled."""
    now = _now()
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute("SELECT user_id, status FROM invoices WHERE id=?", (invoice_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            raise ValueError(f"Invoice {invoice_id} not found")
        user_id, status = row[0] or 0, row[1]
        if status in ("draft", "cancelled"):
            raise ValueError(f"Cannot record payment for a {status} invoice")

        await db.execute(PAYMENT_INSERT, (invoice_id, paid_on, amount, note))
        # Atomic recalc: avoids TOCTOU race when concurrent payments hit the same invoice
        await db.execute(
            """UPDATE invoices
               SET paid_total = (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE invoice_id = ?),
                   updated_at = ?,
                   status = CASE
                       WHEN (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE invoice_id = ?)
                            >= total - 1.0
                            AND status NOT IN ('draft', 'cancelled')
                       THEN 'paid'
                       ELSE status
                   END
               WHERE id = ?""",
            (invoice_id, now, invoice_id, invoice_id),
        )
        await db.commit()

    result = await get_invoice(invoice_id, user_id)
    if result is None:
        raise ValueError(f"Invoice {invoice_id} not found after payment")
    return result


async def delete_payment(payment_id: int, invoice_id: int) -> dict[str, Any]:
    """Remove a payment and recalculate paid_total."""
    now = _now()
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute("SELECT user_id FROM invoices WHERE id=?", (invoice_id,)) as cur:
            row = await cur.fetchone()
        user_id = (row[0] or 0) if row else 0

        await db.execute(PAYMENT_DELETE, (payment_id, invoice_id))
        # Atomic recalc after deletion
        await db.execute(
            """UPDATE invoices
               SET paid_total = (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE invoice_id = ?),
                   updated_at = ?
               WHERE id = ?""",
            (invoice_id, now, invoice_id),
        )
        await db.commit()

    result = await get_invoice(invoice_id, user_id)
    if result is None:
        raise ValueError(f"Invoice {invoice_id} not found")
    return result


# ── Credit notes ──────────────────────────────────────────────────────────────

async def create_credit_note(original_id: int, user_id: int = 0) -> dict[str, Any]:
    """Clone an invoice as a credit note draft with negated item prices."""
    original = await get_invoice(original_id, user_id)
    if original is None:
        raise ValueError(f"Invoice {original_id} not found")

    data = dict(original["data"])
    data["items"] = [
        {**item, "unit_price": -abs(item["unit_price"])}
        for item in data.get("items", [])
    ]
    cn_number = await peek_next_number("DD", user_id=user_id)
    data["invoice_number"] = cn_number
    data["_status"] = "draft"
    data["_credit_note_ref"] = original["invoice_number"]

    return {
        "data": data,
        "doc_type": "credit_note",
        "credit_note_for": original_id,
        "original_number": original["invoice_number"],
        "suggested_number": cn_number,
    }


# ── Delete ────────────────────────────────────────────────────────────────────

async def delete_invoice(invoice_id: int, user_id: int = 0) -> bool:
    async with aiosqlite.connect(_db_path()) as db:
        # Read the invoice before deleting so we can roll back the sequence
        # if it was a draft (status='draft') and was the last committed number.
        async with db.execute(
            "SELECT invoice_number, status FROM invoices WHERE id=? AND user_id=?",
            (invoice_id, user_id),
        ) as cur:
            row = await cur.fetchone()

        result = await db.execute(INVOICE_DELETE, (invoice_id, user_id))
        deleted = result.rowcount > 0

        if deleted and row is not None:
            number, status = row[0], row[1]
            # Only roll back for drafts — issued/sent invoices were used on real documents.
            if status == "draft":
                parsed = _parse_number(number)
                if parsed is not None:
                    prefix, year, seq = parsed
                    async with db.execute(SEQ_SELECT, (user_id, year, prefix)) as cur:
                        seq_row = await cur.fetchone()
                    if seq_row and seq_row[0] == seq:
                        await db.execute(
                            "UPDATE sequences SET last_seq = last_seq - 1 "
                            "WHERE user_id=? AND year=? AND prefix=? AND last_seq=?",
                            (user_id, year, prefix, seq),
                        )

        await db.commit()
        return deleted


# ── Invoice number uniqueness ──────────────────────────────────────────────────

async def check_duplicate_number(
    number: str, user_id: int, exclude_id: int | None = None
) -> int | None:
    """Return the id of a conflicting invoice, or None if the number is free."""
    sql    = INVOICE_CHECK_DUPLICATE_EXCL if exclude_id is not None else INVOICE_CHECK_DUPLICATE
    params = (number, user_id, exclude_id) if exclude_id is not None else (number, user_id)
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def advance_sequence(number: str, user_id: int) -> None:
    """Advance the sequence counter to at least `seq` from a parsed invoice number.

    Safe to call on every save — uses MAX so it never decrements the counter.
    """
    parsed = _parse_number(number)
    if parsed is None:
        return
    prefix, year, seq = parsed
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(SEQ_ADVANCE, (user_id, year, prefix, seq))
        await db.commit()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _compute_total(data: dict[str, Any]) -> float:
    """Compute grand total from the data blob (mirrors models.py logic).
    Non-VAT payers: total = sum of qty × unit_price (no VAT applied).
    VAT payers: total = sum of qty × unit_price × (1 + vat_rate/100).
    """
    vat_payer = data.get("supplier", {}).get("vat_payer", False)
    total = 0.0
    for item in data.get("items", []):
        qty   = float(item.get("quantity", 0))
        price = float(item.get("unit_price", 0))
        base  = round(qty * price, 2)
        if vat_payer:
            vat    = float(item.get("vat_rate", 0))
            total += round(base + round(base * vat / 100, 2), 2)
        else:
            total += base
    return round(total, 2)
