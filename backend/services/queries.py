"""
services/queries.py — All SQL statements for Fakturant.

One named constant per query. Sections mirror the structure of db.py.
No imports, no logic — safe to read in isolation as a full SQL reference.
"""

# ── Schema ────────────────────────────────────────────────────────────────────

DDL_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

CREATE TABLE IF NOT EXISTS invoices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(id),
    invoice_number  TEXT    NOT NULL DEFAULT '',
    doc_type        TEXT    NOT NULL DEFAULT 'invoice',
    status          TEXT    NOT NULL DEFAULT 'draft',
    credit_note_for INTEGER REFERENCES invoices(id) ON DELETE SET NULL,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    issued_at       TEXT,
    due_date        TEXT,
    total           REAL    NOT NULL DEFAULT 0,
    paid_total      REAL    NOT NULL DEFAULT 0,
    data            TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id  INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    paid_on     TEXT    NOT NULL,
    amount      REAL    NOT NULL,
    note        TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sequences (
    user_id     INTEGER NOT NULL DEFAULT 0,
    year        INTEGER NOT NULL,
    prefix      TEXT    NOT NULL,
    last_seq    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, year, prefix)
);

CREATE INDEX IF NOT EXISTS idx_invoices_updated  ON invoices (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_invoices_user     ON invoices (user_id);
CREATE INDEX IF NOT EXISTS idx_invoices_number   ON invoices (invoice_number);
CREATE INDEX IF NOT EXISTS idx_invoices_status   ON invoices (status);
CREATE INDEX IF NOT EXISTS idx_invoices_due      ON invoices (due_date);
CREATE INDEX IF NOT EXISTS idx_payments_invoice  ON payments (invoice_id);

CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL DEFAULT '',
    ico        TEXT NOT NULL DEFAULT '',
    dic        TEXT NOT NULL DEFAULT '',
    address    TEXT NOT NULL DEFAULT '',
    email      TEXT NOT NULL DEFAULT '',
    phone      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contacts_user ON contacts (user_id);
"""

# Idempotent ALTER TABLE migrations for databases created before new columns existed.
# Each statement is run in a try/except so duplicate-column errors are silently skipped.
DDL_MIGRATIONS: list[str] = [
    "ALTER TABLE invoices ADD COLUMN doc_type TEXT NOT NULL DEFAULT 'invoice'",
    "ALTER TABLE invoices ADD COLUMN credit_note_for INTEGER",
    "ALTER TABLE invoices ADD COLUMN issued_at TEXT",
    "ALTER TABLE invoices ADD COLUMN due_date TEXT",
    "ALTER TABLE invoices ADD COLUMN total REAL NOT NULL DEFAULT 0",
    "ALTER TABLE invoices ADD COLUMN paid_total REAL NOT NULL DEFAULT 0",
    "ALTER TABLE invoices ADD COLUMN user_id INTEGER REFERENCES users(id)",
    "ALTER TABLE sequences ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0",
    # Email verification & password reset — existing users start as verified (DEFAULT 1)
    "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE users ADD COLUMN verification_token TEXT",
    "ALTER TABLE users ADD COLUMN reset_token TEXT",
    "ALTER TABLE users ADD COLUMN reset_token_expires TEXT",
    "ALTER TABLE users ADD COLUMN profile TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE invoices ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'",
]

# Sequences table rebuild — adds user_id to the composite PK.
# SQLite cannot drop/change a PRIMARY KEY, so we recreate the table.
# Wrapped in IF NOT EXISTS so it is idempotent.
DDL_SEQ_REBUILD = """
CREATE TABLE IF NOT EXISTS sequences_v2 (
    user_id     INTEGER NOT NULL DEFAULT 0,
    year        INTEGER NOT NULL,
    prefix      TEXT    NOT NULL,
    last_seq    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, year, prefix)
);
INSERT OR IGNORE INTO sequences_v2 (user_id, year, prefix, last_seq)
    SELECT COALESCE(user_id, 0), year, prefix, last_seq FROM sequences;
DROP TABLE sequences;
ALTER TABLE sequences_v2 RENAME TO sequences;
"""

# ── Users ─────────────────────────────────────────────────────────────────────

USER_INSERT = (
    "INSERT INTO users (email, password_hash, created_at) VALUES (?,?,?)"
)

USER_GET_BY_EMAIL = (
    "SELECT id, email, password_hash, email_verified FROM users WHERE email=?"
)

USER_GET_BY_ID = (
    "SELECT id, email, email_verified FROM users WHERE id=?"
)

# ── Email verification ────────────────────────────────────────────────────────

USER_SET_VERIFICATION_TOKEN = (
    "UPDATE users SET verification_token=?, email_verified=0 WHERE id=?"
)

USER_GET_BY_VERIFICATION_TOKEN = (
    "SELECT id, email, email_verified FROM users WHERE verification_token=?"
)

USER_VERIFY_EMAIL = (
    "UPDATE users SET email_verified=1, verification_token=NULL WHERE id=?"
)

# ── Password reset ────────────────────────────────────────────────────────────

USER_SET_RESET_TOKEN = (
    "UPDATE users SET reset_token=?, reset_token_expires=? WHERE id=?"
)

USER_GET_BY_RESET_TOKEN = (
    "SELECT id, email, reset_token_expires FROM users WHERE reset_token=?"
)

USER_RESET_PASSWORD = (
    "UPDATE users SET password_hash=?, reset_token=NULL, reset_token_expires=NULL WHERE id=?"
)

# ── Sequences ─────────────────────────────────────────────────────────────────

SEQ_INSERT_OR_IGNORE = (
    "INSERT INTO sequences(user_id, year, prefix, last_seq) VALUES(?,?,?,0) "
    "ON CONFLICT(user_id, year, prefix) DO NOTHING"
)

SEQ_INCREMENT = (
    "UPDATE sequences SET last_seq = last_seq + 1 "
    "WHERE user_id=? AND year=? AND prefix=?"
)

SEQ_SELECT = (
    "SELECT last_seq FROM sequences WHERE user_id=? AND year=? AND prefix=?"
)

# ── Invoices — listing ────────────────────────────────────────────────────────

_INVOICE_LIST_COLUMNS = """
    SELECT id, invoice_number, doc_type, status, credit_note_for,
           created_at, updated_at, issued_at, due_date, total, paid_total, tags
    FROM invoices
