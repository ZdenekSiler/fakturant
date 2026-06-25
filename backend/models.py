"""
Pydantic models for Fakturant.
InvoiceData is the single source of truth for invoice structure and validation.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class Party(BaseModel):
    name: str = ""
    ico: str = ""
    dic: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""
    vat_payer: bool = False


class LineItem(BaseModel):
    description: str = ""
    project: str = ""
    item_date: str = ""
    quantity: float = 1.0
    unit: str = "ks"
    unit_price: float = 0.0
    vat_rate: float = 21.0

    def base(self) -> float:
        return round(self.quantity * self.unit_price, 2)

    def vat(self) -> float:
        return round(self.base() * self.vat_rate / 100, 2)

    def total(self) -> float:
        return round(self.base() + self.vat(), 2)


class InvoiceData(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    template: Literal["modern", "classic", "minimal"] = "modern"
    invoice_number: str = ""
    issue_date: str = ""
    duzp: str = ""
    due_date: str = ""
    currency: str = "CZK"
    bank_account: str = ""
    variable_symbol: str = ""
    iban: str = ""
    swift: str = ""
    notes: str = ""
    tags: list[str] = []
    logo_b64: str | None = None
    signature_b64: str | None = None
    supplier: Party = Party()
    customer: Party = Party()
    items: list[LineItem] = []

    @field_validator("template", mode="before")
    @classmethod
    def coerce_template(cls, v: str) -> str:
        return v if v in ("modern", "classic", "minimal") else "modern"

    def grand_base(self) -> float:
        return round(sum(i.base() for i in self.items), 2)

    def grand_vat(self) -> float:
        if not self.supplier.vat_payer:
            return 0.0
        return round(sum(i.vat() for i in self.items), 2)

    def grand_total(self) -> float:
        if not self.supplier.vat_payer:
            return self.grand_base()
        return round(sum(i.total() for i in self.items), 2)

    def vat_breakdown(self) -> dict[float, dict[str, float]]:
        if not self.supplier.vat_payer:
            return {}
        breakdown: dict[float, dict[str, float]] = {}
        for item in self.items:
            rate = item.vat_rate
            if rate not in breakdown:
                breakdown[rate] = {"base": 0.0, "vat": 0.0, "total": 0.0}
            breakdown[rate]["base"] = round(breakdown[rate]["base"] + item.base(), 2)
            breakdown[rate]["vat"] = round(breakdown[rate]["vat"] + item.vat(), 2)
            breakdown[rate]["total"] = round(breakdown[rate]["total"] + item.total(), 2)
        return breakdown

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.invoice_number:
            errors.append("Číslo faktury je povinné")
        if not self.issue_date:
            errors.append("Datum vystavení je povinné")
        if not self.due_date:
            errors.append("Datum splatnosti je povinné")
        if not self.supplier.name:
            errors.append("Název dodavatele je povinný")
        if not self.supplier.ico:
            errors.append("IČO dodavatele je povinné (§ 29 odst. 1 zákona č. 235/2004 Sb.)")
        if not self.customer.name:
            errors.append("Název odběratele je povinný")
        if not self.bank_account and not self.iban:
            errors.append("Bankovní účet nebo IBAN je povinný")
        if not self.items:
            errors.append("Faktura musí obsahovat alespoň jednu položku")
        if self.supplier.vat_payer and not self.supplier.dic:
            errors.append("Plátce DPH musí mít DIČ dodavatele")
        if self.supplier.vat_payer and not self.duzp:
            errors.append("DUZP je povinné pro plátce DPH (§ 26 odst. 3 zákona č. 235/2004 Sb.)")
        return errors
