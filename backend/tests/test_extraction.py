import json
from decimal import Decimal
import pytest
from backend.invoice_processing.extraction import extract_rules, extract_groq, GroqExtractionError
from backend.invoice_processing.schemas import InvoiceCandidate

TEXT = """Supplier: Blue Meadow Dairy
Invoice Number: BMD-001
Invoice Date: 2026-11-01
Due Date: 2026-12-01
Currency: USD
Item | Quantity | Unit Price | Amount
Whole Milk | 10 | 4.25 | 42.50
Subtotal: 42.50
Tax: 2.50
Shipping: 0.00
Discount: 0.00
Total: 45.00
"""


def test_rules_baseline():
    inv = extract_rules(TEXT)
    assert inv.invoice_number == "BMD-001" and inv.total == Decimal("45.00")
    assert inv.line_items[0].quantity == 10


def test_invalid_groq_config_not_silently_faked(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    with pytest.raises(GroqExtractionError):
        extract_groq(TEXT)


def test_groq_adapter_validates_structured_response(monkeypatch):
    from backend.invoice_processing import extraction
    monkeypatch.setenv("GROQ_API_KEY", "private-test-token")
    monkeypatch.setenv("GROQ_MODEL", "mock-model")
    captured = {}
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": json.dumps(extract_rules(TEXT).model_dump(mode="json"))}}]}
    class FakeHTTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, json, headers):
            captured["response_format"] = json["response_format"]
            return Response()
    monkeypatch.setattr(extraction.httpx, "Client", FakeHTTP)
    inv = extract_groq(TEXT)
    assert inv.total == Decimal("45.00")
    assert captured["response_format"]["type"] == "json_schema"
