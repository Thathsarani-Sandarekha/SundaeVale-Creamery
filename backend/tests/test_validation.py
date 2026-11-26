from backend.invoice_processing.schemas import InvoiceCandidate
from backend.invoice_processing.validation import issues_for, normalize_identity


def valid_invoice():
    return InvoiceCandidate.model_validate({
        "supplier_name": "Blue Meadow Dairy", "invoice_number": "BMD-001", "invoice_date": "2026-11-01",
        "due_date": "2026-12-01", "currency": "USD",
        "line_items": [{"description": "Milk", "quantity": "10", "unit_price": "4.25", "line_total": "42.50"}],
        "subtotal": "42.50", "tax": "2.50", "shipping": "0", "discount": "0", "total": "45.00",
    })


def test_correct_invoice_no_warnings():
    assert issues_for(valid_invoice()) == []


def test_incorrect_total_flagged():
    inv = valid_invoice().model_copy(update={"total": valid_invoice().total + 3})
    assert "TOTAL_MISMATCH" in [v.code for v in issues_for(inv)]


def test_line_total_and_subtotal_mismatches():
    inv = valid_invoice()
    inv.line_items[0].line_total += 10
    codes = [v.code for v in issues_for(inv)]
    assert "LINE_TOTAL_MISMATCH" in codes and "SUBTOTAL_MISMATCH" in codes


def test_missing_fields_stay_missing():
    inv = InvoiceCandidate()
    assert len([v for v in issues_for(inv) if v.code == "MISSING_REQUIRED"]) == 5


def test_date_order_and_duplicate():
    inv = valid_invoice()
    inv.due_date = inv.invoice_date.replace(day=1, month=10)
    codes = [v.code for v in issues_for(inv, duplicate=True)]
    assert "DATE_ORDER" in codes and "POSSIBLE_DUPLICATE" in codes


def test_normalizes_vendor_identity():
    assert normalize_identity("Blue Meadow, Dairy") == normalize_identity("BLUE-MEADOW DAIRY")
