from io import BytesIO
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from backend.invoice_processing.api import create_app
from backend.invoice_processing.worker import process_one


def pdf_bytes(text):
    output = BytesIO()
    page = canvas.Canvas(output)
    y = 780
    for line in text.splitlines():
        page.drawString(70, y, line)
        y -= 22
    page.save()
    return output.getvalue()


INVOICE = """Supplier: Blue Meadow Dairy
Invoice Number: BMD-001
Invoice Date: 2026-11-01
Due Date: 2026-12-01
Currency: USD
Item | Quantity | Unit Price | Amount
Whole Milk | 10 | 4.25 | 42.50
Subtotal: 42.50
Tax: 2.50
Total: 45.00
"""


@pytest.fixture
def api(tmp_path):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'db.sqlite'}", storage_dir=tmp_path / "pdf", api_key="test-secret")
    with TestClient(app) as client:
        yield client, app.state.invoice_store


def test_auth_required(api):
    client, _ = api
    assert client.get("/api/v1/invoices").status_code == 401
    assert client.get("/health").status_code == 200


def test_batch_upload_worker_and_duplicate(api):
    client, store = api
    headers = {"x-api-key": "test-secret"}
    payload = pdf_bytes(INVOICE)
    r = client.post("/api/v1/invoices/upload", files=[("files", ("milk.pdf", payload, "application/pdf")), ("files", ("milk_copy.pdf", payload, "application/pdf"))], headers=headers)
    assert r.status_code == 202, r.text
    ids = [v["id"] for v in r.json()["uploaded"]]
    assert process_one(store, mode="rules") is not None
    assert process_one(store, mode="rules") is not None
    assert process_one(store, mode="rules") is None
    results = [client.get(f"/api/v1/invoices/{id}", headers=headers).json() for id in ids]
    assert sorted(x["status"] for x in results) == ["processed", "review_required"]
    assert any("POSSIBLE_DUPLICATE" in [flag["code"] for flag in x["issues"]] for x in results)
    assert all(x["extraction"]["total"] == "45.00" for x in results)
    assert all(x["extraction_method"].endswith("+rules-baseline") for x in results)


def test_bad_pdf_and_path_traversal(api):
    client, store = api
    headers = {"x-api-key": "test-secret"}
    r = client.post("/api/v1/invoices/upload", files={"files": ("../../../bad.pdf", b"not a pdf", "application/pdf")}, headers=headers)
    assert r.status_code == 400
    assert list(store.storage_dir.glob("*.pdf")) == []


def test_incorrect_total_flagged(api):
    client, store = api
    headers = {"x-api-key": "test-secret"}
    payload = pdf_bytes(INVOICE.replace("Total: 45.00", "Total: 50.00"))
    r = client.post("/api/v1/invoices/upload", files={"files": ("wrong.pdf", payload, "application/pdf")}, headers=headers)
    assert r.status_code == 202
    job_id = r.json()["uploaded"][0]["id"]
    process_one(store, mode="rules")
    doc = client.get(f"/api/v1/invoices/{job_id}", headers=headers).json()
    assert doc["status"] == "review_required"
    assert "TOTAL_MISMATCH" in [x["code"] for x in doc["issues"]]


def test_no_ocr_without_docling_reports_failure(api, monkeypatch):
    from backend.invoice_processing import worker
    from backend.invoice_processing.parsing import ParseFailure
    client, store = api
    headers = {"x-api-key": "test-secret"}
    r = client.post("/api/v1/invoices/upload", files={"files": ("scan.pdf", pdf_bytes(INVOICE), "application/pdf")}, headers=headers)
    job_id = r.json()["uploaded"][0]["id"]
    def fail_scan(path): raise ParseFailure("OCR_UNAVAILABLE")
    worker.process_one(store, parser=fail_scan, mode="rules")
    doc = client.get(f"/api/v1/invoices/{job_id}", headers=headers).json()
    assert doc["status"] == "failed"
    assert doc["error_code"] == "OCR_UNAVAILABLE"
