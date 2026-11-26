"""Regression tests for optional 200-DPI source evidence (no OCR/Groq calls)."""
from __future__ import annotations

from backend.invoice_processing.ocr_geometry_evidence import (
    append_geometry_evidence, high_confidence_rows, GEOMETRY_MARKER,
)


def _fixture_row(qty="2", rate="45.00", total="90.00"):
    def t(x, y, text):
        return {"page": 1, "x": x, "y": y, "text": text}
    return [
        t(121, 732, "Product/service"),
        t(417, 732, "QTY"),
        t(552, 733, "Rate"),
        t(703, 733, "Net"),
        t(117, 837, "Pallet shipping"),
        *([t(946, 837, qty)] if qty is not None else []),
        *([t(1168, 836, rate)] if rate is not None else []),
        *([t(1412, 837, total)] if total is not None else []),
        t(1024, 1155, "Net supplies:90.00"),
        t(1024, 1221, "Tax:0.00"),
    ]


def test_spatial_row_recovers_three_observed_values():
    rows = high_confidence_rows(_fixture_row())
    assert rows == [{"page": 1, "description": "Pallet shipping",
                    "quantity": "2", "unit_price": "45.00", "line_total": "90.00"}]


def test_missing_quantity_must_not_be_backcalculated():
    assert high_confidence_rows(_fixture_row(qty=None)) == []


def test_wrong_arithmetic_is_rejected_not_corrected():
    assert high_confidence_rows(_fixture_row(total="95.00")) == []


def test_unlabeled_random_numbers_are_not_treated_as_rows():
    tokens = _fixture_row()[4:]
    assert high_confidence_rows(tokens) == []


def test_raw_source_text_is_preserved_and_metadata_is_explicit():
    source = "Tradingparty:Lakeshore Logistics LLC\n| Pallet shipping | | | 45.00 | 90.00 |"
    text = append_geometry_evidence(source, high_confidence_rows(_fixture_row()))
    assert text.startswith(source)
    assert GEOMETRY_MARKER in text
    assert '"quantity":"2"' in text
    assert '"unit_price":"45.00"' in text
    assert append_geometry_evidence(source, []) == source


def test_multiple_ocr_tokens_cannot_be_guessed_as_a_row():
    tokens = _fixture_row() + [{"page": 1, "x": 1600, "y": 837, "text": "17.00"}]
    assert high_confidence_rows(tokens) == []
