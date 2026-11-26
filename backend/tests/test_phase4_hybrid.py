"""Hybrid routing regression tests: no external network requests or persisted data."""
from pathlib import Path
import json
from fastapi.testclient import TestClient
from backend.invoice_processing import extraction
from backend.invoice_processing.api import create_app
from backend.invoice_processing.evaluation import evaluate
from backend.invoice_processing.parsing import extract_pdf
from backend.invoice_processing.worker import process_one
from backend.invoice_processing.schemas import InvoiceCandidate
from backend.invoice_processing.validation import issues_for

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples" / "invoices"


def test_hybrid_uses_rules_on_complete_digital_invoice(monkeypatch):
    def no_external(_):
        raise AssertionError("Groq must not be called for a well-structured invoice")
    monkeypatch.setattr(extraction, "extract_groq", no_external)
    text, parser = extract_pdf(SAMPLES / "01_blue_meadow_dairy.pdf")
    candidate, method = extraction.extract_invoice(text, "hybrid")
    assert method == "rules-baseline"
    assert candidate.total == 366


def test_hybrid_does_not_guess_missing_invoice_date(monkeypatch):
    def no_external(_):
        raise AssertionError("A genuinely missing invoice date is a review issue, not a reason to hallucinate")
    monkeypatch.setattr(extraction, "extract_groq", no_external)
    text, _ = extract_pdf(SAMPLES / "05_ember_cocoa_missing_date.pdf")
    candidate, method = extraction.extract_invoice(text, "hybrid")
    assert method == "rules-baseline"
    assert candidate.invoice_date is None
    assert "MISSING_REQUIRED" in [issue.code for issue in issues_for(candidate)]


def test_hybrid_does_not_repair_printed_wrong_total(monkeypatch):
    monkeypatch.setattr(extraction, "extract_groq", lambda _: (_ for _ in ()).throw(AssertionError("should use rules")))
    text, _ = extract_pdf(SAMPLES / "03_frostpak_incorrect_total.pdf")
    candidate, method = extraction.extract_invoice(text, "hybrid")
    assert method == "rules-baseline"
    assert candidate.total == 180
    assert "TOTAL_MISMATCH" in [issue.code for issue in issues_for(candidate)]


def test_hybrid_calls_groq_only_for_structural_failure(monkeypatch):
    calls = []
    def fake_groq(text):
        calls.append(text)
        return InvoiceCandidate(supplier_name="Supplier", invoice_number="X-01", currency="USD", subtotal="20", total="20",
                                line_items=[{"description":"Milk","quantity":"2","unit_price":"10","line_total":"20"}])
    monkeypatch.setattr(extraction, "extract_groq", fake_groq)
    candidate, method = extraction.extract_invoice("SUPPLIER INVOICE\nUnusual table format; total due 20", "hybrid")
    assert method == "groq-fallback"
    assert calls and candidate.invoice_number == "X-01"


def test_hybrid_baseline_evaluation_matches_untouched_rules_without_external_calls(monkeypatch):
    monkeypatch.setattr(extraction, "extract_groq", lambda _: (_ for _ in ()).throw(AssertionError("not needed")))
    # An evaluator uses the module extract_invoice entry point, which does not touch Groq on these fixtures.
    baseline = evaluate(SAMPLES / "ground_truth.json", mode="rules")
    hybrid = evaluate(SAMPLES / "ground_truth.json", mode="hybrid", allow_external=True)
    assert hybrid["field_accuracy"] == baseline["field_accuracy"] == 1.0
    assert hybrid["scored_count"] == 5
    assert all(row["differing_fields"] == [] for row in hybrid["per_document"])


def test_worker_requires_review_of_ocr_or_ai_before_approval(tmp_path):
    api_key = "test-secret"
    app = create_app(database_url=f"sqlite:///{tmp_path / 'invoice.sqlite'}", storage_dir=tmp_path / "stored", api_key=api_key)
    with TestClient(app) as client:
        headers = {"x-api-key": api_key}
        with (SAMPLES / "01_blue_meadow_dairy.pdf").open("rb") as file:
            response = client.post("/api/v1/invoices/upload", files={"files":("milk.pdf", file, "application/pdf")}, headers=headers)
        assert response.status_code == 202
        doc_id = response.json()["uploaded"][0]["id"]
        # Fake OCR method but real valid PDF text; test review behavior without loading OCR models.
        def mock_parser(path):
            return extract_pdf(path)[0], "docling-ocr"
        process_one(app.state.invoice_store, parser=mock_parser, mode="rules")
        details = client.get(f"/api/v1/invoices/{doc_id}", headers=headers).json()
        assert details["status"] == "review_required"
        assert "VERIFY_EXTRACTION" in [x["code"] for x in details["issues"]]
        rejected = client.post(f"/api/v1/invoices/{doc_id}/approve", headers=headers,
            json={"review_token":details["review_token"],"reviewer":"Local Finance Reviewer"})
        assert rejected.status_code == 409
        saved = client.patch(f"/api/v1/invoices/{doc_id}/review", headers=headers,
            json={"review_token":details["review_token"],"reviewer":"Local Finance Reviewer","extraction":details["extraction"]})
        assert saved.status_code == 200
        assert saved.json()["status"] == "processed"
        approved = client.post(f"/api/v1/invoices/{doc_id}/approve", headers=headers,
            json={"review_token":saved.json()["review_token"],"reviewer":"Local Finance Reviewer"})
        assert approved.status_code == 200


def test_hybrid_retries_structural_validation_error_through_groq(monkeypatch):
    monkeypatch.setattr(extraction, "extract_groq", lambda _: InvoiceCandidate(
        supplier_name="Fixed Supplier", invoice_number="FS-1", currency="USD",
        line_items=[{"description":"Item","quantity":1,"unit_price":1,"line_total":1}],
        subtotal=1, total=1))
    text = "Supplier: Bad Layout\nInvoice Number: BAD-1\nInvoice Date: NOT-A-DATE\nTotal: 10"
    candidate, method = extraction.extract_invoice(text, "hybrid")
    assert method == "groq-fallback" and candidate.invoice_number == "FS-1"
