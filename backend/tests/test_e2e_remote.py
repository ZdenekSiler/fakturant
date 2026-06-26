"""
tests/test_e2e_remote.py

End-to-end Playwright tests against the remote Fakturant instance.

Run against production:
    uv run pytest tests/test_e2e_remote.py -v

Run against local dev server:
    E2E_BASE_URL=http://localhost:8000 uv run pytest tests/test_e2e_remote.py -v

Credentials:
    E2E_EMAIL    (default: zd.siler@gmail.com)
    E2E_PASSWORD (default: Test1234!)
"""
import json
import os

import pytest
from playwright.sync_api import Page, expect

BASE_URL  = os.getenv("E2E_BASE_URL",  "https://fakturant.zdenovo.com")
E2E_EMAIL = os.getenv("E2E_EMAIL",     "zd.siler@gmail.com")
E2E_PASS  = os.getenv("E2E_PASSWORD",  "Test1234!")

pytestmark = pytest.mark.e2e

INVOICE_DATA = {
    "supplier": {"name": "E2E Supplier s.r.o.", "ico": "12345678", "address": "Test 1, Prague 110 00"},
    "customer": {"name": "E2E Customer s.r.o.", "address": "Test 2, Brno 600 00"},
    "items": [{"description": "E2E item", "quantity": 1, "unit_price": 1000, "vat_rate": 21}],
    "bank_account": "123456789/0800",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def login(page: Page) -> None:
    page.goto(f"{BASE_URL}/login")
    page.fill('input[name="email"]', E2E_EMAIL)
    page.fill('input[name="password"]', E2E_PASS)
    page.click('button[type="submit"]')
    page.wait_for_url(f"{BASE_URL}/", timeout=10_000)


def _save_draft(page: Page) -> int:
    resp = page.request.post(
        f"{BASE_URL}/api/invoices/save",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"data": INVOICE_DATA, "doc_type": "invoice", "commit_sequence": False}),
    )
    assert resp.status in (200, 201), f"Draft creation failed: {resp.status} — {resp.text()}"
    return resp.json()["id"]


