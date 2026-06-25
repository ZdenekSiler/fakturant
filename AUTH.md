# Authentication & User Isolation

## Overview

Fakturant uses session-cookie authentication with per-user invoice isolation. Each user registers with email + password, receives a signed session cookie, and can only access their own invoices.

**No JWT. No OAuth. No third-party auth service.** Just a signed cookie containing the user's database ID.

---

## Architecture

```
Browser                FastAPI                  SQLite
  │                       │                       │
  │── POST /auth/signup ──►                       │
  │   email, password      │                       │
  │                        │ hash password         │
  │                        │ (Argon2id)            │
  │                        │── INSERT users ──────►│
  │                        │◄── user_id = 42 ──────│
  │◄── 303 + Set-Cookie ───│                       │
  │    fakturant_session=  │                       │
  │    "42.timestamp.HMAC" │                       │
  │                        │                       │
  │── GET /api/invoices ──►│                       │
  │   Cookie: 42.ts.HMAC   │                       │
  │                        │ unsign cookie         │
  │                        │ user_id = 42          │
  │                        │── SELECT WHERE ───────►│
  │                        │   user_id = 42        │
  │◄── [ user 42's invoices]│◄──────────────────────│
```

---

## Signup Flow

**Endpoint:** `POST /auth/signup`  
**Page:** `GET /signup`

```
1. User submits: email, password, confirm
2. Validate:
     - email contains "@" and "."
     - password length ≥ 8
     - password == confirm
3. Hash password with Argon2id
4. INSERT INTO users (email, password_hash, created_at)
     - UNIQUE constraint on email (COLLATE NOCASE) → 409 on duplicate
5. On first-ever user: claim all legacy invoices (user_id = NULL → user_id = 1)
6. Set signed session cookie
7. Redirect to /
```

Registration can be disabled by setting `ALLOW_SIGNUP=false` in `.env` — useful after the initial user is created.

---

## Login Flow

**Endpoint:** `POST /auth/login`  
**Page:** `GET /login`

```
1. User submits: email, password
2. SELECT FROM users WHERE email = ? (case-insensitive)
3. If user not found:
     perform dummy Argon2 verify (prevents timing-based email enumeration)
     return 401
4. Verify: argon2.verify(stored_hash, submitted_password)
     If mismatch → return 401
5. Set signed session cookie with user_id
6. Redirect to /
```

Both "user not found" and "wrong password" return the same error message:  
**"Nesprávný e-mail nebo heslo."** — an attacker cannot tell which case applies.

---

## Session Cookie

Every authenticated request carries a cookie named `fakturant_session`.

### Contents

```
42.1748000000.HMACSig
│  │           │
│  │           └── HMAC-SHA1 signature (itsdangerous TimestampSigner)
│  └────────────── Unix timestamp of login
└───────────────── user_id (integer)
```

### Attributes

| Attribute | Value | Why |
|---|---|---|
| `HttpOnly` | true | JavaScript cannot read it — XSS-safe |
| `SameSite` | Lax | Blocks cross-site POST — CSRF-safe |
| `Secure` | true in prod | HTTPS only (Caddy provides TLS) |
| `Path` | / | Valid for the entire app |
| `Max-Age` | 30 days | Persistent across browser restarts |

### Signing

`SESSION_SECRET` (env var, min 32 chars) signs the cookie with HMAC via `itsdangerous.TimestampSigner`. If anyone tampers with the cookie value, the signature check fails and the cookie is rejected.

If `SESSION_SECRET` changes (e.g. on server restart without a persistent `.env`), all existing sessions are invalidated — users must log in again.

---

## Password Hashing

Passwords are hashed with **Argon2id** — the winner of the Password Hashing Competition (2015) and the 2024 OWASP recommendation.

### Parameters (defaults from argon2-cffi)

| Parameter | Value | Meaning |
|---|---|---|
| Algorithm | Argon2id | Hybrid: resistant to both GPU and side-channel attacks |
| Memory | 64 MB | Required RAM per hash attempt |
| Iterations | 3 | CPU cost multiplier |
| Parallelism | 4 | Threads |

### Stored format

```
$argon2id$v=19$m=65536,t=3,p=4$BASE64_SALT$BASE64_HASH
```

The salt is random per-user (embedded in the string). Two users with the same password produce different hashes.

### Why Argon2id beats alternatives

