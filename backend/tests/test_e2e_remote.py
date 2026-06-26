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

Auth note: nginx rate-limits /auth/login to 10r/m burst=5. All tests after
TestAuth share a session-scoped browser context (login once) to avoid throttling.
"""
import json
import os

import pytest
from playwright.sync_api import Browser, Page, expect

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

# Full invoice data satisfying all validation rules
VALID_INVOICE_DATA = {
    **INVOICE_DATA,
    "invoice_number": "FA-2026-E2E",
    "issue_date": "2026-06-26",
    "due_date": "2026-07-10",
}


# ── Shared auth fixture (login once per test session) ─────────────────────────

@pytest.fixture(scope="session")
def authed_storage(browser: Browser):
    """Log in once and capture storage state (session cookie) for reuse."""
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(f"{BASE_URL}/login")
    page.fill('input[name="email"]', E2E_EMAIL)
    page.fill('input[name="password"]', E2E_PASS)
    page.click('button[type="submit"]')
    page.wait_for_url(f"{BASE_URL}/", timeout=15_000)
    storage = ctx.storage_state()
    ctx.close()
    return storage


@pytest.fixture()
def authed_page(browser: Browser, authed_storage):
    """Fresh page pre-seeded with the shared authenticated session cookie."""
    ctx = browser.new_context(storage_state=authed_storage)
    page = ctx.new_page()
    page.goto(f"{BASE_URL}/")
    yield page
    ctx.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── Auth (uses fresh unauthenticated page fixture) ────────────────────────────

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
        page.goto(f"{BASE_URL}/login")
        page.fill('input[name="email"]', E2E_EMAIL)
        page.fill('input[name="password"]', E2E_PASS)
        page.click('button[type="submit"]')
        page.wait_for_url(f"{BASE_URL}/", timeout=10_000)
        expect(page).to_have_url(f"{BASE_URL}/")

    def test_unauthenticated_api_returns_401(self, page: Page):
        resp = page.request.get(f"{BASE_URL}/api/invoices")
        assert resp.status in (401, 403)


# ── Dashboard (Přehled) ───────────────────────────────────────────────────────

class TestDashboard:
    def test_dashboard_button_visible_after_login(self, authed_page: Page):
        expect(authed_page.locator("#dashboardBtn")).to_be_visible(timeout=5_000)

    def test_dashboard_opens_without_http_errors(self, authed_page: Page):
        errors = []
        authed_page.on("response", lambda r: errors.append((r.url, r.status)) if r.status >= 400 else None)
        authed_page.locator("#dashboardBtn").click()
        authed_page.wait_for_timeout(3_000)
        assert not errors, f"Dashboard triggered HTTP errors: {errors}"

    def test_dashboard_shows_kpi_values(self, authed_page: Page):
        authed_page.locator("#dashboardBtn").click()
        authed_page.wait_for_timeout(2_000)
        expect(authed_page.locator(".kpi-value").first).to_be_visible(timeout=5_000)

    def test_dashboard_api_limit_500(self, authed_page: Page):
        resp = authed_page.request.get(f"{BASE_URL}/api/invoices?limit=500")
        assert resp.status == 200
        assert isinstance(resp.json(), list)


# ── Invoice List ──────────────────────────────────────────────────────────────

class TestInvoiceList:
    def test_invoice_list_button_visible(self, authed_page: Page):
        expect(authed_page.locator("#invoiceListBtn")).to_be_visible(timeout=5_000)

    def test_new_invoice_button_visible(self, authed_page: Page):
        expect(authed_page.locator("button", has_text="Nová").first).to_be_visible(timeout=5_000)

    def test_invoice_list_api_returns_list(self, authed_page: Page):
        resp = authed_page.request.get(f"{BASE_URL}/api/invoices")
        assert resp.status == 200
        assert isinstance(resp.json(), list)

    def test_invoice_filter_by_type(self, authed_page: Page):
        resp = authed_page.request.get(f"{BASE_URL}/api/invoices?doc_type=invoice")
        assert resp.status == 200
        data = resp.json()
        assert all(r.get("doc_type") == "invoice" for r in data)


# ── Save & CRUD ───────────────────────────────────────────────────────────────

class TestSaveAndCRUD:
    def test_save_draft_returns_id(self, authed_page: Page):
        inv_id = _save_draft(authed_page)
        assert isinstance(inv_id, int) and inv_id > 0

    def test_get_invoice_by_id(self, authed_page: Page):
        inv_id = _save_draft(authed_page)
        resp = authed_page.request.get(f"{BASE_URL}/api/invoices/{inv_id}")
        assert resp.status == 200
        data = resp.json()
        assert data["id"] == inv_id
        assert data["status"] == "draft"

    def test_get_nonexistent_invoice_returns_404(self, authed_page: Page):
        resp = authed_page.request.get(f"{BASE_URL}/api/invoices/999999")
        assert resp.status == 404

    def test_update_draft(self, authed_page: Page):
        inv_id = _save_draft(authed_page)
        updated = {**INVOICE_DATA, "notes": "Updated in e2e test"}
        resp = authed_page.request.post(
            f"{BASE_URL}/api/invoices/save",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"data": updated, "invoice_id": inv_id, "commit_sequence": False}),
        )
        assert resp.status in (200, 201)
        assert resp.json()["id"] == inv_id

    def test_delete_draft(self, authed_page: Page):
        inv_id = _save_draft(authed_page)
        resp = authed_page.request.delete(f"{BASE_URL}/api/invoices/{inv_id}")
        assert resp.status in (200, 204)
        assert authed_page.request.get(f"{BASE_URL}/api/invoices/{inv_id}").status == 404

    def test_delete_nonexistent_returns_404(self, authed_page: Page):
        resp = authed_page.request.delete(f"{BASE_URL}/api/invoices/999999")
        assert resp.status == 404


# ── Invoice Lifecycle ─────────────────────────────────────────────────────────

class TestInvoiceLifecycle:
    def test_issue_invoice(self, authed_page: Page):
        inv_id = _save_draft(authed_page)
        data = _patch_status(authed_page, inv_id, "issued")
        assert data["status"] == "issued"

    def test_issued_invoice_has_issued_at(self, authed_page: Page):
        inv_id = _save_draft(authed_page)
        data = _patch_status(authed_page, inv_id, "issued")
        assert data.get("issued_at") is not None

    def test_mark_as_sent(self, authed_page: Page):
        inv_id = _save_draft(authed_page)
        _patch_status(authed_page, inv_id, "issued")
        data = _patch_status(authed_page, inv_id, "sent")
        assert data["status"] == "sent"

    def test_mark_as_paid(self, authed_page: Page):
        inv_id = _save_draft(authed_page)
        _patch_status(authed_page, inv_id, "issued")
        data = _patch_status(authed_page, inv_id, "paid")
        assert data["status"] == "paid"

    def test_cancel_invoice(self, authed_page: Page):
        inv_id = _save_draft(authed_page)
        _patch_status(authed_page, inv_id, "issued")
        data = _patch_status(authed_page, inv_id, "cancelled")
        assert data["status"] == "cancelled"

    def test_invalid_status_returns_422(self, authed_page: Page):
        inv_id = _save_draft(authed_page)
        resp = authed_page.request.fetch(
            f"{BASE_URL}/api/invoices/{inv_id}/status",
            method="PATCH",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"status": "nonexistent"}),
        )
        assert resp.status == 422


# ── Payments ──────────────────────────────────────────────────────────────────

class TestPayments:
    def test_record_payment_on_issued_invoice(self, authed_page: Page):
        inv_id = _save_draft(authed_page)
        _patch_status(authed_page, inv_id, "issued")
        resp = authed_page.request.post(
            f"{BASE_URL}/api/invoices/{inv_id}/payments",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"amount": 1210.0, "paid_on": "2026-06-26", "note": "E2E payment"}),
        )
        assert resp.status == 200

    def test_cannot_pay_draft(self, authed_page: Page):
        inv_id = _save_draft(authed_page)
        resp = authed_page.request.post(
            f"{BASE_URL}/api/invoices/{inv_id}/payments",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"amount": 100.0, "paid_on": "2026-06-26"}),
        )
        assert resp.status == 400

    def test_negative_payment_returns_422(self, authed_page: Page):
        inv_id = _save_draft(authed_page)
        _patch_status(authed_page, inv_id, "issued")
        resp = authed_page.request.post(
            f"{BASE_URL}/api/invoices/{inv_id}/payments",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"amount": -100.0, "paid_on": "2026-06-26"}),
        )
        assert resp.status == 422


# ── Preview & PDF ─────────────────────────────────────────────────────────────

class TestPreviewAndPDF:
    def test_preview_renders_html(self, authed_page: Page):
        resp = authed_page.request.post(
            f"{BASE_URL}/preview",
            headers={"Content-Type": "application/json"},
            data=json.dumps(INVOICE_DATA),
        )
        assert resp.status == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_generate_pdf(self, authed_page: Page):
        resp = authed_page.request.post(
            f"{BASE_URL}/generate-pdf",
            headers={"Content-Type": "application/json"},
            data=json.dumps(INVOICE_DATA),
        )
        assert resp.status == 200
        assert "application/pdf" in resp.headers.get("content-type", "")

    def test_validate_empty_returns_errors(self, authed_page: Page):
        resp = authed_page.request.post(
            f"{BASE_URL}/validate",
            headers={"Content-Type": "application/json"},
            data=json.dumps({}),
        )
        assert resp.status == 200
        data = resp.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0

    def test_validate_complete_invoice_passes(self, authed_page: Page):
        resp = authed_page.request.post(
            f"{BASE_URL}/validate",
            headers={"Content-Type": "application/json"},
            data=json.dumps(VALID_INVOICE_DATA),
        )
        assert resp.status == 200
        assert resp.json()["valid"] is True


# ── ARES ──────────────────────────────────────────────────────────────────────

class TestAres:
    def test_lookup_known_company(self, authed_page: Page):
        resp = authed_page.request.get(f"{BASE_URL}/api/ares/ico/27082440")
        assert resp.status == 200
        assert resp.json() is not None

    def test_lookup_unknown_ico(self, authed_page: Page):
        resp = authed_page.request.get(f"{BASE_URL}/api/ares/ico/00000001")
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

    def test_no_js_errors_on_main_app(self, authed_page: Page):
        errors = []
        authed_page.on("pageerror", lambda e: errors.append(str(e)))
        authed_page.wait_for_timeout(3_000)
        assert not errors, f"JS errors on main app: {errors}"

    def test_logout_clears_session(self, authed_page: Page):
        authed_page.evaluate("() => { document.getElementById('logoutForm')?.submit(); }")
        authed_page.wait_for_url(f"{BASE_URL}/login", timeout=8_000)
        resp = authed_page.request.get(f"{BASE_URL}/api/invoices")
        assert resp.status in (401, 403)