def _patch_status(page: Page, inv_id: int, status: str) -> dict:
    resp = page.request.fetch(
        f"{BASE_URL}/api/invoices/{inv_id}/status",
        method="PATCH",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"status": status}),
    )
    assert resp.status == 200, f"Status PATCH failed: {resp.status} — {resp.text()}"
    return resp.json()


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_login_page_loads(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        expect(page.locator('input[name="email"]')).to_be_visible()
        expect(page.locator('input[name="password"]')).to_be_visible()
        expect(page.locator('button[type="submit"]')).to_be_visible()

    def test_login_wrong_password_shows_error(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        page.fill('input[name="email"]', E2E_EMAIL)
        page.fill('input[name="password"]', "wrongpassword")
        page.click('button[type="submit"]')
        expect(page.locator(".error-msg")).to_be_visible(timeout=5_000)

    def test_login_success_redirects_to_app(self, page: Page):
        login(page)
        expect(page).to_have_url(f"{BASE_URL}/", timeout=8_000)

    def test_logout_clears_session(self, page: Page):
        login(page)
        page.evaluate("() => { document.getElementById('logoutForm')?.submit(); }")
        page.wait_for_url(f"{BASE_URL}/login", timeout=8_000)
        resp = page.request.get(f"{BASE_URL}/api/invoices")
        assert resp.status in (401, 403)

    def test_unauthenticated_api_returns_401(self, page: Page):
        resp = page.request.get(f"{BASE_URL}/api/invoices")
        assert resp.status in (401, 403)


# ── Dashboard (Přehled) ───────────────────────────────────────────────────────

class TestDashboard:
    def test_dashboard_button_visible_after_login(self, page: Page):
        login(page)
        expect(page.locator("#dashboardBtn")).to_be_visible(timeout=5_000)

    def test_dashboard_opens_without_http_errors(self, page: Page):
        login(page)
        errors = []
        page.on("response", lambda r: errors.append((r.url, r.status)) if r.status >= 400 else None)
        page.locator("#dashboardBtn").click()
        page.wait_for_timeout(3_000)
        assert not errors, f"Dashboard triggered HTTP errors: {errors}"

    def test_dashboard_shows_kpi_values(self, page: Page):
        login(page)
        page.locator("#dashboardBtn").click()
        page.wait_for_timeout(2_000)
        expect(page.locator(".kpi-value").first).to_be_visible(timeout=5_000)

    def test_dashboard_api_limit_500(self, page: Page):
        login(page)
        resp = page.request.get(f"{BASE_URL}/api/invoices?limit=500")
        assert resp.status == 200
        assert isinstance(resp.json(), list)


# ── Invoice List ──────────────────────────────────────────────────────────────

class TestInvoiceList:
    def test_invoice_list_button_visible(self, page: Page):
        login(page)
        expect(page.locator("#invoiceListBtn")).to_be_visible(timeout=5_000)

    def test_new_invoice_button_visible(self, page: Page):
        login(page)
        expect(page.locator("button", has_text="Nová").first).to_be_visible(timeout=5_000)

    def test_invoice_list_api_returns_list(self, page: Page):
        login(page)
        resp = page.request.get(f"{BASE_URL}/api/invoices")
        assert resp.status == 200
        assert isinstance(resp.json(), list)

    def test_invoice_filter_by_type(self, page: Page):
        login(page)
        resp = page.request.get(f"{BASE_URL}/api/invoices?doc_type=invoice")
        assert resp.status == 200
        data = resp.json()
        assert all(r.get("doc_type") == "invoice" for r in data)


# ── Save & CRUD ───────────────────────────────────────────────────────────────

class TestSaveAndCRUD:
    def test_save_draft_returns_id(self, page: Page):
        login(page)
        inv_id = _save_draft(page)
        assert isinstance(inv_id, int) and inv_id > 0

    def test_get_invoice_by_id(self, page: Page):
        login(page)
        inv_id = _save_draft(page)
        resp = page.request.get(f"{BASE_URL}/api/invoices/{inv_id}")
        assert resp.status == 200
        data = resp.json()
        assert data["id"] == inv_id
        assert data["status"] == "draft"

    def test_get_nonexistent_invoice_returns_404(self, page: Page):
        login(page)
        resp = page.request.get(f"{BASE_URL}/api/invoices/999999")
        assert resp.status == 404

    def test_update_draft(self, page: Page):
        login(page)
        inv_id = _save_draft(page)
        updated = {**INVOICE_DATA, "notes": "Updated in e2e test"}
        resp = page.request.post(
            f"{BASE_URL}/api/invoices/save",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"data": updated, "invoice_id": inv_id, "commit_sequence": False}),
        )
        assert resp.status in (200, 201)
        assert resp.json()["id"] == inv_id

    def test_delete_draft(self, page: Page):
        login(page)
        inv_id = _save_draft(page)
        resp = page.request.delete(f"{BASE_URL}/api/invoices/{inv_id}")
        assert resp.status in (200, 204)
        assert page.request.get(f"{BASE_URL}/api/invoices/{inv_id}").status == 404

    def test_delete_nonexistent_returns_404(self, page: Page):
        login(page)
        resp = page.request.delete(f"{BASE_URL}/api/invoices/999999")
        assert resp.status == 404


# ── Invoice Lifecycle ─────────────────────────────────────────────────────────

class TestInvoiceLifecycle:
    def test_issue_invoice(self, page: Page):
        login(page)
        inv_id = _save_draft(page)
        data = _patch_status(page, inv_id, "issued")
        assert data["status"] == "issued"

    def test_issued_invoice_gets_sequence_number(self, page: Page):
        login(page)
        inv_id = _save_draft(page)
        data = _patch_status(page, inv_id, "issued")
        number = data.get("number") or data.get("invoice_number", "")
        assert number != ""

    def test_mark_as_sent(self, page: Page):
        login(page)
        inv_id = _save_draft(page)
        _patch_status(page, inv_id, "issued")
        data = _patch_status(page, inv_id, "sent")
        assert data["status"] == "sent"

    def test_mark_as_paid(self, page: Page):
        login(page)
        inv_id = _save_draft(page)
        _patch_status(page, inv_id, "issued")
        data = _patch_status(page, inv_id, "paid")
        assert data["status"] == "paid"

    def test_cancel_invoice(self, page: Page):
        login(page)
        inv_id = _save_draft(page)
        _patch_status(page, inv_id, "issued")
        data = _patch_status(page, inv_id, "cancelled")
        assert data["status"] == "cancelled"

    def test_invalid_status_returns_422(self, page: Page):
        login(page)
        inv_id = _save_draft(page)
        resp = page.request.fetch(
            f"{BASE_URL}/api/invoices/{inv_id}/status",
            method="PATCH",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"status": "nonexistent"}),
        )
        assert resp.status == 422


