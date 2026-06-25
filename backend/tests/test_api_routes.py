"""
tests/test_api_routes.py

Coverage: every HTTP endpoint in main.py
  - correct status codes for happy path
  - auth guard (401 without session)
  - 404 for unknown IDs
  - input validation (422 / 400)

Auth: signup fixture creates a test user and logs in so the session cookie
      is automatically forwarded by TestClient on every request.
"""
from __future__ import annotations

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "routes_test.db"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-for-routes-tests-32chars!")
    monkeypatch.setenv("ALLOW_SIGNUP", "true")
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as c:
        r = c.post("/auth/signup", data={
            "email": "route@test.com",
            "password": "routepass123",
            "confirm": "routepass123",
        })
        assert r.status_code == 303, f"Signup failed: {r.status_code}"
        yield c


@pytest.fixture
def anon(tmp_path, monkeypatch):
    """Unauthenticated client — shares the same DB as client fixture would."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "anon_test.db"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-for-anon-tests-32chars!!")
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as c:
        yield c


def _inv(number="FA-2025-001"):
    return {
        "template": "modern",
        "invoice_number": number,
        "issue_date": "2025-01-10",
        "duzp": "2025-01-10",
        "due_date": "2025-01-24",
        "currency": "CZK",
        "bank_account": "1234/0800",
        "variable_symbol": "1",
        "iban": "", "swift": "", "notes": "", "logo_b64": None,
        "supplier": {"name": "A s.r.o.", "ico": "27766383", "dic": "CZ27766383",
                     "address": "Praha", "email": "a@a.cz", "vat_payer": True},
        "customer": {"name": "B a.s.", "ico": "45272956", "dic": "CZ45272956",
                     "address": "Brno", "email": "b@b.cz", "vat_payer": True},
        "items": [{"description": "X", "project": "", "item_date": "",
                   "quantity": 1, "unit": "ks", "unit_price": 1000.0, "vat_rate": 21.0}],
    }


# ═══════════════════════════════════════════════════════════
# /health
# ═══════════════════════════════════════════════════════════

def test_health(anon):
    r = anon.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ═══════════════════════════════════════════════════════════
# Auth routes
# ═══════════════════════════════════════════════════════════

class TestAuthRoutes:

    def test_login_page_200(self, anon):
        assert anon.get("/login").status_code == 200

    def test_signup_page_200(self, anon):
        assert anon.get("/signup").status_code == 200

    def test_auth_me_unauthenticated(self, anon):
        assert anon.get("/auth/me").status_code == 401

    def test_auth_me_authenticated(self, client):
        r = client.get("/auth/me")
        assert r.status_code == 200
        assert r.json()["authenticated"] is True

    def test_signup_creates_user(self, anon):
        r = anon.post("/auth/signup", data={
            "email": "new@test.com",
            "password": "newpass123",
            "confirm": "newpass123",
        })
        assert r.status_code == 303

    def test_signup_duplicate_email(self, client):
        client.post("/auth/signup", data={
            "email": "dup@test.com", "password": "pass12345", "confirm": "pass12345"
        })
        r = client.post("/auth/signup", data={
            "email": "dup@test.com", "password": "pass12345", "confirm": "pass12345"
        })
        assert r.status_code == 409

    def test_signup_short_password(self, anon):
        r = anon.post("/auth/signup", data={
            "email": "short@test.com", "password": "abc", "confirm": "abc"
        })
        assert r.status_code == 422

    def test_signup_password_mismatch(self, anon):
        r = anon.post("/auth/signup", data={
            "email": "mm@test.com", "password": "pass12345", "confirm": "different"
        })
        assert r.status_code == 422

    def test_login_wrong_password(self, anon):
        anon.post("/auth/signup", data={
            "email": "wp@test.com", "password": "correctpass", "confirm": "correctpass"
        })
        r = anon.post("/auth/login", data={
            "email": "wp@test.com", "password": "wrongpass"
        })
        assert r.status_code == 401

    def test_logout_clears_session(self, client):
        assert client.get("/auth/me").status_code == 200
        client.post("/auth/logout")
        assert client.get("/auth/me").status_code == 401


# ═══════════════════════════════════════════════════════════
# Auth guard — /api/invoices/* requires login
# ═══════════════════════════════════════════════════════════

class TestAuthGuard:

    def test_list_requires_auth(self, anon):
        assert anon.get("/api/invoices").status_code == 401

    def test_get_requires_auth(self, anon):
        assert anon.get("/api/invoices/1").status_code == 401

    def test_save_requires_auth(self, anon):
        r = anon.post("/api/invoices/save", json={"data": _inv()})
        assert r.status_code == 401

    def test_delete_requires_auth(self, anon):
        assert anon.delete("/api/invoices/1").status_code == 401

    def test_status_requires_auth(self, anon):
        assert anon.patch("/api/invoices/1/status", json={"status": "issued"}).status_code == 401

    def test_payments_requires_auth(self, anon):
        r = anon.post("/api/invoices/1/payments", json={"amount": 100, "paid_on": "2025-01-01"})
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════
# Public routes — no auth needed
# ═══════════════════════════════════════════════════════════

class TestPublicRoutes:

    def test_preview_public(self, anon):
        r = anon.post("/preview", json=_inv())
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_validate_public(self, anon):
        r = anon.post("/validate", json=_inv())
        assert r.status_code == 200
        assert "valid" in r.json()

    def test_sequence_next_public(self, anon):
        r = anon.get("/api/sequence/next?prefix=FA")
        assert r.status_code == 200
        assert r.json()["number"].startswith("FA-")

    def test_sequence_check_public(self, anon):
        r = anon.get("/api/sequence/check?number=FA-2025-001")
        assert r.status_code == 200
        assert "ok" in r.json()


# ═══════════════════════════════════════════════════════════
# Invoice CRUD
# ═══════════════════════════════════════════════════════════

class TestInvoiceCRUD:

    def test_list_empty_on_fresh_db(self, client):
        r = client.get("/api/invoices")
        assert r.status_code == 200
        assert r.json() == []

    def test_save_creates_invoice(self, client):
        r = client.post("/api/invoices/save", json={"data": _inv()})
        assert r.status_code == 200
        assert r.json()["invoice_number"] == "FA-2025-001"

    def test_save_returns_id(self, client):
        r = client.post("/api/invoices/save", json={"data": _inv()})
        assert "id" in r.json()
        assert r.json()["id"] > 0

    def test_get_saved_invoice(self, client):
        saved = client.post("/api/invoices/save", json={"data": _inv()}).json()
        r = client.get(f"/api/invoices/{saved['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == saved["id"]

    def test_get_unknown_invoice_404(self, client):
        assert client.get("/api/invoices/99999").status_code == 404

    def test_list_shows_saved_invoice(self, client):
        client.post("/api/invoices/save", json={"data": _inv()})
        r = client.get("/api/invoices")
        assert len(r.json()) == 1

    def test_update_existing_invoice(self, client):
        saved = client.post("/api/invoices/save", json={"data": _inv()}).json()
        updated_data = {**_inv(), "notes": "updated note"}
        r = client.post("/api/invoices/save", json={
            "data": updated_data,
            "invoice_id": saved["id"],
        })
        assert r.status_code == 200
        assert r.json()["data"]["notes"] == "updated note"

    def test_delete_invoice(self, client):
        saved = client.post("/api/invoices/save", json={"data": _inv()}).json()
        r = client.delete(f"/api/invoices/{saved['id']}")
        assert r.status_code == 204
        assert client.get(f"/api/invoices/{saved['id']}").status_code == 404

    def test_delete_unknown_invoice_404(self, client):
        assert client.delete("/api/invoices/99999").status_code == 404

    def test_delete_clears_from_list(self, client):
        saved = client.post("/api/invoices/save", json={"data": _inv()}).json()
        client.delete(f"/api/invoices/{saved['id']}")
        assert client.get("/api/invoices").json() == []

    def test_save_invalid_doc_type_422(self, client):
        r = client.post("/api/invoices/save", json={
            "data": _inv(), "doc_type": "invalid"
        })
        assert r.status_code == 422

    def test_invoice_isolation(self, tmp_path, monkeypatch):
        """User B cannot see user A's invoices."""
        monkeypatch.setenv("DB_PATH", str(tmp_path / "iso_test.db"))
        monkeypatch.setenv("SESSION_SECRET", "isolation-test-secret-32chars-long!")
        from fastapi.testclient import TestClient
        from main import app
        with TestClient(app, follow_redirects=False) as c:
            # Create user A and save an invoice
            c.post("/auth/signup", data={"email": "a@iso.com", "password": "pass12345", "confirm": "pass12345"})
            saved = c.post("/api/invoices/save", json={"data": _inv()}).json()
            inv_id = saved["id"]

            # Log out, register user B
            c.post("/auth/logout")
            c.post("/auth/signup", data={"email": "b@iso.com", "password": "pass12345", "confirm": "pass12345"})

            # User B sees empty list
            assert c.get("/api/invoices").json() == []
            # User B gets 404 for user A's invoice ID
            assert c.get(f"/api/invoices/{inv_id}").status_code == 404
            # User B cannot delete user A's invoice
            assert c.delete(f"/api/invoices/{inv_id}").status_code == 404


