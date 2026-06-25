"""
Czech SPD payment QR code generator.

SPD (Short Payment Descriptor) is the Czech/Slovak standard for payment QR codes.
Format: SPD*1.0*ACC:{IBAN}*AM:{amount}*CC:{currency}*X-VS:{vs}*MSG:{msg}
"""
from __future__ import annotations

import base64
import io
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import InvoiceData


def czech_account_to_iban(account: str) -> str | None:
    """Convert Czech bank account '1234567890/0800' or '19-1234567890/0800' to IBAN."""
    m = re.match(r'^(?:(\d+)-)?(\d+)/(\d{4})$', account.strip().replace(" ", ""))
    if not m:
        return None
    prefix = (m.group(1) or "0").zfill(6)
    number = m.group(2).zfill(10)
    bank   = m.group(3)
    bban   = bank + prefix + number          # 20-digit BBAN

    # MOD-97 check digit: rearrange as BBAN + "123500" (CZ=12,35; 00 placeholder)
    check = 98 - (int(bban + "123500") % 97)
    return f"CZ{str(check).zfill(2)}{bban}"


def build_spd(data: InvoiceData) -> str | None:
    """Build the SPD payment string from invoice data. Returns None if not enough info."""
    iban = (data.iban or "").strip()
    if not iban and data.bank_account:
        iban = czech_account_to_iban(data.bank_account) or ""
    if not iban:
        return None

    amount = data.grand_total()
    if amount <= 0:
        return None

    parts = [
        "SPD*1.0",
        f"ACC:{iban.replace(' ', '')}",
        f"AM:{amount:.2f}",
        f"CC:{(data.currency or 'CZK').upper()}",
    ]
    if data.variable_symbol:
        parts.append(f"X-VS:{data.variable_symbol}")
    if data.invoice_number:
        parts.append(f"MSG:{data.invoice_number[:35]}")

    return "*".join(parts)


def generate_qr_b64(data: InvoiceData, scale: int = 5) -> str | None:
    """
    Generate a Czech SPD payment QR code as a base64-encoded PNG data URL.
    Returns None if payment data is insufficient or segno/pillow is unavailable.
    """
    spd = build_spd(data)
    if spd is None:
        return None
    try:
        import segno  # noqa: PLC0415
        qr = segno.make_qr(spd, error="M")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=scale, border=2, dark="#1a1a1a")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None
