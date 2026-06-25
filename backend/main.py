"""
Fakturant – Czech invoice generator
FastAPI backend: persistence + sequential numbering + payments + credit notes.

In production, nginx serves static assets and the SPA; the backend serves API only.
In local dev, set FRONTEND_DIR=../frontend to have FastAPI serve the SPA too.
"""
from __future__ import annotations

import io
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, field_validator

from auth import current_user_id, make_auth_middleware, router as auth_router
from models import InvoiceData
from services.ares import get_by_ico, search_by_name
from services.db import (
    add_payment,
    advance_sequence,
    check_duplicate_number,
    check_sequence_gap,
    create_credit_note,
    delete_contact,
    delete_invoice,
    delete_payment,
    get_invoice,
    get_user_profile,
    init_db,
    list_contacts,
    list_invoices,
    mark_overdue,
    next_number,
    peek_next_number,
    save_invoice,
    save_user_profile,
    update_status,
    upsert_contact,
)
from services.qr import generate_qr_b64

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).parent


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    from config import read_secret
    secret = read_secret("fakturant_session_secret", "SESSION_SECRET")
    if len(secret) < 32:
        sys.exit(
            "FATAL: SESSION_SECRET is missing or too short (need ≥ 32 chars). "
            "Generate one: python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )
    await init_db()
    overdue_count = await mark_overdue()
    if overdue_count:
        logger.warning("Marked %d invoice(s) as overdue on startup", overdue_count)
    yield


# ── App ───────────────────────────────────────────────────────────────────────

_is_prod = os.environ.get("ALLOWED_ORIGIN", "*") != "*"

app = FastAPI(
    title="Fakturant CZ",
    version="4.0.0",
    docs_url=None if _is_prod else "/api/docs",
    redoc_url=None if _is_prod else "/api/redoc",
    lifespan=lifespan,
)

_allowed_origin = os.environ.get("ALLOWED_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_allowed_origin],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=_allowed_origin != "*",
)

_jinja = Environment(
    loader=FileSystemLoader(BASE_DIR / "templates"),
    autoescape=select_autoescape(["html"]),
)
_jinja.filters["czk"] = lambda v: f"{v:,.2f}".replace(",", " ").replace(".", ",")
app.state.jinja = _jinja
app.middleware("http")(make_auth_middleware())
app.include_router(auth_router)

# Dev-only: serve the SPA from the local frontend directory when FRONTEND_DIR is set
_frontend_dir = Path(os.environ["FRONTEND_DIR"]) if "FRONTEND_DIR" in os.environ else None
if _frontend_dir and _frontend_dir.exists():
    app.mount("/static/css", StaticFiles(directory=_frontend_dir / "src"), name="css")
    app.mount("/static/js",  StaticFiles(directory=_frontend_dir / "src"), name="js")

    @app.get("/", response_class=HTMLResponse)
    async def _spa_index() -> HTMLResponse:
        return HTMLResponse((_frontend_dir / "index.html").read_text(encoding="utf-8"))


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "4.0.0"}


# ── User profile ──────────────────────────────────────────────────────────────

