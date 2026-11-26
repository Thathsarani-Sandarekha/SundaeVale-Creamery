"""Deterministic invoice checks. LLMs never decide financial correctness."""
from __future__ import annotations
import re
from decimal import Decimal
from .schemas import InvoiceCandidate, Issue

TOLERANCE = Decimal("0.02")


def normalize_identity(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def issues_for(invoice: InvoiceCandidate, *, duplicate: bool = False) -> list[Issue]:
    issues: list[Issue] = []

    def add(code: str, field: str, message: str, severity: str = "review"):
        issues.append(Issue(code=code, field=field, message=message, severity=severity))

    for field in ("supplier_name", "invoice_number", "invoice_date", "currency", "total"):
        if getattr(invoice, field) is None:
            add("MISSING_REQUIRED", field, f"Required field '{field}' is missing.")
    if invoice.currency and not re.fullmatch(r"[A-Z]{3}", invoice.currency):
        add("INVALID_CURRENCY", "currency", "Expected a three-letter currency code.")
    if invoice.invoice_date and invoice.due_date and invoice.due_date < invoice.invoice_date:
        add("DATE_ORDER", "due_date", "Due date precedes invoice date.")
    if not invoice.line_items:
        add("MISSING_LINE_ITEMS", "line_items", "No invoice line items were extracted.")

    reported_lines = []
    for idx, line in enumerate(invoice.line_items):
        if line.quantity is not None and line.quantity < 0:
            add("NEGATIVE_QUANTITY", f"line_items.{idx}.quantity", "Quantity cannot be negative.")
        if line.unit_price is not None and line.unit_price < 0:
            add("NEGATIVE_PRICE", f"line_items.{idx}.unit_price", "Unit price cannot be negative.")
        if line.quantity is not None and line.unit_price is not None and line.line_total is not None:
            if abs(line.quantity * line.unit_price - line.line_total) > TOLERANCE:
                add("LINE_TOTAL_MISMATCH", f"line_items.{idx}.line_total", "Line amount differs from quantity × unit price.")
        if line.line_total is not None:
            reported_lines.append(line.line_total)
    if invoice.subtotal is not None and reported_lines and len(reported_lines) == len(invoice.line_items):
        if abs(sum(reported_lines, Decimal("0")) - invoice.subtotal) > TOLERANCE:
            add("SUBTOTAL_MISMATCH", "subtotal", "Subtotal differs from the sum of line amounts.")
    if invoice.total is not None and invoice.subtotal is not None:
        expected = invoice.subtotal + (invoice.tax or Decimal("0")) + (invoice.shipping or Decimal("0")) - (invoice.discount or Decimal("0"))
        if abs(expected - invoice.total) > TOLERANCE:
            add("TOTAL_MISMATCH", "total", "Total differs from subtotal + tax + shipping - discount.")
    for field in ("subtotal", "tax", "shipping", "discount", "total"):
        val = getattr(invoice, field)
        if val is not None and val < 0:
            add("NEGATIVE_AMOUNT", field, f"Unexpected negative {field}.")
    if duplicate:
        add("POSSIBLE_DUPLICATE", "invoice_number", "An invoice with this supplier and invoice number already exists.")
    return issues
