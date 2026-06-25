"""
tests/test_invoicing_features.py
Tests for the four core invoicing gap features:
  1. Sequential numbering (next_number, peek, gap check)
  2. Status lifecycle (transitions, overdue scan)
  3. Payment recording (add, delete, auto-paid)
  4. Credit notes (create, negative amounts, reference)
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "features_test.db"))


@pytest.fixture
def base_data() -> dict:
    return {
        "template": "modern",
        "invoice_number": "FA-2025-001",
        "issue_date": "2025-01-10",
        "duzp":       "2025-01-10",
        "due_date":   "2025-01-24",
        "currency":   "CZK",
        "bank_account": "1234567890/0800",
        "variable_symbol": "20250001",
        "iban": "", "swift": "", "notes": "", "logo_b64": None,
        "supplier": {"name":"Acme s.r.o.","ico":"27766383","dic":"CZ27766383",
                     "address":"Praha","email":"x@x.cz","vat_payer":True},
        "customer": {"name":"Klient a.s.","ico":"45272956","dic":"CZ45272956",
                     "address":"Brno","email":"y@y.cz","vat_payer":True},
        "items": [
            {"description":"Vývoj","project":"P1","item_date":"2025-01-10",
             "quantity":10,"unit":"hod","unit_price":2000.0,"vat_rate":21.0},
        ],
    }


# ═══════════════════════════════════════════════════════════
# 1. SEQUENTIAL NUMBERING
# ═══════════════════════════════════════════════════════════

class TestSequentialNumbering:

    @pytest.mark.asyncio
    async def test_first_number_is_001(self):
        from services.db import init_db, next_number
        await init_db()
        n = await next_number("FA", 2025)
        assert n == "FA-2025-001"

    @pytest.mark.asyncio
    async def test_increments_on_each_call(self):
        from services.db import init_db, next_number
        await init_db()
        n1 = await next_number("FA", 2025)
        n2 = await next_number("FA", 2025)
        n3 = await next_number("FA", 2025)
        assert n1 == "FA-2025-001"
        assert n2 == "FA-2025-002"
        assert n3 == "FA-2025-003"

    @pytest.mark.asyncio
    async def test_separate_prefix_separate_counter(self):
        from services.db import init_db, next_number
        await init_db()
        fa = await next_number("FA", 2025)
        dd = await next_number("DD", 2025)
        fa2 = await next_number("FA", 2025)
        assert fa  == "FA-2025-001"
        assert dd  == "DD-2025-001"
        assert fa2 == "FA-2025-002"

    @pytest.mark.asyncio
    async def test_separate_year_separate_counter(self):
        from services.db import init_db, next_number
        await init_db()
        n25 = await next_number("FA", 2025)
        n26 = await next_number("FA", 2026)
        assert n25 == "FA-2025-001"
        assert n26 == "FA-2026-001"

    @pytest.mark.asyncio
    async def test_peek_does_not_increment(self):
        from services.db import init_db, next_number, peek_next_number
        await init_db()
        peeked = await peek_next_number("FA", 2025)
        actual = await next_number("FA", 2025)
        assert peeked == actual == "FA-2025-001"
        peeked2 = await peek_next_number("FA", 2025)
        assert peeked2 == "FA-2025-002"  # counter advanced by next_number above

    @pytest.mark.asyncio
    async def test_gap_check_correct_number(self):
        from services.db import check_sequence_gap, init_db, next_number
        await init_db()
        await next_number("FA", 2025)   # counter is now 1
        result = await check_sequence_gap("FA-2025-002")
        assert result["ok"] is True
        assert result["gap"] == 0

    @pytest.mark.asyncio
    async def test_gap_check_detects_skip(self):
        from services.db import check_sequence_gap, init_db, next_number
        await init_db()
        await next_number("FA", 2025)   # counter = 1; expected next = 2
        result = await check_sequence_gap("FA-2025-005")
        assert result["ok"] is False
        assert result["gap"] == 3
        assert result["expected"] == "FA-2025-002"

    @pytest.mark.asyncio
    async def test_gap_check_detects_duplicate(self):
        from services.db import check_sequence_gap, init_db, next_number
        await init_db()
        await next_number("FA", 2025)
        await next_number("FA", 2025)   # counter = 2
        result = await check_sequence_gap("FA-2025-001")
        assert result["ok"] is False
        assert result["gap"] < 0

    @pytest.mark.asyncio
    async def test_gap_check_unparseable_is_ok(self):
        from services.db import check_sequence_gap, init_db
        await init_db()
        result = await check_sequence_gap("CUSTOM-NUMBER")
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_zero_padded_three_digits(self):
        from services.db import init_db, next_number
        await init_db()
        n = await next_number("FA", 2025)
        assert n == "FA-2025-001"


# ═══════════════════════════════════════════════════════════
# 2. STATUS LIFECYCLE
# ═══════════════════════════════════════════════════════════

class TestStatusLifecycle:

    @pytest.mark.asyncio
    async def test_default_status_draft(self, base_data):
        from services.db import init_db, save_invoice
        await init_db()
        row = await save_invoice(base_data)
        assert row["status"] == "draft"

    @pytest.mark.asyncio
    async def test_transition_to_issued(self, base_data):
        from services.db import get_invoice, init_db, save_invoice, update_status
        await init_db()
        row = await save_invoice(base_data)
        updated = await update_status(row["id"], "issued")
        assert updated["status"] == "issued"
        assert updated["issued_at"] is not None

    @pytest.mark.asyncio
    async def test_issued_at_only_set_on_issued(self, base_data):
        from services.db import init_db, save_invoice, update_status
        await init_db()
        row = await save_invoice(base_data)
        assert row.get("issued_at") is None
        await update_status(row["id"], "issued")
        await update_status(row["id"], "sent")
        loaded = await update_status(row["id"], "paid")
        assert loaded["issued_at"] is not None  # preserved from issued transition

    @pytest.mark.asyncio
    async def test_all_valid_transitions(self, base_data):
        from services.db import init_db, save_invoice, update_status
        await init_db()
        for status in ("draft", "issued", "sent", "paid", "overdue", "cancelled"):
            row = await save_invoice({**base_data, "invoice_number": f"FA-{status}"})
            result = await update_status(row["id"], status)
            assert result["status"] == status

    @pytest.mark.asyncio
    async def test_invalid_status_raises(self, base_data):
        from services.db import init_db, save_invoice, update_status
        await init_db()
        row = await save_invoice(base_data)
        with pytest.raises(ValueError, match="Invalid status"):
            await update_status(row["id"], "flying")

    @pytest.mark.asyncio
    async def test_mark_overdue_flips_sent_past_due(self, base_data):
        from services.db import init_db, save_invoice, mark_overdue, update_status
        await init_db()
        past_data = {**base_data, "due_date": "2020-01-01"}  # clearly in the past
        row = await save_invoice(past_data)
        await update_status(row["id"], "issued")
        await update_status(row["id"], "sent")
        count = await mark_overdue()
        assert count >= 1
        from services.db import get_invoice
        refreshed = await get_invoice(row["id"])
        assert refreshed["status"] == "overdue"

    @pytest.mark.asyncio
    async def test_mark_overdue_ignores_paid(self, base_data):
        from services.db import get_invoice, init_db, mark_overdue, save_invoice, update_status
        await init_db()
        past_data = {**base_data, "due_date": "2020-01-01"}
        row = await save_invoice(past_data)
        await update_status(row["id"], "issued")
        await update_status(row["id"], "sent")
        await update_status(row["id"], "paid")
        await mark_overdue()
        refreshed = await get_invoice(row["id"])
        assert refreshed["status"] == "paid"   # must not flip paid → overdue

    @pytest.mark.asyncio
    async def test_mark_overdue_ignores_future_due(self, base_data):
        from services.db import get_invoice, init_db, mark_overdue, save_invoice, update_status
        await init_db()
        future_data = {**base_data, "due_date": "2099-12-31"}
        row = await save_invoice(future_data)
        await update_status(row["id"], "issued")
        await update_status(row["id"], "sent")
        await mark_overdue()
        refreshed = await get_invoice(row["id"])
        assert refreshed["status"] == "sent"   # not overdue yet


# ═══════════════════════════════════════════════════════════
# 3. PAYMENT RECORDING
# ═══════════════════════════════════════════════════════════

class TestPaymentRecording:

    @pytest.mark.asyncio
    async def test_add_payment(self, base_data):
        from services.db import add_payment, init_db, save_invoice, update_status
        await init_db()
        row = await save_invoice(base_data)
        await update_status(row["id"], "issued")
        result = await add_payment(row["id"], 5000.0, "2025-01-20", "first payment")
        assert len(result["payments"]) == 1
        assert result["payments"][0]["amount"] == 5000.0
        assert result["paid_total"] == 5000.0

    @pytest.mark.asyncio
    async def test_multiple_payments_accumulate(self, base_data):
        from services.db import add_payment, init_db, save_invoice, update_status
        await init_db()
        row = await save_invoice(base_data)
        await update_status(row["id"], "issued")
        await add_payment(row["id"], 5000.0, "2025-01-15")
        result = await add_payment(row["id"], 5000.0, "2025-01-20")
        assert result["paid_total"] == 10000.0
        assert len(result["payments"]) == 2

    @pytest.mark.asyncio
    async def test_auto_paid_when_full_amount(self, base_data):
        from services.db import add_payment, init_db, save_invoice, update_status
        await init_db()
        row = await save_invoice(base_data)
        await update_status(row["id"], "issued")
        total = row["total"]   # 10 × 2000 × 1.21 = 24200
        result = await add_payment(row["id"], total, "2025-01-20")
        assert result["status"] == "paid"

    @pytest.mark.asyncio
    async def test_partial_payment_stays_issued(self, base_data):
        from services.db import add_payment, init_db, save_invoice, update_status
        await init_db()
        row = await save_invoice(base_data)
        await update_status(row["id"], "issued")
        result = await add_payment(row["id"], 1000.0, "2025-01-15")
        assert result["status"] != "paid"
        assert result["paid_total"] == 1000.0

    @pytest.mark.asyncio
    async def test_delete_payment_recalculates(self, base_data):
        from services.db import add_payment, delete_payment, init_db, save_invoice, update_status
        await init_db()
        row = await save_invoice(base_data)
        await update_status(row["id"], "issued")
        r = await add_payment(row["id"], 5000.0, "2025-01-15")
        pid = r["payments"][0]["id"]
        await add_payment(row["id"], 3000.0, "2025-01-20")
        after_del = await delete_payment(pid, row["id"])
        assert after_del["paid_total"] == 3000.0
        assert len(after_del["payments"]) == 1

    @pytest.mark.asyncio
    async def test_payment_on_draft_raises(self, base_data):
        from services.db import add_payment, init_db, save_invoice
        await init_db()
        row = await save_invoice(base_data)
        with pytest.raises(ValueError, match="draft"):
            await add_payment(row["id"], 100.0, "2025-01-15")

    @pytest.mark.asyncio
    async def test_total_computed_correctly(self, base_data):
        from services.db import init_db, save_invoice
        await init_db()
        row = await save_invoice(base_data)
        # 10 hod × 2000 Kč = 20000 základ + 21% DPH = 24200 total
        assert row["total"] == 24200.0


# ═══════════════════════════════════════════════════════════
# 4. CREDIT NOTES
# ═══════════════════════════════════════════════════════════

class TestCreditNotes:

    @pytest.mark.asyncio
    async def test_create_credit_note_returns_draft(self, base_data):
        from services.db import create_credit_note, init_db, save_invoice
        await init_db()
        row = await save_invoice(base_data)
        cn = await create_credit_note(row["id"])
        assert cn["doc_type"] == "credit_note"
        assert cn["credit_note_for"] == row["id"]
        assert cn["original_number"] == base_data["invoice_number"]

    @pytest.mark.asyncio
    async def test_credit_note_number_uses_dd_prefix(self, base_data):
        from services.db import create_credit_note, init_db, save_invoice
        await init_db()
        row = await save_invoice(base_data)
        cn = await create_credit_note(row["id"])
        assert cn["suggested_number"].startswith("DD-")

    @pytest.mark.asyncio
    async def test_credit_note_items_negated(self, base_data):
        from services.db import create_credit_note, init_db, save_invoice
        await init_db()
        row = await save_invoice(base_data)
        cn = await create_credit_note(row["id"])
        for item in cn["data"]["items"]:
            assert item["unit_price"] <= 0, "Credit note items must have negative unit_price"

    @pytest.mark.asyncio
    async def test_credit_note_for_nonexistent_raises(self):
        from services.db import create_credit_note, init_db
        await init_db()
        with pytest.raises(ValueError, match="not found"):
            await create_credit_note(99999)

    @pytest.mark.asyncio
    async def test_save_credit_note(self, base_data):
        from services.db import create_credit_note, init_db, save_invoice
        await init_db()
        original = await save_invoice(base_data)
        cn = await create_credit_note(original["id"])
        saved_cn = await save_invoice(
            cn["data"],
            doc_type="credit_note",
            credit_note_for=original["id"],
        )
        assert saved_cn["doc_type"] == "credit_note"
        assert saved_cn["credit_note_for"] == original["id"]

    @pytest.mark.asyncio
    async def test_credit_note_listed_separately(self, base_data):
        from services.db import create_credit_note, init_db, list_invoices, save_invoice
        await init_db()
        orig = await save_invoice(base_data)
        cn   = await create_credit_note(orig["id"])
        await save_invoice(cn["data"], doc_type="credit_note", credit_note_for=orig["id"])
        invoices = await list_invoices(doc_type="invoice")
        cn_list  = await list_invoices(doc_type="credit_note")
        assert len(invoices) == 1
        assert len(cn_list)  == 1

    @pytest.mark.asyncio
    async def test_credit_note_total_is_negative(self, base_data):
        from services.db import create_credit_note, init_db, save_invoice
        await init_db()
        orig = await save_invoice(base_data)
        cn   = await create_credit_note(orig["id"])
        saved_cn = await save_invoice(cn["data"], doc_type="credit_note", credit_note_for=orig["id"])
        assert saved_cn["total"] <= 0, "Credit note total should be negative or zero"


# ═══════════════════════════════════════════════════════════
# API INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api_features.db"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-for-pytest-minimum-32chars!")
    monkeypatch.setenv("ALLOW_SIGNUP", "true")
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as c:
        resp = c.post("/auth/signup", data={
            "email": "test@test.com",
            "password": "testpass123",
            "confirm": "testpass123",
        })
        assert resp.status_code == 303, f"Signup failed: {resp.status_code} {resp.text[:200]}"
        yield c


def _inv_payload(number="FA-2025-001", due="2025-01-24"):
    return {
        "template":"modern","invoice_number":number,
        "issue_date":"2025-01-10","duzp":"2025-01-10","due_date":due,
        "currency":"CZK","bank_account":"1234/0800","variable_symbol":"1","iban":"","swift":"","notes":"","logo_b64":None,
        "supplier":{"name":"A s.r.o.","ico":"27766383","dic":"CZ27766383","address":"Praha","email":"a@a.cz","vat_payer":True},
        "customer":{"name":"B a.s.","ico":"45272956","dic":"CZ45272956","address":"Brno","email":"b@b.cz","vat_payer":True},
        "items":[{"description":"X","project":"","item_date":"","quantity":1,"unit":"ks","unit_price":1000.0,"vat_rate":21.0}],
    }


class TestAPISequence:
    def test_next_number_endpoint(self, client):
        resp = client.get("/api/sequence/next?prefix=FA&year=2025")
        assert resp.status_code == 200
        assert resp.json()["number"] == "FA-2025-001"

    def test_check_gap_no_gap(self, client):
        resp = client.get("/api/sequence/check?number=FA-2025-001")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_check_gap_detects_skip(self, client):
        # Advance counter to 2, then check 5
        client.get("/api/sequence/next?prefix=FA&year=2025")
        client.get("/api/sequence/next?prefix=FA&year=2025")
        resp = client.get("/api/sequence/check?number=FA-2025-005")
        assert resp.json()["ok"] is False


class TestAPIStatus:
    def test_transition_to_issued(self, client):
        saved = client.post("/api/invoices/save", json={"data":_inv_payload()}).json()
        resp = client.patch(f"/api/invoices/{saved['id']}/status", json={"status":"issued"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "issued"

    def test_invalid_status_returns_422(self, client):
        saved = client.post("/api/invoices/save", json={"data":_inv_payload()}).json()
        resp = client.patch(f"/api/invoices/{saved['id']}/status", json={"status":"flying"})
        assert resp.status_code == 422

    def test_mark_overdue_endpoint(self, client):
        resp = client.post("/api/invoices/mark-overdue")
        assert resp.status_code == 200
        assert "marked_overdue" in resp.json()


class TestAPIPayments:
    def test_add_payment(self, client):
        saved = client.post("/api/invoices/save", json={"data":_inv_payload()}).json()
        client.patch(f"/api/invoices/{saved['id']}/status", json={"status":"issued"})
        resp = client.post(f"/api/invoices/{saved['id']}/payments",
                           json={"amount":500.0,"paid_on":"2025-01-20","note":"test"})
        assert resp.status_code == 200
        assert resp.json()["paid_total"] == 500.0

    def test_payment_on_draft_rejected(self, client):
        saved = client.post("/api/invoices/save", json={"data":_inv_payload()}).json()
        resp = client.post(f"/api/invoices/{saved['id']}/payments",
                           json={"amount":100.0,"paid_on":"2025-01-20"})
        assert resp.status_code == 400

    def test_delete_payment(self, client):
        saved = client.post("/api/invoices/save", json={"data":_inv_payload()}).json()
        client.patch(f"/api/invoices/{saved['id']}/status", json={"status":"issued"})
        pr = client.post(f"/api/invoices/{saved['id']}/payments",
                         json={"amount":200.0,"paid_on":"2025-01-20"}).json()
        pid = pr["payments"][0]["id"]
        resp = client.delete(f"/api/invoices/{saved['id']}/payments/{pid}")
        assert resp.status_code == 200
        assert resp.json()["paid_total"] == 0.0

    def test_zero_amount_rejected(self, client):
        saved = client.post("/api/invoices/save", json={"data":_inv_payload()}).json()
        client.patch(f"/api/invoices/{saved['id']}/status", json={"status":"issued"})
        resp = client.post(f"/api/invoices/{saved['id']}/payments",
                           json={"amount":0,"paid_on":"2025-01-20"})
        assert resp.status_code == 422


class TestAPICreditNote:
    def test_create_credit_note_endpoint(self, client):
        saved = client.post("/api/invoices/save", json={"data":_inv_payload()}).json()
        resp = client.post(f"/api/invoices/{saved['id']}/credit-note")
        assert resp.status_code == 200
        d = resp.json()
        assert d["doc_type"] == "credit_note"
        assert d["credit_note_for"] == saved["id"]
        assert d["suggested_number"].startswith("DD-")

    def test_credit_note_for_nonexistent(self, client):
        resp = client.post("/api/invoices/99999/credit-note")
        assert resp.status_code == 404
