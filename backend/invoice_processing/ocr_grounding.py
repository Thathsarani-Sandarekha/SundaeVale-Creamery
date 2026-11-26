"""Conservative field grounding against explicit OCR/source labels.

This module does not infer missing quantities, unit prices or dates, does not
correct ambiguous OCR characters, and never makes provider calls. It only
uses values that are explicitly labelled in the source text.
"""
from __future__ import annotations

from decimal import Decimal
import re

from .schemas import InvoiceCandidate


SUPPLIER_LABEL = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:trading\s*party|supplier|vendor)\s*[:\-]\s*([^\r\n|]+)\s*$"
)
BUYER_LABEL = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:bill\s*to|billto|buyer|customer)\s*[:\-]\s*([^\r\n]+)"
)
INVOICE_PREFIX = re.compile(
    r"(?i)^(?:document\s*ref\s*[:/#.\-]?\s*|ref\s*[:/#.\-]?\s*|no\.\s*|invoice\s*(?:number|no\.?|#)\s*[:/#.\-]?\s*)(?=[a-z0-9])"
)
NUMERIC = r"([+-]?\d[\d,]*(?:\.\d+)?)"
# Look for an explicit labelled amount, not an arbitrary item description.
SHIPPING_LABEL = re.compile(r"(?i)\b(?:handling|shipping|freight|transport|delivery)\s*:\s*" + NUMERIC + r"\b")
DISCOUNT_LABEL = re.compile(r"(?i)\b(?:deduction|discount|price\s*reduction)\s*:\s*" + NUMERIC + r"\b")


def _identity(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def _clean_party(value: str) -> str:
    # Strip Markdown decoration but never invent missing whitespace.
    return value.strip().strip("*_ `")


def ground_explicit_fields(text: str, candidate: InvoiceCandidate) -> InvoiceCandidate:
    """Repair only source-evidenced, unambiguous fields in OCR/LLM candidates.

    A supplier correction requires an explicitly labeled supplier/trading party.
    Do not fill a missing supplier when there is no such label, and never infer
    invoice quantities by dividing totals. All corrections remain human-reviewable.
    """
    changes: dict = {}
    # Multiple incompatible source labels are ambiguous. Do not override an
    # extraction based on whichever label happens to appear first.
    names = [_clean_party(m.group(1)) for m in SUPPLIER_LABEL.finditer(text[:4000])]
    names = [name for name in names if name]
    identities = {_identity(name) for name in names}
    if len(identities) == 1 and names:
        source_supplier = max(names, key=lambda name: name.count(" "))
        current = candidate.supplier_name
        # Prefer the explicit source label, especially when it preserves more
        # of the word spacing than OCR/LLM output. Never remove useful spacing
        # based only on a concatenated OCR string.
        if (_identity(source_supplier) != _identity(current)
                or source_supplier.count(" ") > (current or "").count(" ")):
            changes["supplier_name"] = source_supplier

    number = candidate.invoice_number or ""
    trimmed = INVOICE_PREFIX.sub("", number, count=1).strip()
    # Strip only obvious OCR-joined field labels, and only when the exact string
    # appears in source text. Never rewrite I/l/1 or O/0 characters.
    if trimmed and trimmed != number and number.casefold() in text.casefold():
        changes["invoice_number"] = trimmed

    for field, pattern in (("shipping", SHIPPING_LABEL), ("discount", DISCOUNT_LABEL)):
        matches = {Decimal(m.group(1).replace(",", "")) for m in pattern.finditer(text)}
        if len(matches) == 1:
            source_amount = matches.pop()
            if getattr(candidate, field) != source_amount:
                changes[field] = source_amount
    if not changes:
        return candidate
    return InvoiceCandidate.model_validate({**candidate.model_dump(mode="python"), **changes})
