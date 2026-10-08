"""
tests/test_bank.py

Czech bank account / IBAN validation (services/bank.py) and its use in
InvoiceData.validation_warnings() + QR IBAN conversion.
"""
from __future__ import annotations

import pytest

from models import InvoiceData
from services.bank import (
    account_checksum_ok,
    bank_account_warnings,
    iban_ok,
    parse_czech_account,
)
from services.qr import czech_account_to_iban, generate_qr_b64

VALID = "670100-2212415741/6210"


def _inv(**over):
    base = {
        "invoice_number": "FA-2026-001", "issue_date": "2026-01-01", "due_date": "2026-01-15",
        "bank_account": VALID,
        "supplier": {"name": "A", "ico": "11979879"},
        "customer": {"name": "B"},
        "items": [{"description": "X", "quantity": 1, "unit_price": 1000}],
    }
    base.update(over)
    return InvoiceData(**base)


class TestParse:

    def test_full_account(self):
        assert parse_czech_account(VALID) == ("670100", "2212415741", "6210")

    def test_without_prefix_is_zero_padded(self):
        assert parse_czech_account("19/0800") == ("000000", "0000000019", "0800")

    def test_spaces_ignored(self):
        assert parse_czech_account(" 670100-2212415741 / 6210 ") == ("670100", "2212415741", "6210")

    @pytest.mark.parametrize("bad", ["670100-2212415741", "abc/0800", "123/80", "1234567-12/0800", ""])
    def test_unparseable(self, bad):
        assert parse_czech_account(bad) is None


class TestChecksums:

    def test_real_account_passes_mod11(self):
        assert account_checksum_ok("670100", "2212415741")

    def test_typo_fails_mod11(self):
        assert not account_checksum_ok("670100", "2212415742")

    def test_iban_valid(self):
        assert iban_ok("CZ03 6210 6701 0022 1241 5741")

    def test_iban_bad_check_digits(self):
        assert not iban_ok("CZ04 6210 6701 0022 1241 5741")

    def test_qr_iban_conversion_unchanged(self):
        assert czech_account_to_iban(VALID) == "CZ0362106701002212415741"
        assert iban_ok(czech_account_to_iban(VALID))


class TestWarnings:

    def test_valid_account_no_warnings(self):
        assert bank_account_warnings(VALID) == []

    def test_empty_no_warnings(self):
        assert bank_account_warnings("", "") == []

    def test_missing_bank_code(self):
        w = bank_account_warnings("670100-2212415741")
        assert len(w) == 1 and "kód banky" in w[0]

    def test_bad_format(self):
        assert "neplatný formát" in bank_account_warnings("12-ab/0800")[0]

    def test_bad_checksum(self):
        assert "modulo 11" in bank_account_warnings("670100-2212415742/6210")[0]

    def test_bad_iban(self):
        assert "IBAN" in bank_account_warnings("", "CZ0400000000000000000000")[0]


class TestInvoiceWarnings:

    def test_missing_bank_code_warns_about_qr(self):
        inv = _inv(bank_account="670100-2212415741")
        w = inv.validation_warnings()
        assert any("kód banky" in x for x in w)
        assert any("QR platba" in x for x in w)
        assert generate_qr_b64(inv) is None
        assert inv.validation_errors() == []          # still a valid invoice

    def test_valid_account_no_warnings_and_qr(self):
        inv = _inv()
        assert inv.validation_warnings() == []
        assert generate_qr_b64(inv) is not None

    def test_iban_rescues_qr(self):
        inv = _inv(bank_account="670100-2212415741", iban="CZ0362106701002212415741")
        w = inv.validation_warnings()
        assert any("kód banky" in x for x in w)
        assert not any("QR platba" in x for x in w)