# ═══════════════════════════════════════════════════════════
# Status lifecycle
# ═══════════════════════════════════════════════════════════

class TestStatusLifecycle:

    def _saved(self, client):
        return client.post("/api/invoices/save", json={"data": _inv()}).json()

    def test_default_status_draft(self, client):
        assert self._saved(client)["status"] == "draft"

    def test_transition_draft_to_issued(self, client):
        inv = self._saved(client)
        r = client.patch(f"/api/invoices/{inv['id']}/status", json={"status": "issued"})
        assert r.status_code == 200
        assert r.json()["status"] == "issued"
        assert r.json()["issued_at"] is not None

    def test_transition_to_sent(self, client):
        inv = self._saved(client)
        client.patch(f"/api/invoices/{inv['id']}/status", json={"status": "issued"})
        r = client.patch(f"/api/invoices/{inv['id']}/status", json={"status": "sent"})
        assert r.json()["status"] == "sent"

    def test_transition_to_cancelled(self, client):
        inv = self._saved(client)
        r = client.patch(f"/api/invoices/{inv['id']}/status", json={"status": "cancelled"})
        assert r.json()["status"] == "cancelled"

    def test_invalid_status_422(self, client):
        inv = self._saved(client)
        r = client.patch(f"/api/invoices/{inv['id']}/status", json={"status": "flying"})
        assert r.status_code == 422

    def test_status_unknown_invoice_404(self, client):
        r = client.patch("/api/invoices/99999/status", json={"status": "issued"})
        assert r.status_code == 404

    def test_mark_overdue_endpoint(self, client):
        r = client.post("/api/invoices/mark-overdue")
        assert r.status_code == 200
        assert "marked_overdue" in r.json()


