"""
tests/test_password_reset.py

Covers the password reset flow:
- Forgot-password sends reset email for known user
- Forgot-password for unknown email shows same success page (no info leak)
- Valid reset token renders the form
- Expired token is rejected
- Valid token + new password updates the password
- Mismatched / short password is rejected
- Token is single-use
"""
from __future__ import annotations

import pytest


# ── Fixture ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH",        str(tmp_path / "reset_test.db"))
    monkeypatch.setenv("SESSION_SECRET", "reset-test-secret-32-chars-ok!!x")
    monkeypatch.setenv("ALLOW_SIGNUP",   "true")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_fake_key")
    monkeypatch.setenv("APP_URL",        "http://testserver")
    sent = []
    import services.email as email_mod
    monkeypatch.setattr(email_mod, "send_email",
                        lambda to, subject, html: sent.append({"to": to, "subject": subject, "html": html})
                        or __import__("asyncio").sleep(0))
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as c:
        c.sent_emails = sent
        # Register and verify a user
        c.post("/auth/signup", data={
            "email": "user@test.com", "password": "oldpass1", "confirm": "oldpass1",
        })
        # Verify email so the user can log in
        import re
        html = sent[-1]["html"]
        m = re.search(r"token=([\w\-]+)", html)
        if m:
            c.get(f"/auth/verify-email?token={m.group(1)}")
        yield c


def _extract_reset_token(html: str) -> str | None:
    import re
    m = re.search(r"reset-password\?token=([\w\-]+)", html)
    return m.group(1) if m else None


# ── 1. Forgot-password request ─────────────────────────────────────────────────

class TestForgotPassword:

    def test_known_email_sends_email(self, client):
        initial = len(client.sent_emails)
        r = client.post("/auth/forgot-password", data={"email": "user@test.com"})
        assert r.status_code == 200
        assert "zkontrolujte" in r.text.lower()
        assert len(client.sent_emails) == initial + 1

    def test_unknown_email_shows_same_response(self, client):
        initial = len(client.sent_emails)
        r = client.post("/auth/forgot-password", data={"email": "nobody@test.com"})
        assert r.status_code == 200
        assert "zkontrolujte" in r.text.lower()
        assert len(client.sent_emails) == initial  # no email sent

    def test_reset_email_contains_token_link(self, client):
        client.post("/auth/forgot-password", data={"email": "user@test.com"})
        html = client.sent_emails[-1]["html"]
        assert "reset-password?token=" in html


# ── 2. Reset password form ─────────────────────────────────────────────────────

class TestResetPasswordForm:

    def test_valid_token_renders_form(self, client):
        client.post("/auth/forgot-password", data={"email": "user@test.com"})
        token = _extract_reset_token(client.sent_emails[-1]["html"])
        assert token
        r = client.get(f"/auth/reset-password?token={token}")
        assert r.status_code == 200
        assert 'name="password"' in r.text

    def test_invalid_token_returns_400(self, client):
        r = client.get("/auth/reset-password?token=notarealtoken")
        assert r.status_code == 400

    def test_expired_token_returns_400(self, client):
        import asyncio
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        client.post("/auth/forgot-password", data={"email": "user@test.com"})
        token = _extract_reset_token(client.sent_emails[-1]["html"])
        # Overwrite the token with an already-expired timestamp
        from services.db import get_user_by_email, set_reset_token
        user = asyncio.run(get_user_by_email("user@test.com"))
        asyncio.run(set_reset_token(user["id"], token, past))
        r = client.get(f"/auth/reset-password?token={token}")
        assert r.status_code == 400


# ── 3. Submitting the new password ────────────────────────────────────────────

class TestResetPasswordSubmit:

    def _get_token(self, client):
        client.post("/auth/forgot-password", data={"email": "user@test.com"})
        return _extract_reset_token(client.sent_emails[-1]["html"])

    def test_valid_reset_updates_password(self, client):
        token = self._get_token(client)
        r = client.post("/auth/reset-password", data={
            "token": token, "password": "newpass99", "confirm": "newpass99",
        })
        assert r.status_code == 303
        assert r.headers["location"] == "/login"
        # Old password should no longer work
        r2 = client.post("/auth/login", data={"email": "user@test.com", "password": "oldpass1"})
        assert r2.status_code == 401
        # New password works
        r3 = client.post("/auth/login", data={"email": "user@test.com", "password": "newpass99"})
        assert r3.status_code == 303

    def test_mismatched_passwords_returns_422(self, client):
        token = self._get_token(client)
        r = client.post("/auth/reset-password", data={
            "token": token, "password": "newpass99", "confirm": "different1",
        })
        assert r.status_code == 422

    def test_short_password_returns_422(self, client):
        token = self._get_token(client)
        r = client.post("/auth/reset-password", data={
            "token": token, "password": "short", "confirm": "short",
        })
        assert r.status_code == 422

    def test_token_is_single_use(self, client):
        token = self._get_token(client)
        client.post("/auth/reset-password", data={
            "token": token, "password": "newpass99", "confirm": "newpass99",
        })
        r = client.post("/auth/reset-password", data={
            "token": token, "password": "another99", "confirm": "another99",
        })
        assert r.status_code == 400
