"""Regression tests for monetary field equivalence in evaluation (no API calls)."""
from backend.invoice_processing.evaluation import _score_fields, _field_values
from backend.invoice_processing.schemas import InvoiceCandidate


def make_candidate(*, amount="180.00", unit_price="45.00", tax="0.00", qty="4"):
    return InvoiceCandidate.model_validate({
        "supplier_name": "Ember Cocoa Supply Inc.",
        "invoice_number": "ECS-2026-032",
        "invoice_date": None,
        "due_date": "2026-12-12",
        "currency": "USD",
        "line_items": [{
            "description": "Dutch cocoa - 25 lb bag",
            "quantity": qty,
            "unit_price": unit_price,
            "line_total": amount,
        }],
        "subtotal": amount,
        "tax": tax,
        "shipping": "0.00",
        "discount": "0.00",
        "total": amount,
    })


def test_decimal_scales_do_not_create_false_field_errors():
    expected = make_candidate()
    actual = make_candidate(amount="180", unit_price="45.0", tax="-0", qty="4.00")
    scored = _score_fields(expected, actual)
    assert scored["correct"] == scored["compared"]
    assert scored["fp"] == scored["fn"] == 0
    assert _field_values(expected) == _field_values(actual)


def test_real_amount_error_is_still_reported():
    expected = make_candidate()
    actual = make_candidate(amount="181", unit_price="46")
    scored = _score_fields(expected, actual)
    assert scored["correct"] < scored["compared"]
    assert scored["fp"] > 0 and scored["fn"] > 0
    assert _field_values(actual)["total"] == "181"


def test_invoice_identifier_case_remains_significant():
    expected = make_candidate()
    actual = expected.model_copy(update={"invoice_number": "ECS-2026-032".lower()})
    scores = _score_fields(expected, actual)
    assert scores["correct"] == scores["compared"] - 1


def test_missing_date_is_not_filled_or_guessed():
    candidate = make_candidate()
    assert _field_values(candidate)["invoice_date"] is None