# ═══════════════════════════════════════════════════════════
# Payments
# ═══════════════════════════════════════════════════════════

class TestPayments:

    def _issued(self, client):
        inv = client.post("/api/invoices/save", json={"data": _inv()}).json()
        client.patch(f"/api/invoices/{inv['id']}/status", json={"status": "issued"})
        return inv

    def test_add_payment(self, client):
        inv = self._issued(client)
        r = client.post(f"/api/invoices/{inv['id']}/payments",
                        json={"amount": 500.0, "paid_on": "2025-01-20"})
        assert r.status_code == 200
        assert r.json()["paid_total"] == 500.0

    def test_full_payment_marks_paid(self, client):
        inv = self._issued(client)
        total = inv["total"]
        r = client.post(f"/api/invoices/{inv['id']}/payments",
                        json={"amount": total, "paid_on": "2025-01-20"})
        assert r.json()["status"] == "paid"

    def test_payment_on_draft_rejected(self, client):
        inv = client.post("/api/invoices/save", json={"data": _inv()}).json()
        r = client.post(f"/api/invoices/{inv['id']}/payments",
                        json={"amount": 100.0, "paid_on": "2025-01-20"})
        assert r.status_code == 400

    def test_zero_amount_rejected(self, client):
        inv = self._issued(client)
        r = client.post(f"/api/invoices/{inv['id']}/payments",
                        json={"amount": 0, "paid_on": "2025-01-20"})
        assert r.status_code == 422

    def test_negative_amount_rejected(self, client):
        inv = self._issued(client)
        r = client.post(f"/api/invoices/{inv['id']}/payments",
                        json={"amount": -100, "paid_on": "2025-01-20"})
        assert r.status_code == 422

    def test_delete_payment(self, client):
        inv = self._issued(client)
        pr = client.post(f"/api/invoices/{inv['id']}/payments",
                         json={"amount": 200.0, "paid_on": "2025-01-20"}).json()
        pid = pr["payments"][0]["id"]
        r = client.delete(f"/api/invoices/{inv['id']}/payments/{pid}")
        assert r.status_code == 200
        assert r.json()["paid_total"] == 0.0

    def test_payment_unknown_invoice_404(self, client):
        r = client.post("/api/invoices/99999/payments",
                        json={"amount": 100, "paid_on": "2025-01-20"})
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════
# Credit notes
# ═══════════════════════════════════════════════════════════

