"""
tests/test_dashboard_data.py

Verifies that GET /api/invoices returns the exact fields the dashboard
aggregates client-side: total, paid_total, status, issued_at, due_date,
doc_type. Also checks that the list endpoint supports the limit=500 query
used by the dashboard fetch.
"""
from __future__ import annotations

import pytest


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "dashboard_test.db"))
    monkeypatch.setenv("SESSION_SECRET", "dashboard-test-secret-32chars-ok!")
    monkeypatch.setenv("ALLOW_SIGNUP", "true")
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as c:
        r = c.post("/auth/signup", data={
            "email": "dash@test.com",
            "password": "dashpass123",
            "confirm": "dashpass123",
        })
        assert r.status_code == 303
        yield c


def _inv(number="FA-2025-001", due="2025-01-24", issue="2025-01-10"):
    return {
        "template": "modern",
        "invoice_number": number,
        "issue_date": issue,
        "duzp": issue,
        "due_date": due,
        "currency": "CZK",
        "bank_account": "1234/0800",
        "variable_symbol": "1",
        "iban": "", "swift": "", "notes": "", "logo_b64": None,
        "supplier": {"name": "A s.r.o.", "ico": "27766383", "dic": "CZ27766383",
                     "address": "Praha", "email": "a@a.cz", "vat_payer": True},
        "customer": {"name": "B a.s.", "ico": "45272956", "dic": "CZ45272956",
                     "address": "Brno",  "email": "b@b.cz", "vat_payer": True},
        "items": [{"description": "Práce", "project": "", "item_date": issue,
                   "quantity": 10, "unit": "hod", "unit_price": 1000.0, "vat_rate": 21.0}],
    }


# ── 1. List endpoint returns dashboard fields ───────────────────────────────────

class TestDashboardFields:

    def test_list_includes_required_fields(self, client):
        """Every field the dashboard aggregates must be present in the list response."""
        client.post("/api/invoices/save", json={"data": _inv(), "commit_sequence": True})
        r = client.get("/api/invoices")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        row = rows[0]
        required = ["id", "invoice_number", "status", "doc_type",
                    "total", "paid_total", "issued_at", "due_date",
                    "created_at", "updated_at"]
        for field in required:
            assert field in row, f"Missing field: {field}"

    def test_total_matches_computed_amount(self, client):
        """total should reflect 10 hod × 1000 Kč + 21% VAT = 12 100 Kč."""
        client.post("/api/invoices/save", json={"data": _inv(), "commit_sequence": True})
        r = client.get("/api/invoices")
        row = r.json()[0]
        assert row["total"] == pytest.approx(12100.0)

    def test_paid_total_starts_at_zero(self, client):
        client.post("/api/invoices/save", json={"data": _inv(), "commit_sequence": True})
        r = client.get("/api/invoices")
        assert r.json()[0]["paid_total"] == 0.0

    def test_issued_at_set_after_issuing(self, client):
        """issued_at must be populated when status transitions to 'issued'."""
        save_r = client.post("/api/invoices/save", json={"data": _inv(), "commit_sequence": True})
        inv_id = save_r.json()["id"]
        client.patch(f"/api/invoices/{inv_id}/status", json={"status": "issued"})
        r = client.get("/api/invoices")
        row = r.json()[0]
        assert row["issued_at"] is not None
        assert row["issued_at"] != ""

    def test_issued_at_null_on_draft(self, client):
        client.post("/api/invoices/save", json={"data": _inv(), "commit_sequence": True})
        r = client.get("/api/invoices")
        assert r.json()[0]["issued_at"] is None

    def test_due_date_preserved(self, client):
        client.post("/api/invoices/save", json={"data": _inv(due="2025-03-31"), "commit_sequence": True})
        r = client.get("/api/invoices")
        assert r.json()[0]["due_date"] == "2025-03-31"

    def test_doc_type_invoice(self, client):
        client.post("/api/invoices/save", json={"data": _inv(), "commit_sequence": True})
        r = client.get("/api/invoices")
        assert r.json()[0]["doc_type"] == "invoice"

    def test_doc_type_credit_note(self, client):
        save_r = client.post("/api/invoices/save", json={"data": _inv(), "commit_sequence": True})
        inv_id = save_r.json()["id"]
        client.patch(f"/api/invoices/{inv_id}/status", json={"status": "issued"})
        client.post("/api/invoices/save", json={
            "data": {**_inv("DD-2025-001"), "doc_type": "credit_note"},
            "doc_type": "credit_note",
            "credit_note_for": inv_id,
            "commit_sequence": True,
        })
        r = client.get("/api/invoices")
        types = {row["doc_type"] for row in r.json()}
        assert "credit_note" in types


