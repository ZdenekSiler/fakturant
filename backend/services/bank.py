"""
Czech bank account and IBAN validation.

Account format: [prefix-]number/bank — prefix up to 6 digits, number up to 10, bank code 4.
Prefix and number each carry a mod-11 checksum (ČNB vyhláška č. 169/2011 Sb.).
"""
from __future__ import annotations

import re

_ACCOUNT_RE  = re.compile(r'^(?:(\d{1,6})-)?(\d{2,10})/(\d{4})$')
_NO_BANK_RE  = re.compile(r'^(?:\d{1,6}-)?\d{2,10}$')
_PREFIX_W    = (10, 5, 8, 4, 2, 1)
_NUMBER_W    = (6, 3, 7, 9, 10, 5, 8, 4, 2, 1)


def _clean(s: str) -> str:
    return (s or "").strip().replace(" ", "")


def parse_czech_account(account: str) -> tuple[str, str, str] | None:
    """Return (prefix, number, bank) zero-padded to 6/10/4 digits, or None if not parseable."""
    m = _ACCOUNT_RE.match(_clean(account))
    if not m:
        return None
    return (m.group(1) or "0").zfill(6), m.group(2).zfill(10), m.group(3)


def _mod11_ok(digits: str, weights: tuple[int, ...]) -> bool:
    return sum(int(d) * w for d, w in zip(digits, weights)) % 11 == 0


def account_checksum_ok(prefix: str, number: str) -> bool:
    return _mod11_ok(prefix.zfill(6), _PREFIX_W) and _mod11_ok(number.zfill(10), _NUMBER_W)


def iban_ok(iban: str) -> bool:
    s = _clean(iban).upper()
    if not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{10,30}', s):
        return False
    rearranged = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def bank_account_warnings(bank_account: str, iban: str = "") -> list[str]:
    """Non-blocking, user-facing (Czech) warnings about payment details."""
    warnings: list[str] = []
    acc = _clean(bank_account)
    if acc:
        parsed = parse_czech_account(acc)
        if parsed is None:
            if _NO_BANK_RE.match(acc):
                warnings.append("Číslo účtu nemá kód banky (např. /6210)")
            else:
                warnings.append("Číslo účtu má neplatný formát (očekáváno [předčíslí-]číslo/kód banky)")
        elif not account_checksum_ok(parsed[0], parsed[1]):
            warnings.append("Číslo účtu neprošlo kontrolou (modulo 11) — zkontrolujte překlep")
    if _clean(iban) and not iban_ok(iban):
        warnings.append("IBAN je neplatný (kontrolní číslice nesouhlasí)")
    return warnings
