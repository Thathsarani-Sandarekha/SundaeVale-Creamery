"""Offline source grounding guards. No OCR models or Groq network calls."""
import json
from decimal import Decimal

from backend.invoice_processing.ocr_grounding import ground_explicit_fields
from backend.invoice_processing.schemas import InvoiceCandidate
from backend.invoice_processing import extraction


OCR_TEXT = """<!-- image -->
SundaeValeCreamery|AccountsPayable|SyntheticDemonstration
## Tradingparty: Lakeshore Logistics LLC
Ref/LL-2026-0219
Billto:SundaeValeCreamery|AccountsPayable
| Product/service | QTY | Rate | Net |  |  |
| Pallet shipping |  |  |  | 45.00 | 90.00 |
|   |   |   |   | Handling:0.00 |  |
|   |   |   |   | Deduction:0.00 |  |
"""


def candidate(**overrides):
    data = {"supplier_name": "SundaeValeCreamery", "invoice_number": "Ref/LL-2026-0219",
            "line_items": [{"description": "Pallet shipping", "quantity": None, "unit_price": None,
                            "line_total": "90.00"}], "subtotal": "90.00", "tax": "0.00",
            "shipping": "45.00", "discount": None, "total": "90.00"}
    return InvoiceCandidate.model_validate({**data, **overrides})


def test_repairs_only_explicit_supplier_and_money_labels():
    repaired = ground_explicit_fields(OCR_TEXT, candidate())
    assert repaired.supplier_name == "Lakeshore Logistics LLC"
    assert repaired.invoice_number == "LL-2026-0219"
    assert repaired.shipping == Decimal("0") and repaired.discount == Decimal("0")
    assert repaired.line_items[0].quantity is None  # never infer 90 / 45
    assert repaired.line_items[0].unit_price is None


def test_does_not_invent_an_unlabeled_supplier_or_amount():
    raw = "<!-- image -->\nBillto:SundaeValeCreamery\n| Shipping item | 1 | 30 | 30 |"
    original = candidate(supplier_name=None, invoice_number="OVl-2026-055", shipping=None, discount=None)
    repaired = ground_explicit_fields(raw, original)
    assert repaired.supplier_name is None
    assert repaired.invoice_number == "OVl-2026-055"
    assert repaired.shipping is None and repaired.discount is None


def test_does_not_guess_ambiguous_invoice_characters():
    source = "No.OVl-2026-055\n## Tradingparty: Orchard Valley Ingredients"
    repaired = ground_explicit_fields(source, candidate(invoice_number="No.OVl-2026-055"))
    assert repaired.invoice_number == "OVl-2026-055"  # not OVI


def test_ignores_conflicting_shipping_evidence():
    raw = "Handling: 0.00\nHandling: 12.00\nDeduction: 0.00"
    repaired = ground_explicit_fields(raw, candidate(supplier_name="Lakeshore Logistics LLC"))
    assert repaired.shipping == 45
    assert repaired.discount == 0


def test_groq_adapter_uses_source_grounding(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-token")
    monkeypatch.setenv("GROQ_MODEL", "test-model")
    class FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": json.dumps(candidate().model_dump(mode="json"))}}]}
    class FakeHTTP:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, url, json, headers):
            assert "trading party" in json["messages"][0]["content"].lower()
            return FakeResponse()
    monkeypatch.setattr(extraction.httpx, "Client", FakeHTTP)
    actual = extraction.extract_groq(OCR_TEXT)
    assert actual.supplier_name == "Lakeshore Logistics LLC"
    assert actual.shipping == 0


def test_ambiguous_vendor_labels_are_not_silently_selected():
    raw = "Supplier: Vendor A LLC\nTradingparty: Vendor B LLC\nBillto: SundaeVale"
    original = candidate(supplier_name="Vendor C LLC")
    repaired = ground_explicit_fields(raw, original)
    assert repaired.supplier_name == "Vendor C LLC"


def test_explicit_source_restores_spaces_but_never_removes_them():
    source = "Tradingparty: Lakeshore Logistics LLC"
    assert ground_explicit_fields(source, candidate(supplier_name="LakeshoreLogistics LLC")).supplier_name == "Lakeshore Logistics LLC"
    joined_source = "Tradingparty: LakeshoreLogistics LLC"
    assert ground_explicit_fields(joined_source, candidate(supplier_name="Lakeshore Logistics LLC")).supplier_name == "Lakeshore Logistics LLC"