# ── Payments ──────────────────────────────────────────────────────────────────

class TestPayments:
    def test_record_payment_on_issued_invoice(self, page: Page):
        login(page)
        inv_id = _save_draft(page)
        _patch_status(page, inv_id, "issued")
        resp = page.request.post(
            f"{BASE_URL}/api/invoices/{inv_id}/payments",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"amount": 1210.0, "paid_on": "2026-06-26", "note": "E2E payment"}),
        )
        assert resp.status == 200

    def test_cannot_pay_draft(self, page: Page):
        login(page)
        inv_id = _save_draft(page)
        resp = page.request.post(
            f"{BASE_URL}/api/invoices/{inv_id}/payments",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"amount": 100.0, "paid_on": "2026-06-26"}),
        )
        assert resp.status == 400

    def test_negative_payment_returns_422(self, page: Page):
        login(page)
        inv_id = _save_draft(page)
        _patch_status(page, inv_id, "issued")
        resp = page.request.post(
            f"{BASE_URL}/api/invoices/{inv_id}/payments",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"amount": -100.0, "paid_on": "2026-06-26"}),
        )
        assert resp.status == 422


# ── Preview & PDF ─────────────────────────────────────────────────────────────

class TestPreviewAndPDF:
    def test_preview_renders_html(self, page: Page):
        login(page)
        resp = page.request.post(
            f"{BASE_URL}/preview",
            headers={"Content-Type": "application/json"},
            data=json.dumps(INVOICE_DATA),
        )
        assert resp.status == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_generate_pdf(self, page: Page):
        login(page)
        resp = page.request.post(
            f"{BASE_URL}/generate-pdf",
            headers={"Content-Type": "application/json"},
            data=json.dumps(INVOICE_DATA),
        )
        assert resp.status == 200
        assert "application/pdf" in resp.headers.get("content-type", "")

    def test_validate_empty_returns_errors(self, page: Page):
        login(page)
        resp = page.request.post(
            f"{BASE_URL}/validate",
            headers={"Content-Type": "application/json"},
            data=json.dumps({}),
        )
        assert resp.status == 200
        data = resp.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0

    def test_validate_complete_invoice_passes(self, page: Page):
        login(page)
        resp = page.request.post(
            f"{BASE_URL}/validate",
            headers={"Content-Type": "application/json"},
            data=json.dumps(INVOICE_DATA),
        )
        assert resp.status == 200
        assert resp.json()["valid"] is True


# ── ARES ──────────────────────────────────────────────────────────────────────

class TestAres:
    def test_lookup_known_company(self, page: Page):
        login(page)
        resp = page.request.get(f"{BASE_URL}/api/ares/ico/27082440")
        assert resp.status == 200
        assert resp.json() is not None

    def test_lookup_unknown_ico(self, page: Page):
        login(page)
        resp = page.request.get(f"{BASE_URL}/api/ares/ico/00000001")
        assert resp.status in (200, 404)


# ── Health & Security ─────────────────────────────────────────────────────────

class TestHealthAndSecurity:
    def test_health_endpoint(self, page: Page):
        resp = page.request.get(f"{BASE_URL}/health")
        assert resp.status == 200
        assert resp.json()["status"] == "ok"

    def test_no_js_errors_on_login_page(self, page: Page):
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE_URL}/login")
        page.wait_for_timeout(1_000)
        assert not errors, f"JS errors on login: {errors}"

    def test_no_js_errors_on_main_app(self, page: Page):
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        login(page)
        page.wait_for_timeout(3_000)
        assert not errors, f"JS errors on main app: {errors}"