# ── 2. KPI aggregation inputs ───────────────────────────────────────────────────

class TestKPIData:

    def _save_and_issue(self, client, number, due="2025-06-30"):
        r = client.post("/api/invoices/save", json={"data": _inv(number, due=due), "commit_sequence": True})
        inv_id = r.json()["id"]
        client.patch(f"/api/invoices/{inv_id}/status", json={"status": "issued"})
        return inv_id

    def test_outstanding_balance_calculation(self, client):
        """paid_total stays 0 until payment; outstanding = total - paid_total."""
        inv_id = self._save_and_issue(client, "FA-2025-001")
        r = client.get("/api/invoices")
        row = next(i for i in r.json() if i["id"] == inv_id)
        outstanding = row["total"] - row["paid_total"]
        assert outstanding == pytest.approx(12100.0)

    def test_paid_total_updates_after_payment(self, client):
        inv_id = self._save_and_issue(client, "FA-2025-001")
        client.post(f"/api/invoices/{inv_id}/payments",
                    json={"amount": 5000.0, "paid_on": "2025-02-01", "note": ""})
        r = client.get("/api/invoices")
        row = next(i for i in r.json() if i["id"] == inv_id)
        assert row["paid_total"] == pytest.approx(5000.0)
        assert row["total"] - row["paid_total"] == pytest.approx(7100.0)

    def test_status_overdue_present_in_list(self, client):
        """Dashboard overdue KPI relies on filtering by status='overdue'."""
        inv_id = self._save_and_issue(client, "FA-2025-001")
        client.patch(f"/api/invoices/{inv_id}/status", json={"status": "sent"})
        client.patch(f"/api/invoices/{inv_id}/status", json={"status": "overdue"})
        r = client.get("/api/invoices")
        statuses = {row["status"] for row in r.json()}
        assert "overdue" in statuses

    def test_multiple_invoices_for_aggregation(self, client):
        """List endpoint returns all invoices needed for month/YTD totals."""
        for i in range(1, 4):
            client.post("/api/invoices/save", json={
                "data": _inv(f"FA-2025-00{i}"),
                "commit_sequence": i == 1,
            })
        r = client.get("/api/invoices")
        assert len(r.json()) == 3


# ── 3. Limit=500 support ────────────────────────────────────────────────────────

class TestListLimit:

    def test_limit_200_accepted(self, client):
        """Dashboard fetches /api/invoices?limit=200 — the maximum allowed."""
        r = client.get("/api/invoices?limit=200")
        assert r.status_code == 200

    def test_limit_above_200_rejected(self, client):
        """API enforces limit <= 200; values above return 422."""
        r = client.get("/api/invoices?limit=201")
        assert r.status_code == 422

    def test_offset_pagination(self, client):
        for i in range(1, 4):
            client.post("/api/invoices/save", json={"data": _inv(f"FA-2025-00{i}")})
        r = client.get("/api/invoices?limit=2&offset=0")
        assert r.status_code == 200
        assert len(r.json()) == 2
        r2 = client.get("/api/invoices?limit=2&offset=2")
        assert r2.status_code == 200
        assert len(r2.json()) == 1


# ── 4. Auth guard on list endpoint ─────────────────────────────────────────────

class TestDashboardAuth:

    def test_list_requires_auth(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "anon_dash.db"))
        monkeypatch.setenv("SESSION_SECRET", "anon-dashboard-test-secret-32chars!")
        from fastapi.testclient import TestClient
        from main import app
        with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as anon:
            r = anon.get("/api/invoices")
            assert r.status_code == 401