"""

INVOICE_LIST_ALL = (
    _INVOICE_LIST_COLUMNS
    + "WHERE user_id=? ORDER BY updated_at DESC LIMIT ? OFFSET ?"
)

INVOICE_LIST_BY_DOCTYPE = (
    _INVOICE_LIST_COLUMNS
    + "WHERE user_id=? AND doc_type=? ORDER BY updated_at DESC LIMIT ? OFFSET ?"
)

# ── Invoices — single row ─────────────────────────────────────────────────────

INVOICE_GET = "SELECT * FROM invoices WHERE id=? AND user_id=?"

INVOICE_GET_TOTAL = "SELECT total FROM invoices WHERE id=? AND user_id=?"

# ── Invoices — write ──────────────────────────────────────────────────────────

INVOICE_INSERT = """
    INSERT INTO invoices
       (user_id, invoice_number, doc_type, status, credit_note_for,
        created_at, updated_at, due_date, total, paid_total, tags, data)
    VALUES (?,?,?,?,?,?,?,?,?,0,?,?)
"""

INVOICE_UPDATE = """
    UPDATE invoices
    SET invoice_number=?, doc_type=?, status=?, due_date=?,
        updated_at=?, total=?, tags=?, data=?
    WHERE id=? AND user_id=?
"""

INVOICE_DELETE = "DELETE FROM invoices WHERE id=? AND user_id=?"

# ── Invoices — status transitions ─────────────────────────────────────────────

STATUS_UPDATE_ISSUED = (
    "UPDATE invoices SET status=?, issued_at=?, updated_at=? WHERE id=? AND user_id=?"
)

STATUS_UPDATE = (
    "UPDATE invoices SET status=?, updated_at=? WHERE id=? AND user_id=?"
)

STATUS_MARK_OVERDUE = (
    "UPDATE invoices SET status='overdue', updated_at=? "
    "WHERE status='sent' AND due_date IS NOT NULL AND due_date < ?"
)

# ── Invoices — payment denormalisation ────────────────────────────────────────

INVOICE_UPDATE_PAID_STATUS = (
    "UPDATE invoices SET paid_total=?, status=?, updated_at=? WHERE id=?"
)

INVOICE_UPDATE_PAID_TOTAL = (
    "UPDATE invoices SET paid_total=?, updated_at=? WHERE id=?"
)

# ── Payments ──────────────────────────────────────────────────────────────────

PAYMENT_INSERT = (
    "INSERT INTO payments (invoice_id, paid_on, amount, note) VALUES (?,?,?,?)"
)

PAYMENT_SUM = (
    "SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id=?"
)

PAYMENT_SELECT_FOR_INVOICE = (
    "SELECT * FROM payments WHERE invoice_id=? ORDER BY paid_on ASC"
)

PAYMENT_DELETE = (
    "DELETE FROM payments WHERE id=? AND invoice_id=?"
)

# ── Invoice number uniqueness ──────────────────────────────────────────────────

INVOICE_CHECK_DUPLICATE = (
    "SELECT id FROM invoices WHERE invoice_number=? AND user_id=?"
)

INVOICE_CHECK_DUPLICATE_EXCL = (
    "SELECT id FROM invoices WHERE invoice_number=? AND user_id=? AND id != ?"
)

# ── Sequence advance ───────────────────────────────────────────────────────────

SEQ_ADVANCE = (
    "INSERT INTO sequences (user_id, year, prefix, last_seq) VALUES (?,?,?,?) "
    "ON CONFLICT(user_id, year, prefix) DO UPDATE SET last_seq = MAX(last_seq, excluded.last_seq)"
)

# ── User profile ───────────────────────────────────────────────────────────────

PROFILE_GET = "SELECT profile FROM users WHERE id=?"

PROFILE_SAVE = "UPDATE users SET profile=? WHERE id=?"

# ── Contacts ──────────────────────────────────────────────────────────────────

CONTACT_LIST = (
    "SELECT id, name, ico, dic, address, email, phone, created_at, updated_at "
    "FROM contacts WHERE user_id=? ORDER BY name ASC"
)

CONTACT_INSERT = (
    "INSERT INTO contacts (user_id, name, ico, dic, address, email, phone, created_at, updated_at) "
    "VALUES (?,?,?,?,?,?,?,?,?)"
)

CONTACT_UPDATE = (
    "UPDATE contacts SET name=?, ico=?, dic=?, address=?, email=?, phone=?, updated_at=? "
    "WHERE id=? AND user_id=?"
)

CONTACT_DELETE = "DELETE FROM contacts WHERE id=? AND user_id=?"

CONTACT_GET = (
    "SELECT id, name, ico, dic, address, email, phone, created_at, updated_at "
    "FROM contacts WHERE id=? AND user_id=?"
)