@app.get("/api/user/profile")
async def api_get_profile(user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return await get_user_profile(user_id)


@app.put("/api/user/profile")
async def api_save_profile(
    profile: dict[str, Any],
    user_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    await save_user_profile(user_id, profile)
    return {"ok": True}


# ── Contacts ──────────────────────────────────────────────────────────────────

class ContactIn(BaseModel):
    name:    str = ""
    ico:     str = ""
    dic:     str = ""
    address: str = ""
    email:   str = ""
    phone:   str = ""


@app.get("/api/contacts")
async def api_list_contacts(user_id: int = Depends(current_user_id)) -> list[dict[str, Any]]:
    return await list_contacts(user_id)


@app.post("/api/contacts")
async def api_create_contact(
    data:    ContactIn,
    user_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    return await upsert_contact(user_id, data.model_dump())


@app.put("/api/contacts/{contact_id}")
async def api_update_contact(
    contact_id: int,
    data:       ContactIn,
    user_id:    int = Depends(current_user_id),
) -> dict[str, Any]:
    return await upsert_contact(user_id, data.model_dump(), contact_id=contact_id)


@app.delete("/api/contacts/{contact_id}")
async def api_delete_contact(
    contact_id: int,
    user_id:    int = Depends(current_user_id),
) -> dict[str, Any]:
    await delete_contact(user_id, contact_id)
    return {"ok": True}


# ── ARES (public) ─────────────────────────────────────────────────────────────

@app.get("/api/ares/ico/{ico}")
async def ares_lookup_ico(ico: str) -> dict:
    ico = ico.strip()
    if not ico.isdigit() or len(ico) > 8:
        raise HTTPException(400, "IČO must be 1-8 digits")
    result = await get_by_ico(ico)
    if result is None:
        raise HTTPException(404, f"IČO '{ico}' not found in ARES")
    return result.model_dump()


@app.get("/api/ares/search")
async def ares_search(
    q: str = Query(..., min_length=2),
    n: int = Query(8, ge=1, le=20),
) -> list[dict]:
    return [r.model_dump() for r in await search_by_name(q.strip(), limit=n)]


# ── Sequence (public — needed for new invoice form without login) ─────────────

@app.get("/api/sequence/next")
async def api_next_number(
    prefix:  str = Query("FA", min_length=1, max_length=10),
    year:    int = Query(None),
    request: Request = None,
) -> dict[str, str]:
    uid = getattr(request.state, "user_id", None) or 0
    number = await peek_next_number(prefix=prefix.upper(), year=year, user_id=uid)
    return {"number": number}


@app.get("/api/sequence/check")
async def api_check_gap(
    number:  str = Query(...),
    request: Request = None,
) -> dict[str, Any]:
    uid = getattr(request.state, "user_id", None) or 0
    return await check_sequence_gap(number, user_id=uid)


# ── Invoice list & fetch ──────────────────────────────────────────────────────

@app.get("/api/invoices")
async def api_list(
    limit:    int         = Query(50, ge=1, le=200),
    offset:   int         = Query(0,  ge=0),
    doc_type: str | None  = Query(None),
    user_id:  int         = Depends(current_user_id),
) -> list[dict[str, Any]]:
    return await list_invoices(limit=limit, offset=offset, doc_type=doc_type, user_id=user_id)


@app.get("/api/invoices/{invoice_id}")
async def api_get(
    invoice_id: int,
    user_id:    int = Depends(current_user_id),
) -> dict[str, Any]:
    row = await get_invoice(invoice_id, user_id=user_id)
    if row is None:
        raise HTTPException(404, f"Invoice {invoice_id} not found")
    return row


@app.delete("/api/invoices/{invoice_id}", status_code=204)
async def api_delete(
    invoice_id: int,
    user_id:    int = Depends(current_user_id),
) -> None:
    row = await get_invoice(invoice_id, user_id=user_id)
    if row is None:
        raise HTTPException(404, f"Invoice {invoice_id} not found")
    if row["status"] != "draft":
        raise HTTPException(400, f"Cannot delete a '{row['status']}' invoice — cancel it (Stornováno) first")
    deleted = await delete_invoice(invoice_id, user_id=user_id)
    if not deleted:
        raise HTTPException(404, f"Invoice {invoice_id} not found")


# ── Save ──────────────────────────────────────────────────────────────────────

class SaveRequest(BaseModel):
    data:            dict[str, Any]
    invoice_id:      int | None = None
    doc_type:        str        = "invoice"
    credit_note_for: int | None = None
    commit_sequence: bool       = False

    @field_validator("doc_type")
    @classmethod
    def valid_doc_type(cls, v: str) -> str:
        if v not in ("invoice", "credit_note"):
            raise ValueError(f"Invalid doc_type: {v}")
        return v


@app.post("/api/invoices/save")
async def api_save(
    req:     SaveRequest,
    user_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    try:
        InvoiceData(**req.data)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc

    data   = dict(req.data)
    number = data.get("invoice_number", "")

    # Guard: reject duplicate invoice numbers (excluding self on edits)
    if number:
        conflict = await check_duplicate_number(number, user_id, exclude_id=req.invoice_id)
        if conflict:
            raise HTTPException(409, f"Číslo faktury '{number}' již existuje (faktura #{conflict})")

    # Commit sequence on first save of a new invoice
    if req.invoice_id is None and req.commit_sequence and number:
        prefix_match = number.split("-")[0] if "-" in number else "FA"
        year_match   = int(number.split("-")[1]) if number.count("-") >= 2 else None
        await next_number(prefix=prefix_match, year=year_match, user_id=user_id)

    # Always advance the counter so manual number edits never create future collisions
    if number:
        await advance_sequence(number, user_id)

    return await save_invoice(
        data,
        invoice_id=req.invoice_id,
        doc_type=req.doc_type,
        credit_note_for=req.credit_note_for,
        user_id=user_id,
    )


# ── Status lifecycle ──────────────────────────────────────────────────────────

class StatusRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, v: str) -> str:
        valid = {"draft", "issued", "sent", "paid", "overdue", "cancelled"}
        if v not in valid:
            raise ValueError(f"Invalid status '{v}'. Must be one of: {valid}")
        return v


@app.patch("/api/invoices/{invoice_id}/status")
async def api_update_status(
    invoice_id: int,
    req:        StatusRequest,
    user_id:    int = Depends(current_user_id),
) -> dict[str, Any]:
    row = await get_invoice(invoice_id, user_id=user_id)
    if row is None:
        raise HTTPException(404, f"Invoice {invoice_id} not found")
    return await update_status(invoice_id, req.status, user_id=user_id)


@app.post("/api/invoices/mark-overdue")
async def api_mark_overdue(
    user_id: int = Depends(current_user_id),
) -> dict[str, int]:
    count = await mark_overdue()
    return {"marked_overdue": count}


# ── Payments ──────────────────────────────────────────────────────────────────

class PaymentRequest(BaseModel):
    amount:  float
    paid_on: str
    note:    str = ""

    @field_validator("amount")
    @classmethod
    def positive_amount(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Payment amount must be > 0")
        return v


@app.post("/api/invoices/{invoice_id}/payments")
async def api_add_payment(
    invoice_id: int,
    req:        PaymentRequest,
    user_id:    int = Depends(current_user_id),
) -> dict[str, Any]:
    row = await get_invoice(invoice_id, user_id=user_id)
    if row is None:
        raise HTTPException(404, f"Invoice {invoice_id} not found")
    if row["status"] in ("draft", "cancelled"):
        raise HTTPException(400, f"Cannot record payment for a {row['status']} invoice")
    return await add_payment(invoice_id, req.amount, req.paid_on, req.note)


@app.delete("/api/invoices/{invoice_id}/payments/{payment_id}", status_code=200)
async def api_delete_payment(
    invoice_id: int,
    payment_id: int,
    user_id:    int = Depends(current_user_id),
) -> dict[str, Any]:
    row = await get_invoice(invoice_id, user_id=user_id)
    if row is None:
        raise HTTPException(404, f"Invoice {invoice_id} not found")
    return await delete_payment(payment_id, invoice_id)


# ── Credit notes ──────────────────────────────────────────────────────────────

@app.post("/api/invoices/{invoice_id}/credit-note")
async def api_create_credit_note(
    invoice_id: int,
    user_id:    int = Depends(current_user_id),
) -> dict[str, Any]:
    try:
        return await create_credit_note(invoice_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


# ── Render (public) ───────────────────────────────────────────────────────────

@app.post("/preview", response_class=HTMLResponse)
async def preview(data: InvoiceData) -> HTMLResponse:
    return HTMLResponse(_render_invoice(data))


@app.post("/validate")
async def validate(data: InvoiceData) -> dict[str, bool | list[str]]:
    errors = data.validation_errors()
    return {"valid": len(errors) == 0, "errors": errors}


@app.post("/generate-pdf")
async def generate_pdf(data: InvoiceData) -> StreamingResponse:
    html = _render_invoice(data)
    try:
        import weasyprint  # noqa: PLC0415
    except ImportError as err:
        raise HTTPException(500, "weasyprint not installed — run: uv sync") from err
    pdf_bytes = weasyprint.HTML(string=html, base_url=str(BASE_DIR)).write_pdf()
    safe_num = re.sub(r"[^\w\-]", "_", data.invoice_number or "faktura")
    filename = f"{safe_num}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _render_invoice(data: InvoiceData) -> str:
    tpl = _jinja.get_template(f"invoice_{data.template}.html")
    return tpl.render(
        d=data, items=data.items,
        vat_breakdown=data.vat_breakdown(),
        grand_base=data.grand_base(),
        grand_vat=data.grand_vat(),
        grand_total=data.grand_total(),
        qr_b64=generate_qr_b64(data),
    )
