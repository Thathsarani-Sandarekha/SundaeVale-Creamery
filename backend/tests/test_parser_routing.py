"""Regressions for digital invoices vs image-only OCR documents."""
from pathlib import Path
from types import SimpleNamespace
import sys
import types

from backend.invoice_processing.parsing import extract_pdf
from backend.invoice_processing.extraction import extract_rules
from backend.invoice_processing.validation import issues_for


SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "invoices"


def _fake_docling(monkeypatch, *, text="OCR extracted text from a simulated supplier invoice", fail_on_call=False):
    module = types.ModuleType("docling")
    converter_module = types.ModuleType("docling.document_converter")
    called = []

    class DocumentConverter:
        def convert(self, *args, **kwargs):
            called.append((args, kwargs))
            if fail_on_call:
                raise RuntimeError("Docling should not run for digital invoices")
            return SimpleNamespace(document=SimpleNamespace(export_to_markdown=lambda: text))

    converter_module.DocumentConverter = DocumentConverter
    monkeypatch.setitem(sys.modules, "docling", module)
    monkeypatch.setitem(sys.modules, "docling.document_converter", converter_module)
    return called


def test_sample_digital_invoices_skip_docling_and_extract_correctly(monkeypatch):
    called = _fake_docling(monkeypatch, fail_on_call=True)
    for filename, expected_status, expected_code in [
        ("01_blue_meadow_dairy.pdf", "processed", None),
        ("03_frostpak_incorrect_total.pdf", "review_required", "TOTAL_MISMATCH"),
    ]:
        raw, method = extract_pdf(SAMPLES / filename)
        assert method == "pypdf-digital"
        invoice = extract_rules(raw)
        codes = [issue.code for issue in issues_for(invoice)]
        assert ("review_required" if codes else "processed") == expected_status
        if expected_code:
            assert expected_code in codes
        else:
            assert codes == []
    assert called == []


def test_image_only_pdf_uses_docling_ocr(monkeypatch):
    called = _fake_docling(monkeypatch)
    raw, method = extract_pdf(SAMPLES / "02_orchard_valley_scanned.pdf")
    assert "OCR extracted text" in raw
    assert method == "docling-ocr"
    assert len(called) == 1


def test_existing_fixture_batch_end_to_end_and_retry(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.invoice_processing.api import create_app
    from backend.invoice_processing.worker import process_one
    from backend.invoice_processing.retry_failed import requeue_failed

    _fake_docling(monkeypatch, fail_on_call=True)
    app = create_app(database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
                     storage_dir=tmp_path / "pdfs", api_key="dev-secret")
    headers = {"x-api-key": "dev-secret"}
    with TestClient(app) as client:
        names = ("01_blue_meadow_dairy.pdf", "03_frostpak_incorrect_total.pdf")
        files = [("files", (name, (SAMPLES / name).read_bytes(), "application/pdf")) for name in names]
        uploaded = client.post("/api/v1/invoices/upload", headers=headers, files=files)
        assert uploaded.status_code == 202, uploaded.text
        store = app.state.invoice_store
        # A former extraction problem might have marked the jobs failed:
        from backend.invoice_processing.store import InvoiceDocument
        with store.Session.begin() as db:
            for item in uploaded.json()["uploaded"]:
                doc = db.get(InvoiceDocument, item["id"])
                doc.status = "failed"
                doc.error_code = "VALIDATIONERROR"
        assert requeue_failed(store, list(names)) == 2
        assert process_one(store, mode="rules") is not None
        assert process_one(store, mode="rules") is not None
        docs = {d["filename"]: d for d in client.get("/api/v1/invoices", headers=headers).json()["documents"]}
        assert docs[names[0]]["status"] == "processed"
        assert docs[names[1]]["status"] == "review_required"
        assert docs[names[0]]["error_code"] is None
        assert docs[names[1]]["error_code"] is None
        assert "TOTAL_MISMATCH" in [i["code"] for i in docs[names[1]]["issues"]]
