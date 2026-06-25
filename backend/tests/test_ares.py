"""
tests/test_ares.py — unit tests for ARES service and QR helpers.
All HTTP calls are mocked; no real network traffic.
"""
from __future__ import annotations

import pytest
import httpx
import respx

from services.ares import AresResult, _build_address, get_by_ico, search_by_name
from services.qr import build_spd, czech_account_to_iban, generate_qr_b64
from models import InvoiceData, LineItem, Party


# ══════════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════════

def _inv(**kwargs) -> InvoiceData:
    defaults = dict(
        invoice_number="FA-2026-001",
        bank_account="1234567890/0800",
        variable_symbol="20260001",
        currency="CZK",
        items=[LineItem(quantity=10, unit_price=2000, vat_rate=0)],
    )
    defaults.update(kwargs)
    return InvoiceData(**defaults)


# ══════════════════════════════════════════════════════════════════════════════
# 1. _build_address
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildAddress:
    def test_full_address(self):
        sidlo = {"nazevUlice": "Vaculíkova", "cisloDomovni": 2123,
                 "nazevObce": "Úvaly", "psc": 25082}
        assert _build_address(sidlo) == "Vaculíkova, 2123, Úvaly, 25082"

    def test_missing_street(self):
        sidlo = {"cisloDomovni": 5, "nazevObce": "Praha", "psc": 11000}
        result = _build_address(sidlo)
        assert "Praha" in result
        assert "5" in result

    def test_empty_sidlo(self):
        assert _build_address({}) == ""

    def test_skips_empty_parts(self):
        sidlo = {"nazevUlice": "", "nazevObce": "Brno", "psc": 60200}
        result = _build_address(sidlo)
        assert result.startswith("Brno") or "Brno" in result
        assert not result.startswith(",")


# ══════════════════════════════════════════════════════════════════════════════
# 2. get_by_ico  (mocked HTTP)
# ══════════════════════════════════════════════════════════════════════════════

_BASE = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty"

@pytest.mark.asyncio
class TestGetByIco:

    @respx.mock
    async def test_returns_result_on_200(self):
        respx.get(f"{_BASE}/11979879").mock(return_value=httpx.Response(200, json={
            "ico": "11979879",
            "obchodniJmeno": "Zdeněk Šiler",
            "dic": "",
            "sidlo": {"nazevUlice": "Vaculíkova", "cisloDomovni": 2123,
                      "nazevObce": "Úvaly", "psc": 25082},
        }))
        result = await get_by_ico("11979879")
        assert result is not None
        assert result.name == "Zdeněk Šiler"
        assert result.ico == "11979879"
        assert "Vaculíkova" in result.address

    @respx.mock
    async def test_returns_none_on_404(self):
        respx.get(f"{_BASE}/00000000").mock(return_value=httpx.Response(404))
        assert await get_by_ico("00000000") is None

    @respx.mock
    async def test_returns_none_on_network_error(self):
        respx.get(f"{_BASE}/99999999").mock(side_effect=httpx.ConnectError("timeout"))
        assert await get_by_ico("99999999") is None

    @respx.mock
    async def test_dic_populated(self):
        respx.get(f"{_BASE}/08257817").mock(return_value=httpx.Response(200, json={
            "ico": "08257817",
            "obchodniJmeno": "Nummera s.r.o.",
            "dic": "CZ08257817",
            "sidlo": {"nazevUlice": "Slezská", "cisloDomovni": 2127,
                      "nazevObce": "Praha", "psc": 12000},
        }))
        result = await get_by_ico("08257817")
        assert result is not None
        assert result.dic == "CZ08257817"


# ══════════════════════════════════════════════════════════════════════════════
# 3. search_by_name  (mocked HTTP)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestSearchByName:

    @respx.mock
    async def test_returns_list_of_results(self):
        respx.post(f"{_BASE}/vyhledat").mock(return_value=httpx.Response(200, json={
            "ekonomickeSubjekty": [
                {"ico": "08257817", "obchodniJmeno": "Nummera s.r.o.",
                 "dic": "CZ08257817", "sidlo": {"nazevObce": "Praha"}},
            ]
        }))
        results = await search_by_name("Nummera")
        assert len(results) == 1
        assert results[0].name == "Nummera s.r.o."
        assert results[0].ico == "08257817"

    @respx.mock
    async def test_empty_response_returns_empty_list(self):
        respx.post(f"{_BASE}/vyhledat").mock(return_value=httpx.Response(200, json={
            "ekonomickeSubjekty": []
        }))
        assert await search_by_name("XYZ neexistující") == []

    @respx.mock
    async def test_network_error_returns_empty_list(self):
        respx.post(f"{_BASE}/vyhledat").mock(side_effect=httpx.ConnectError("err"))
        assert await search_by_name("anything") == []