class TestCreditNotes:

    def test_create_credit_note(self, client):
        inv = client.post("/api/invoices/save", json={"data": _inv()}).json()
        r = client.post(f"/api/invoices/{inv['id']}/credit-note")
        assert r.status_code == 200
        d = r.json()
        assert d["doc_type"] == "credit_note"
        assert d["credit_note_for"] == inv["id"]
        assert d["suggested_number"].startswith("DD-")

    def test_credit_note_items_negated(self, client):
        inv = client.post("/api/invoices/save", json={"data": _inv()}).json()
        cn = client.post(f"/api/invoices/{inv['id']}/credit-note").json()
        for item in cn["data"]["items"]:
            assert item["unit_price"] <= 0

    def test_credit_note_unknown_invoice_404(self, client):
        assert client.post("/api/invoices/99999/credit-note").status_code == 404


# ═══════════════════════════════════════════════════════════
# Sequence
# ═══════════════════════════════════════════════════════════

class TestSequence:

    def test_next_number_format(self, client):
        r = client.get("/api/sequence/next?prefix=FA&year=2025")
        assert r.status_code == 200
        assert r.json()["number"] == "FA-2025-001"

    def test_check_gap_no_gap(self, anon):
        r = anon.get("/api/sequence/check?number=FA-2025-001")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_delete_draft_rolls_back_sequence(self, client):
        """Deleting a draft invoice must not leave a gap in the sequence.

        Flow: save FA-2025-001 (commits counter to 1) → delete it →
        next peek must return FA-2025-001 again, not FA-2025-002.
        """
        # 1. Save a draft — commit_sequence:true burns FA-2025-001
        r = client.post("/api/invoices/save", json={
            "data": _inv("FA-2025-001"), "commit_sequence": True,
        })
        assert r.status_code == 200
        inv_id = r.json()["id"]

        # 2. Delete the draft
        r = client.delete(f"/api/invoices/{inv_id}")
        assert r.status_code in (200, 204)

        # 3. Counter must have rolled back — next number is 001 again
        r = client.get("/api/sequence/next?prefix=FA&year=2025")
        assert r.status_code == 200
        assert r.json()["number"] == "FA-2025-001", (
            "Deleting a draft left a sequence gap: expected FA-2025-001, "
            f"got {r.json()['number']}"
        )

    def test_delete_non_last_draft_does_not_roll_back_sequence(self, client):
        """Deleting a draft that is NOT the last committed number must not
        roll back the counter — a later invoice already consumed the slot."""
        # Save two drafts: counter goes 1 → 2
        r1 = client.post("/api/invoices/save", json={
            "data": _inv("FA-2025-001"), "commit_sequence": True,
        })
        inv_id_1 = r1.json()["id"]
        client.post("/api/invoices/save", json={
            "data": _inv("FA-2025-002"), "commit_sequence": True,
        })

        # Delete the first draft (seq=1, counter=2 → 1 ≠ 2, no rollback)
        r = client.delete(f"/api/invoices/{inv_id_1}")
        assert r.status_code in (200, 204)

        # Counter must stay at 2 — next peek returns FA-2025-003
        r = client.get("/api/sequence/next?prefix=FA&year=2025")
        assert r.json()["number"] == "FA-2025-003", (
            "Deleting a non-last draft incorrectly rolled back the sequence"
        )

    def test_per_user_sequence_isolation(self, tmp_path, monkeypatch):
        """Two different users each get their own FA-2025-001."""
        monkeypatch.setenv("DB_PATH", str(tmp_path / "seq_iso.db"))
        monkeypatch.setenv("SESSION_SECRET", "seq-isolation-test-secret-32chars!")
        from fastapi.testclient import TestClient
        from main import app
        with TestClient(app, follow_redirects=False) as c:
            c.post("/auth/signup", data={"email": "u1@seq.com", "password": "pass12345", "confirm": "pass12345"})
            n1 = c.get("/api/sequence/next?prefix=FA&year=2025").json()["number"]

            c.post("/auth/logout")
            c.post("/auth/signup", data={"email": "u2@seq.com", "password": "pass12345", "confirm": "pass12345"})
            n2 = c.get("/api/sequence/next?prefix=FA&year=2025").json()["number"]

            assert n1 == "FA-2025-001"
            assert n2 == "FA-2025-001"   # independent counter per user


