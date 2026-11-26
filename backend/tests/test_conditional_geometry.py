"""test real NumPy-shaped OCR boxes and conditional geometry routing.

No live Docling/Groq/RapidOCR dependency or API calls are needed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
from pypdf import PdfWriter

from backend.invoice_processing import ocr_geometry_evidence as evidence
from backend.invoice_processing import parsing

BROKEN = """<!-- image -->
## Tradingparty: Lakeshore Logistics LLC
| Product/service | QTY | Rate | Net | | |
|---|---|---|---|---|---|
| Pallet shipping | | | | 45.00 | 90.00 |
| | | | | Grandtotal:90.00 | |
"""
COMPLETE = """| Product/service | QTY | Rate | Net |
|---|---|---|---|
| Pallet shipping | 2 | 45.00 | 90.00 |
| | | | Grandtotal:90.00 |
"""
ROWS = [{"page": 1, "description": "Pallet shipping", "quantity": "2", "unit_price": "45.00", "line_total": "90.00"}]


def test_incomplete_markdown_triggers_high_res_evidence():
    assert evidence.needs_geometry_evidence(BROKEN)
    assert not evidence.needs_geometry_evidence(COMPLETE)
    assert not evidence.needs_geometry_evidence("Invoice number 123\nTotal: 90.00")
    assert not evidence.needs_geometry_evidence("| Product | Qty | Rate | Net |\n| Item |  | | |")


@pytest.fixture
def scanned_pdf(tmp_path: Path) -> Path:
    output = tmp_path / "synthetic-image-only.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with output.open("wb") as f:
        writer.write(f)
    return output


def _fake_docling(monkeypatch, markdown: str):
    converter_mod = ModuleType("docling.document_converter")

    class FakeConverter:
        def convert(self, *_args, **_kwargs):
            return SimpleNamespace(document=SimpleNamespace(export_to_markdown=lambda: markdown))

    converter_mod.DocumentConverter = FakeConverter
    fake_package = ModuleType("docling")
    fake_package.__path__ = []
    monkeypatch.setitem(sys.modules, "docling", fake_package)
    monkeypatch.setitem(sys.modules, "docling.document_converter", converter_mod)


def test_auto_runs_only_when_table_is_incomplete(monkeypatch, scanned_pdf):
    _fake_docling(monkeypatch, BROKEN)
    monkeypatch.setenv("INVOICE_OCR_GEOMETRY", "auto")
    hits = []
    monkeypatch.setattr(evidence, "extract_geometry_rows", lambda path: hits.append(path) or ROWS)
    text, method = parsing.extract_pdf(scanned_pdf)
    assert method == "docling-ocr+geometry200-auto"
    assert '"quantity":"2"' in text
    assert hits == [scanned_pdf]


def test_auto_skips_complete_rows_and_off_does_not_run(monkeypatch, scanned_pdf):
    hits = []
    monkeypatch.setattr(evidence, "extract_geometry_rows", lambda *_: hits.append(1) or ROWS)
    monkeypatch.setenv("INVOICE_OCR_GEOMETRY", "auto")
    _fake_docling(monkeypatch, COMPLETE)
    _, method = parsing.extract_pdf(scanned_pdf)
    assert method == "docling-ocr"
    _fake_docling(monkeypatch, BROKEN)
    monkeypatch.setenv("INVOICE_OCR_GEOMETRY", "0")
    _, method = parsing.extract_pdf(scanned_pdf)
    assert method == "docling-ocr"
    assert hits == []


def test_optional_pass_errors_do_not_fail_primary_ocr(monkeypatch, scanned_pdf):
    _fake_docling(monkeypatch, BROKEN)
    monkeypatch.setenv("INVOICE_OCR_GEOMETRY", "auto")

    def bad_extra(*_):
        raise RuntimeError("Secondary OCR failed")

    monkeypatch.setattr(evidence, "extract_geometry_rows", bad_extra)
    text, method = parsing.extract_pdf(scanned_pdf)
    assert text == BROKEN.strip()
    assert method == "docling-ocr"


def test_numpy_boxes_process_without_ambiguous_truth_value(monkeypatch, scanned_pdf):
    # Matches the real RapidOCR API: .boxes is a NumPy array, not a list.
    observed = [
        (121, 732, "Product/service"), (417, 732, "QTY"),
        (552, 733, "Rate"), (703, 733, "Net"),
        (117, 837, "Pallet shipping"), (946, 837, "2"),
        (1168, 836, "45.00"), (1412, 837, "90.00"),
        (1024, 1155, "Net supplies:90.00"),
    ]
    boxes = np.array([[[x, y-5], [x+30, y-5], [x+30, y+5], [x, y+5]]
                      for x, y, _ in observed], dtype=np.float32)

    class FakeOCR:
        def __call__(self, *_args):
            return SimpleNamespace(txts=np.array([v[2] for v in observed]), boxes=boxes)

    rapidocr = ModuleType("rapidocr")
    rapidocr.RapidOCR = FakeOCR
    monkeypatch.setitem(sys.modules, "rapidocr", rapidocr)
    assert evidence.extract_geometry_rows(scanned_pdf) == ROWS


def test_empty_numpy_boxes_are_ignored(monkeypatch, scanned_pdf):
    class FakeOCR:
        def __call__(self, *_args):
            return SimpleNamespace(txts=np.array([], dtype=str), boxes=np.empty((0, 4, 2)))

    rapidocr = ModuleType("rapidocr")
    rapidocr.RapidOCR = FakeOCR
    monkeypatch.setitem(sys.modules, "rapidocr", rapidocr)
    assert evidence.extract_geometry_rows(scanned_pdf) == []


def test_geometry_method_always_requires_human_review(tmp_path, monkeypatch):
    # Even if rules are used on OCR text, 'docling-ocr+geometry200-auto' is
    # untrusted extraction and must not bypass explicit review.
    from fastapi.testclient import TestClient
    from backend.invoice_processing.api import create_app
    from backend.invoice_processing.worker import process_one
    from backend.invoice_processing.parsing import extract_pdf

    source = Path(__file__).resolve().parents[2] / "samples/invoices/01_blue_meadow_dairy.pdf"
    app = create_app(database_url=f"sqlite:///{tmp_path / 'db.sqlite'}", storage_dir=tmp_path / "uploads", api_key="test-secret")
    with TestClient(app) as client:
        header = {"x-api-key": "test-secret"}
        with source.open("rb") as stream:
            response = client.post("/api/v1/invoices/upload", headers=header,
                                   files={"files": ("source.pdf", stream, "application/pdf")})
        assert response.status_code == 202
        doc_id = response.json()["uploaded"][0]["id"]
        process_one(app.state.invoice_store,
                    parser=lambda path: (extract_pdf(path)[0], "docling-ocr+geometry200-auto"),
                    mode="rules")
        details = client.get(f"/api/v1/invoices/{doc_id}", headers=header).json()
        assert "VERIFY_EXTRACTION" in {item["code"] for item in details["issues"]}
        assert details["status"] == "review_required"


def test_existing_eager_flag_still_works(monkeypatch, scanned_pdf):
    _fake_docling(monkeypatch, COMPLETE)
    monkeypatch.setenv("INVOICE_OCR_GEOMETRY", "1")
    monkeypatch.setattr(evidence, "extract_geometry_rows", lambda path: ROWS)
    text, method = parsing.extract_pdf(scanned_pdf)
    assert method == "docling-ocr+geometry200"
    assert evidence.GEOMETRY_MARKER in text