# ══════════════════════════════════════════════════════════════════════════════
# 4. czech_account_to_iban
# ══════════════════════════════════════════════════════════════════════════════

class TestCzechAccountToIban:

    def test_standard_account(self):
        iban = czech_account_to_iban("1234567890/0800")
        assert iban is not None
        assert iban.startswith("CZ")
        assert len(iban) == 24

    def test_account_with_prefix(self):
        iban = czech_account_to_iban("19-1234567890/0800")
        assert iban is not None
        assert iban.startswith("CZ")
        assert "000019" in iban  # prefix padded to 6 digits

    def test_short_account_number_padded(self):
        iban = czech_account_to_iban("123/0100")
        assert iban is not None
        assert "0000000123" in iban  # account padded to 10 digits

    def test_invalid_format_returns_none(self):
        assert czech_account_to_iban("not-a-bank-account") is None
        assert czech_account_to_iban("1234567") is None        # no slash+bank
        assert czech_account_to_iban("") is None

    def test_known_iban(self):
        # Verified externally: 1234567890/0800 → CZ0708000000001234567890
        iban = czech_account_to_iban("1234567890/0800")
        assert iban == "CZ0708000000001234567890"


# ══════════════════════════════════════════════════════════════════════════════
# 5. build_spd
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildSpd:

    def test_basic_spd_from_bank_account(self):
        inv = _inv()
        spd = build_spd(inv)
        assert spd is not None
        assert spd.startswith("SPD*1.0*")
        assert "ACC:CZ" in spd
        assert "AM:20000.00" in spd  # 10 * 2000
        assert "CC:CZK" in spd
        assert "X-VS:20260001" in spd
        assert "MSG:FA-2026-001" in spd

    def test_prefers_iban_over_bank_account(self):
        inv = _inv(iban="CZ6508000000001234567890", bank_account="1234567890/0800")
        spd = build_spd(inv)
        assert "ACC:CZ6508000000001234567890" in spd

    def test_returns_none_without_payment_info(self):
        inv = _inv(bank_account="", iban="")
        assert build_spd(inv) is None

    def test_returns_none_for_zero_amount(self):
        inv = _inv(items=[])
        assert build_spd(inv) is None

    def test_msg_truncated_to_35_chars(self):
        long_number = "FA-" + "9" * 40
        inv = _inv(invoice_number=long_number)
        spd = build_spd(inv)
        assert spd is not None
        msg_part = [p for p in spd.split("*") if p.startswith("MSG:")][0]
        assert len(msg_part) - len("MSG:") <= 35

    def test_spd_without_variable_symbol(self):
        inv = _inv(variable_symbol="")
        spd = build_spd(inv)
        assert spd is not None
        assert "X-VS" not in spd


# ══════════════════════════════════════════════════════════════════════════════
# 6. generate_qr_b64
# ══════════════════════════════════════════════════════════════════════════════

class TestGenerateQrB64:

    def test_returns_data_url(self):
        inv = _inv()
        result = generate_qr_b64(inv)
        assert result is not None
        assert result.startswith("data:image/png;base64,")

    def test_returns_none_without_payment_info(self):
        inv = _inv(bank_account="", iban="")
        assert generate_qr_b64(inv) is None

    def test_returns_none_for_empty_invoice(self):
        inv = _inv(items=[])
        assert generate_qr_b64(inv) is None

    def test_output_is_valid_base64_png(self):
        import base64
        inv = _inv()
        result = generate_qr_b64(inv)
        assert result is not None
        b64_part = result.split(",", 1)[1]
        raw = base64.b64decode(b64_part)
        assert raw[:4] == b"\x89PNG"  # PNG magic bytes