class TestInvoiceNumberValidation:

    def test_duplicate_number_rejected(self, client):
        """Saving a second invoice with the same number must return 409."""
        client.post("/api/invoices/save", json={"data": _inv("FA-2025-001"), "commit_sequence": True})
        r = client.post("/api/invoices/save", json={"data": _inv("FA-2025-001")})
        assert r.status_code == 409
        assert "FA-2025-001" in r.json()["detail"]

    def test_edit_own_number_allowed(self, client):
        """Re-saving an invoice with its own number must not be rejected."""
        r = client.post("/api/invoices/save", json={"data": _inv("FA-2025-001"), "commit_sequence": True})
        inv_id = r.json()["id"]
        r = client.post("/api/invoices/save", json={"data": _inv("FA-2025-001"), "invoice_id": inv_id})
        assert r.status_code == 200

    def test_manual_number_edit_advances_sequence(self, client):
        """Manually jumping the number to FA-2025-005 must advance the counter
        so the next auto-generated invoice is FA-2025-006, not FA-2025-002."""
        r = client.post("/api/invoices/save", json={"data": _inv("FA-2025-001"), "commit_sequence": True})
        inv_id = r.json()["id"]
        # Edit the invoice, bumping number ahead
        r = client.post("/api/invoices/save", json={"data": _inv("FA-2025-005"), "invoice_id": inv_id})
        assert r.status_code == 200
        # Next auto-generated must skip past 005
        r = client.get("/api/sequence/next?prefix=FA&year=2025")
        assert r.json()["number"] == "FA-2025-006"
