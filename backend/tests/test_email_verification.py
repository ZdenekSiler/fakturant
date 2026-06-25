"""
tests/test_email_verification.py

Covers the email verification flow:
- Signup with RESEND_API_KEY set → user created unverified, email "sent"
- Signup without RESEND_API_KEY → user created verified, logged in immediately
- Unverified user blocked from /api/invoices (403)
- Valid token → user verified, redirect
- Invalid / used token → 400
- Resend creates a new token
"""
from __future__ import annotations

import pytest


# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def client_with_email(tmp_path, monkeypatch):
    """Client with email enabled (RESEND_API_KEY set) and email sending mocked."""
    monkeypatch.setenv("DB_PATH",         str(tmp_path / "verify_test.db"))
    monkeypatch.setenv("SESSION_SECRET",  "verify-test-secret-32chars-ok!!x")
    monkeypatch.setenv("ALLOW_SIGNUP",    "true")
    monkeypatch.setenv("RESEND_API_KEY",  "re_test_fake_key")
    monkeypatch.setenv("APP_URL",         "http://testserver")
    # Capture outgoing emails without actually sending
    sent = []
    import services.email as email_mod
    monkeypatch.setattr(email_mod, "send_email",
                        lambda to, subject, html: sent.append({"to": to, "subject": subject, "html": html})
                        or __import__("asyncio").sleep(0))
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as c:
        c.sent_emails = sent
        yield c


@pytest.fixture
def client_no_email(tmp_path, monkeypatch):
    """Client with email disabled (no RESEND_API_KEY) — dev mode."""
    monkeypatch.setenv("DB_PATH",        str(tmp_path / "noemail_test.db"))
    monkeypatch.setenv("SESSION_SECRET", "noemail-test-secret-32chars-ok!x")
    monkeypatch.setenv("ALLOW_SIGNUP",   "true")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as c:
        yield c


# ── 1. Signup with email enabled ───────────────────────────────────────────────

class TestSignupWithEmail:

    def test_signup_redirects_to_verify_sent(self, client_with_email):
        r = client_with_email.post("/auth/signup", data={
            "email": "new@test.com", "password": "pass1234", "confirm": "pass1234",
        })
        assert r.status_code == 303
        assert r.headers["location"] == "/auth/verify-email-sent"

    def test_signup_sends_verification_email(self, client_with_email):
        client_with_email.post("/auth/signup", data={
            "email": "send@test.com", "password": "pass1234", "confirm": "pass1234",
        })
        assert len(client_with_email.sent_emails) == 1
        mail = client_with_email.sent_emails[0]
        assert mail["to"] == "send@test.com"
        assert "potvrďte" in mail["subject"].lower()
        assert "verify-email?token=" in mail["html"]

    def test_signup_does_not_set_session_cookie(self, client_with_email):
        r = client_with_email.post("/auth/signup", data={
            "email": "nocookie@test.com", "password": "pass1234", "confirm": "pass1234",
        })
        assert "fakturant_session" not in r.cookies

    def test_unverified_user_blocked_from_api(self, client_with_email):
        client_with_email.post("/auth/signup", data={
            "email": "blocked@test.com", "password": "pass1234", "confirm": "pass1234",
        })
        # Log in manually to get a session
        r = client_with_email.post("/auth/login", data={
            "email": "blocked@test.com", "password": "pass1234",
        })
        assert r.status_code == 303
        r2 = client_with_email.get("/api/invoices")
        assert r2.status_code == 403
        assert r2.json()["detail"] == "email_not_verified"


# ── 2. Signup without email (dev mode) ────────────────────────────────────────

class TestSignupDevMode:

    def test_signup_logs_in_immediately(self, client_no_email):
        r = client_no_email.post("/auth/signup", data={
            "email": "dev@test.com", "password": "pass1234", "confirm": "pass1234",
        })
        assert r.status_code == 303
        assert r.headers["location"] == "/"
        assert "fakturant_session" in r.cookies

    def test_verified_user_can_access_api(self, client_no_email):
        client_no_email.post("/auth/signup", data={
            "email": "verified@test.com", "password": "pass1234", "confirm": "pass1234",
        })
        r = client_no_email.get("/api/invoices")
        assert r.status_code == 200


# ── 3. Email verification token flow ──────────────────────────────────────────

class TestVerificationToken:

    def _signup_and_get_token(self, client):
        client.post("/auth/signup", data={
            "email": "tok@test.com", "password": "pass1234", "confirm": "pass1234",
        })
        mail = client.sent_emails[-1]["html"]
        import re
        m = re.search(r"token=([\w\-]+)", mail)
        return m.group(1) if m else None

    def test_valid_token_verifies_email(self, client_with_email):
        token = self._signup_and_get_token(client_with_email)
        assert token
        r = client_with_email.get(f"/auth/verify-email?token={token}")
        assert r.status_code == 200
        assert "potvrzen" in r.text.lower()

    def test_verified_user_can_access_api(self, client_with_email):
        token = self._signup_and_get_token(client_with_email)
        client_with_email.get(f"/auth/verify-email?token={token}")
        client_with_email.post("/auth/login", data={
            "email": "tok@test.com", "password": "pass1234",
        })
        r = client_with_email.get("/api/invoices")
        assert r.status_code == 200

    def test_invalid_token_returns_400(self, client_with_email):
        r = client_with_email.get("/auth/verify-email?token=bogustoken")
        assert r.status_code == 400

    def test_token_is_single_use(self, client_with_email):
        token = self._signup_and_get_token(client_with_email)
        client_with_email.get(f"/auth/verify-email?token={token}")
        r = client_with_email.get(f"/auth/verify-email?token={token}")
        assert r.status_code == 400


# ── 4. Resend verification ─────────────────────────────────────────────────────

class TestResendVerification:

    def test_resend_sends_new_email(self, client_with_email):
        client_with_email.post("/auth/signup", data={
            "email": "resend@test.com", "password": "pass1234", "confirm": "pass1234",
        })
        initial_count = len(client_with_email.sent_emails)
        client_with_email.post("/auth/resend-verification",
                               data={"email": "resend@test.com"})
        assert len(client_with_email.sent_emails) == initial_count + 1

    def test_resend_for_unknown_email_is_silent(self, client_with_email):
        initial_count = len(client_with_email.sent_emails)
        r = client_with_email.post("/auth/resend-verification",
                                   data={"email": "nobody@test.com"})
        assert r.status_code == 200
        assert len(client_with_email.sent_emails) == initial_count
