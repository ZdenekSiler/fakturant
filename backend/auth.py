"""
auth.py — Multi-user session cookie authentication for Fakturant.

Each user has an account (email + Argon2id password hash).
Session cookie contains the signed user_id integer.

Protected: /api/invoices/*  (all invoice CRUD, status, payments)
Public:    everything else  (form, preview, generate-pdf, ares, sequences)

Env vars:
  SESSION_SECRET  — required, ≥32 chars, used to sign cookies
  ALLOW_SIGNUP    — optional, default "true"; set "false" to lock registration
  RESEND_API_KEY  — optional; if unset, emails are logged to console instead
  RESEND_FROM     — optional; sender address
  APP_URL         — optional; public base URL for links in emails (default http://localhost)
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

from config import read_secret

SESSION_SECRET = read_secret("fakturant_session_secret", "SESSION_SECRET")
ALLOW_SIGNUP   = os.environ.get("ALLOW_SIGNUP", "true").lower() == "true"
COOKIE_NAME    = "fakturant_session"
COOKIE_MAX_AGE = 86400 * 30
_IS_PROD = os.environ.get("ALLOWED_ORIGIN", "*") != "*"


def _email_enabled() -> bool:
    return bool(os.environ.get("RESEND_API_KEY", ""))


def _app_url() -> str:
    return os.environ.get("APP_URL", "http://localhost").rstrip("/")

_PROTECTED_PREFIXES = ("/api/invoices",)

# ── Password hashing (Argon2id) ───────────────────────────────────────────────

_ph = PasswordHasher()   # defaults: time=3, memory=64MB, parallelism=4, type=Argon2id


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ── Cookie helpers ────────────────────────────────────────────────────────────

def _signer():
    from itsdangerous import TimestampSigner
    return TimestampSigner(SESSION_SECRET, salt="fakturant-session")


def set_session_cookie(response: Response, user_id: int) -> None:
    token = _signer().sign(str(user_id)).decode()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        path="/",
        httponly=True,
        secure=_IS_PROD,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def get_user_id(request: Request) -> int | None:
    """Verify session cookie and return user_id, or None if missing/invalid."""
    from itsdangerous import BadSignature, SignatureExpired
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        return None
    try:
        raw = _signer().unsign(token, max_age=COOKIE_MAX_AGE)
        return int(raw)
    except (BadSignature, SignatureExpired, ValueError):
        return None


# ── FastAPI dependency ────────────────────────────────────────────────────────

def current_user_id(request: Request) -> int:
    """Inject authenticated user_id into route handlers. Raises 401 if not logged in."""
    uid = getattr(request.state, "user_id", None)
    if uid is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return uid


# ── Middleware ────────────────────────────────────────────────────────────────

def make_auth_middleware():
    async def auth_middleware(request: Request, call_next):
        request.state.user_id = get_user_id(request)

        path = request.url.path

        # Pass through all non-protected paths immediately
        if not any(path.startswith(p) for p in _PROTECTED_PREFIXES):
            return await call_next(request)

        # Unauthenticated — redirect or 401
        if request.state.user_id is None:
            wants_json = (
                path.startswith("/api/")
                or "application/json" in request.headers.get("accept", "")
            )
            if wants_json:
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse(url="/login", status_code=302)

        # Authenticated — check email verification (only when email is enabled)
        if _email_enabled():
            from services.db import get_user_by_id
            user = await get_user_by_id(request.state.user_id)
            if user and not user.get("email_verified", 1):
                return JSONResponse({"detail": "email_not_verified"}, status_code=403)

        return await call_next(request)

    return auth_middleware


# ── Template helpers ──────────────────────────────────────────────────────────

def _render(request: Request, template: str, **ctx) -> str:
    return request.app.state.jinja.get_template(template).render(**ctx)


# ── Routes ────────────────────────────────────────────────────────────────────

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    if get_user_id(request) is not None:
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(_render(request, "login.html", error="", allow_signup=ALLOW_SIGNUP))


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request) -> HTMLResponse:
    if not ALLOW_SIGNUP:
        return RedirectResponse("/login", status_code=302)
    if get_user_id(request) is not None:
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(_render(request, "signup.html", error=""))


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/auth/login")
async def auth_login(
    request: Request,
    email:    str = Form(...),
    password: str = Form(...),
):
    from services.db import get_user_by_email

    email = email.strip().lower()
    user  = await get_user_by_email(email)

    if user is None:
        _ph.verify(_ph.hash("dummy"), "dummy")  # timing-safe: equalize unknown vs wrong-pw
        logger.warning("Login failed (unknown email) from %s", request.client.host)
        return HTMLResponse(
            _render(request, "login.html", error="Nesprávný e-mail nebo heslo.", allow_signup=ALLOW_SIGNUP),
            status_code=401,
        )

    if not verify_password(password, user["password_hash"]):
        logger.warning("Login failed (wrong password) for %s from %s", email, request.client.host)
        return HTMLResponse(
            _render(request, "login.html", error="Nesprávný e-mail nebo heslo.", allow_signup=ALLOW_SIGNUP),
            status_code=401,
        )

    logger.info("Login success: %s from %s", email, request.client.host)
    resp = RedirectResponse("/", status_code=303)
    set_session_cookie(resp, user["id"])
    return resp


# ── Signup ────────────────────────────────────────────────────────────────────

@router.post("/auth/signup")
async def auth_signup(
    request:  Request,
    email:    str = Form(...),
    password: str = Form(...),
    confirm:  str = Form(...),
):
    from services.db import create_user, set_verification_token
    from services.email import make_verification_email, send_email

    if not ALLOW_SIGNUP:
        return HTMLResponse(_render(request, "signup.html", error="Registrace je zakázána."), status_code=403)

    email = email.strip().lower()

    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return HTMLResponse(_render(request, "signup.html", error="Zadejte platný e-mail."), status_code=422)
    if len(password) < 8:
        return HTMLResponse(_render(request, "signup.html", error="Heslo musí mít alespoň 8 znaků."), status_code=422)
    if password != confirm:
        return HTMLResponse(_render(request, "signup.html", error="Hesla se neshodují."), status_code=422)

    user_id = await create_user(email, hash_password(password))
    if user_id is None:
        return HTMLResponse(_render(request, "signup.html", error="Tento e-mail je již zaregistrován."), status_code=409)

    logger.info("New user registered: %s", email)

    if _email_enabled():
        token = secrets.token_urlsafe(32)
        await set_verification_token(user_id, token)
        link  = f"{_app_url()}/auth/verify-email?token={token}"
        await send_email(email, "Potvrďte svůj účet — Fakturant", make_verification_email(link))
        return RedirectResponse("/auth/verify-email-sent", status_code=303)
    else:
        # Email disabled (dev mode) — log in immediately, user starts as verified (DEFAULT 1)
        resp = RedirectResponse("/", status_code=303)
        set_session_cookie(resp, user_id)
        return resp


# ── Logout ────────────────────────────────────────────────────────────────────

@router.post("/auth/logout")
async def auth_logout():
    resp = RedirectResponse("/login", status_code=303)
    clear_session_cookie(resp)
    return resp


# ── Me ────────────────────────────────────────────────────────────────────────

@router.get("/auth/me")
async def auth_me(request: Request):
    uid = get_user_id(request)
    if uid is not None:
        return {"authenticated": True, "user_id": uid}
    return JSONResponse({"authenticated": False}, status_code=401)


# ── Email verification ────────────────────────────────────────────────────────

@router.get("/auth/verify-email-sent", response_class=HTMLResponse)
async def verify_email_sent_page(request: Request) -> HTMLResponse:
    uid = get_user_id(request)
    email = ""
    if uid:
        from services.db import get_user_by_id
        u = await get_user_by_id(uid)
        email = (u or {}).get("email", "")
    return HTMLResponse(_render(request, "verify_email_sent.html", email=email))


@router.get("/auth/verify-email", response_class=HTMLResponse)
async def verify_email(request: Request, token: str = Query(...)) -> HTMLResponse:
    from services.db import get_user_by_verification_token, verify_email as db_verify

    user = await get_user_by_verification_token(token)
    if not user:
        return HTMLResponse(
            _render(request, "verify_email_sent.html",
                    email="", error="Odkaz je neplatný nebo vypršel."),
            status_code=400,
        )

    await db_verify(user["id"])
    logger.info("Email verified for user %s", user["id"])
    return HTMLResponse(_render(request, "verify_email_done.html"))


@router.post("/auth/resend-verification")
async def resend_verification(request: Request, email: str = Form(...)):
    from services.db import get_user_by_email, set_verification_token
    from services.email import make_verification_email, send_email

    email = email.strip().lower()
    # Always return the same page to avoid disclosing whether the email exists
    user = await get_user_by_email(email)
    if user and not user.get("email_verified", 1) and _email_enabled():
        token = secrets.token_urlsafe(32)
        await set_verification_token(user["id"], token)
        link  = f"{_app_url()}/auth/verify-email?token={token}"
        await send_email(email, "Potvrďte svůj účet — Fakturant", make_verification_email(link))

    return HTMLResponse(_render(request, "verify_email_sent.html", email=email, resent=True))


# ── Password reset ────────────────────────────────────────────────────────────

@router.get("/auth/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_render(request, "forgot_password.html", sent=False, error=""))


@router.post("/auth/forgot-password")
async def forgot_password(request: Request, email: str = Form(...)):
    from services.db import get_user_by_email, set_reset_token
    from services.email import make_reset_email, send_email

    email = email.strip().lower()
    user  = await get_user_by_email(email)

    if user and _email_enabled():
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        token = secrets.token_urlsafe(32)
        await set_reset_token(user["id"], token, expires_at)
        link  = f"{_app_url()}/auth/reset-password?token={token}"
        await send_email(email, "Obnovení hesla — Fakturant", make_reset_email(link))
        logger.info("Password reset requested for %s", email)
    elif not _email_enabled() and user:
        # Dev mode: log the link so devs can test without SMTP
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        token = secrets.token_urlsafe(32)
        await set_reset_token(user["id"], token, expires_at)
        logger.info("[reset-dev] %s/auth/reset-password?token=%s", _app_url(), token)

    # Always show "sent" — never confirm whether email exists
    return HTMLResponse(_render(request, "forgot_password.html", sent=True, error=""))


@router.get("/auth/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = Query(...)) -> HTMLResponse:
    from services.db import get_user_by_reset_token

    user = await get_user_by_reset_token(token)
    if not user:
        return HTMLResponse(
            _render(request, "forgot_password.html", sent=False,
                    error="Odkaz pro obnovení hesla je neplatný nebo vypršel."),
            status_code=400,
        )
    return HTMLResponse(_render(request, "reset_password.html", token=token, error=""))


@router.post("/auth/reset-password")
async def reset_password(
    request:  Request,
    token:    str = Form(...),
    password: str = Form(...),
    confirm:  str = Form(...),
):
    from services.db import get_user_by_reset_token, reset_password as db_reset

    if len(password) < 8:
        return HTMLResponse(
            _render(request, "reset_password.html", token=token, error="Heslo musí mít alespoň 8 znaků."),
            status_code=422,
        )
    if password != confirm:
        return HTMLResponse(
            _render(request, "reset_password.html", token=token, error="Hesla se neshodují."),
            status_code=422,
        )

    user = await get_user_by_reset_token(token)
    if not user:
        return HTMLResponse(
            _render(request, "forgot_password.html", sent=False,
                    error="Odkaz pro obnovení hesla je neplatný nebo vypršel."),
            status_code=400,
        )

    await db_reset(user["id"], hash_password(password))
    logger.info("Password reset completed for user %s", user["id"])

    resp = RedirectResponse("/login", status_code=303)
    return resp
