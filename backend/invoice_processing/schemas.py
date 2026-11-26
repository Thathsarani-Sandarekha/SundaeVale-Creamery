"""Typed candidate invoice data. Missing fields remain missing, never invented."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pydantic import BaseModel, ConfigDict, Field, field_validator


def money(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a monetary amount")
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid decimal amount") from exc


class InvoiceLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None

    @field_validator("quantity", "unit_price", "line_total", mode="before")
    @classmethod
    def parse_decimal(cls, v):
        return money(v)


class InvoiceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    line_items: list[InvoiceLine] = Field(default_factory=list)
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    shipping: Decimal | None = None
    discount: Decimal | None = None
    total: Decimal | None = None

    @field_validator("subtotal", "tax", "shipping", "discount", "total", mode="before")
    @classmethod
    def parse_money(cls, v):
        return money(v)

    @field_validator("currency")
    @classmethod
    def upper_currency(cls, v):
        return v.strip().upper() if v else None

    @field_validator("supplier_name", "invoice_number")
    @classmethod
    def trim_text(cls, v):
        return v.strip() if v and v.strip() else None


class Issue(BaseModel):
    code: str
    severity: str
    field: str
    message: str


class DocumentView(BaseModel):
    id: str
    filename: str
    status: str
    sha256: str
    created_at: str
    extraction_method: str | None = None
    extraction: InvoiceCandidate | None = None
    issues: list[Issue] = Field(default_factory=list)
    error_code: str | None = None