| Algorithm | Memory-hard | GPU-resistant | Recommended |
|---|---|---|---|
| MD5 / SHA-256 | No | No | Never |
| bcrypt | No | Somewhat | Acceptable |
| PBKDF2 | No | No | Acceptable |
| **Argon2id** | **Yes** | **Yes** | **Yes** |

At 64 MB per attempt, a GPU with 8 GB VRAM can only try ~125 passwords simultaneously — versus millions with MD5.

### One-way: no decryption

Argon2id is a **hash**, not encryption. There is no decryption step. On login, the submitted password is re-hashed with the same embedded salt and the two hashes are compared. The original password is never stored or recoverable.

---

## Multi-User Invoice Isolation

Every invoice has a `user_id` column:

```sql
SELECT * FROM invoices WHERE id = ? AND user_id = ?
```

If user B tries to access invoice 42 (which belongs to user A), the query returns no rows → `404 Not Found`. User B cannot read, modify, or delete user A's data even if they know the invoice ID.

The same isolation applies to: save, status transitions, payments, credit notes, delete, and sequence counters (each user gets their own FA-2026-001).

### Legacy data migration

Invoices created before the multi-user system had `user_id = NULL`. When the first user registers, all `NULL` invoices are automatically claimed:

```sql
UPDATE invoices SET user_id = 1 WHERE user_id IS NULL
```

Subsequent users start with an empty namespace.

---

## Protected vs Public Routes

| Route | Auth required | Reason |
|---|---|---|
| `GET /` | No | Invoice form is public |
| `POST /preview` | No | Live preview needs no account |
| `POST /validate` | No | Czech law check is public |
| `POST /generate-pdf` | No | PDF download needs no account |
| `GET /api/ares/*` | No | Company registry lookup is public |
| `GET /api/sequence/*` | No | Invoice number prefill is public |
| `GET /health` | No | Infrastructure |
| `GET /login` `POST /auth/*` | No | Auth routes themselves |
| **`/api/invoices/*`** | **Yes** | All invoice data is per-user |

The protection is enforced in a single Starlette middleware — one place, not per-route:

```python
async def auth_middleware(request, call_next):
    request.state.user_id = get_user_id(request)   # None if unauthenticated

    if not request.url.path.startswith("/api/invoices"):
        return await call_next(request)              # public — skip

    if request.state.user_id is not None:
        return await call_next(request)              # authenticated — allow

    return JSONResponse({"detail": "Not authenticated"}, 401)  # blocked
```

---

## Logout

**Endpoint:** `POST /auth/logout`

Deletes the `fakturant_session` cookie from the browser. The server stores no session state, so there is nothing to invalidate server-side. If you need to force all sessions to expire (e.g. after a security incident), rotate `SESSION_SECRET` in `.env` and restart the server — all cookies become invalid immediately.

---

## Configuration

| Env var | Required | Default | Description |
|---|---|---|---|
| `SESSION_SECRET` | Yes | — | Min 32 chars. Signs session cookies. Rotate to invalidate all sessions. |
| `ALLOW_SIGNUP` | No | `true` | Set `false` to lock registration after initial setup. |

Generate a secure secret:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Files

| File | Purpose |
|---|---|
| `backend/auth.py` | Middleware, cookie helpers, password hashing, login/signup/logout routes |
| `backend/services/db.py` | `create_user`, `get_user_by_email`, `get_user_by_id` |
| `backend/services/queries.py` | `USER_INSERT`, `USER_GET_BY_EMAIL`, `USER_GET_BY_ID` |
| `backend/templates/login.html` | Login form (email + password) |
| `backend/templates/signup.html` | Registration form |
| `backend/main.py` | `Depends(current_user_id)` on all protected routes |

---

## Security checklist

- [x] Passwords hashed with Argon2id (not stored plain)
- [x] Session cookie is HttpOnly (XSS-safe)
- [x] Session cookie is SameSite=Lax (CSRF-safe for form-based logout)
- [x] Session cookie is Secure in production (HTTPS only via Caddy)
- [x] Timing-safe login (dummy verify prevents email enumeration)
- [x] Invoice isolation enforced at SQL level (`WHERE user_id = ?`)
- [x] `SESSION_SECRET` length validated at startup
- [x] Duplicate email returns 409 (SQLite UNIQUE constraint)
- [ ] Rate limiting on `/auth/login` (add nginx `limit_req` — see DEPLOYMENT.md)
- [ ] Password reset via email (not yet implemented)
- [ ] `__Host-` cookie prefix (one-line change in `auth.py` for extra hardening)
