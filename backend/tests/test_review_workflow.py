"""Tests for field corrections, conflict handling, human approval and CSV export."""
import csv
from io import BytesIO, StringIO

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from backend.invoice_processing.api import create_app
from backend.invoice_processing.worker import process_one

HEADERS = {"x-api-key": "dev-local-secret"}
RAW = """Supplier: Blue Meadow Dairy
Invoice Number: BMD-501
Invoice Date: 2026-11-01
Due Date: 2026-12-01
Currency: USD
Item | Quantity | Unit Price | Amount
Whole Milk | 10 | 4.25 | 42.50
Subtotal: 42.50
Tax: 2.50
Total: 50.00
"""


def pdf_bytes(text):
    mem = BytesIO()
    pdf = canvas.Canvas(mem)
    for i, line in enumerate(text.splitlines()):
        pdf.drawString(70, 780 - i * 22, line)
    pdf.save()
    return mem.getvalue()


@pytest.fixture
def system(tmp_path):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", storage_dir=tmp_path / "uploads", api_key=HEADERS['x-api-key'])
    with TestClient(app) as client:
        yield client, app.state.invoice_store


def uploaded(system, raw=RAW):
    client, store = system
    r = client.post('/api/v1/invoices/upload', headers=HEADERS,
                    files={'files': ('supplier.pdf', pdf_bytes(raw), 'application/pdf')})
    assert r.status_code == 202, r.text
    doc_id = r.json()['uploaded'][0]['id']
    assert process_one(store, mode='rules') == doc_id
    return doc_id, client.get(f'/api/v1/invoices/{doc_id}', headers=HEADERS).json()


def save(client, doc_id, doc, total='45.00', *, reviewer='Finance Reviewer'):
    original = doc['extraction'].copy()
    original['total'] = total
    r = client.patch(f'/api/v1/invoices/{doc_id}/review', headers=HEADERS,
                     json={'review_token': doc['review_token'], 'reviewer': reviewer, 'extraction': original})
    return r


def test_review_fix_approve_export_audit(system):
    client, _ = system
    doc_id, doc = uploaded(system)
    assert doc['status'] == 'review_required'
    assert 'TOTAL_MISMATCH' in [f['code'] for f in doc['issues']]
    r = save(client, doc_id, doc)
    assert r.status_code == 200, r.text
    edited = r.json()
    assert edited['extraction']['total'] == '45.00'
    assert edited['status'] == 'processed'
    assert edited['issues'] == []
    stale = save(client, doc_id, doc)
    assert stale.status_code == 409

    r = client.post(f'/api/v1/invoices/{doc_id}/approve', headers=HEADERS,
                    json={'review_token': edited['review_token'], 'reviewer': 'Finance Reviewer'})
    assert r.status_code == 200, r.text
    assert r.json()['status'] == 'approved'
    export = client.post('/api/v1/invoices/export', headers=HEADERS,
                         json={'document_ids': [doc_id], 'reviewer': 'Finance Reviewer'})
    assert export.status_code == 200
    assert export.headers['content-type'].startswith('text/csv')
    rows = list(csv.DictReader(StringIO(export.text)))
    assert len(rows) == 1
    assert rows[0]['total'] == '45.00'
    assert rows[0]['supplier_name'] == 'Blue Meadow Dairy'
    assert client.get(f'/api/v1/invoices/{doc_id}', headers=HEADERS).json()['status'] == 'exported'
    events = client.get(f'/api/v1/invoices/{doc_id}/audit', headers=HEADERS).json()['events']
    assert [e['event'] for e in events] == ['uploaded', 'processing_started', 'processed', 'review_saved', 'approved', 'exported']
    assert events[3]['details']['changed_fields'] == ['total']
    assert '45.00' not in str(events[3]['details'])


def test_approval_blocked_until_validation_fixed(system):
    client, _ = system
    doc_id, doc = uploaded(system)
    r = client.post(f'/api/v1/invoices/{doc_id}/approve', headers=HEADERS,
                    json={'review_token': doc['review_token'], 'reviewer': 'Reviewer'})
    assert r.status_code == 409
    assert client.post('/api/v1/invoices/export', headers=HEADERS,
                       json={'document_ids': [doc_id], 'reviewer': 'Reviewer'}).status_code == 409
    assert client.get(f'/api/v1/invoices/{doc_id}', headers=HEADERS).json()['status'] == 'review_required'


def test_reject_requires_reason_and_is_final(system):
    client, _ = system
    doc_id, doc = uploaded(system)
    r = client.post(f'/api/v1/invoices/{doc_id}/reject', headers=HEADERS,
                    json={'review_token': doc['review_token'], 'reviewer': 'Reviewer', 'reason': 'Incorrect supplier document'})
    assert r.status_code == 200
    assert r.json()['status'] == 'rejected'
    assert save(client, doc_id, r.json()).status_code == 409
    assert client.post('/api/v1/invoices/export', headers=HEADERS,
                       json={'document_ids': [doc_id], 'reviewer': 'Reviewer'}).status_code == 409


def test_duplicate_cannot_be_approved(system):
    client, _ = system
    id1, _ = uploaded(system, RAW.replace('Total: 50.00', 'Total: 45.00'))
    id2, doc2 = uploaded(system, RAW.replace('Total: 50.00', 'Total: 45.00'))
    assert id1 != id2 and doc2['status'] == 'review_required'
    assert any(x['code'] == 'POSSIBLE_DUPLICATE' for x in doc2['issues'])
    r = client.post(f'/api/v1/invoices/{id2}/approve', headers=HEADERS,
                    json={'review_token': doc2['review_token'], 'reviewer': 'Reviewer'})
    assert r.status_code == 409


def test_csv_formula_injection_escaped(system):
    client, _ = system
    doc_id, doc = uploaded(system, RAW.replace('Supplier: Blue Meadow Dairy', 'Supplier: =HYPERLINK(0)')
                           .replace('Total: 50.00', 'Total: 45.00'))
    assert doc['status'] == 'processed'
    approval = client.post(f'/api/v1/invoices/{doc_id}/approve', headers=HEADERS,
                           json={'review_token': doc['review_token'], 'reviewer': 'Reviewer'})
    assert approval.status_code == 200
    export = client.post('/api/v1/invoices/export', headers=HEADERS,
                         json={'document_ids': [doc_id], 'reviewer': 'Reviewer'})
    assert export.status_code == 200
    row = list(csv.DictReader(StringIO(export.text)))[0]
    assert row['supplier_name'].startswith("'=")


def test_pdf_proxy_and_auth(system):
    client, _ = system
    doc_id, _ = uploaded(system)
    assert client.get(f'/api/v1/invoices/{doc_id}/pdf').status_code == 401
    pdf = client.get(f'/api/v1/invoices/{doc_id}/pdf', headers=HEADERS)
    assert pdf.status_code == 200
    assert pdf.headers['content-type'].startswith('application/pdf')
    assert pdf.content.startswith(b'%PDF-')
    assert client.get(f'/api/v1/invoices/{doc_id}/audit').status_code == 401


def test_cannot_export_unknown_document_or_duplicate_ids(system):
    client, _ = system
    doc_id, doc = uploaded(system, RAW.replace('Total: 50.00', 'Total: 45.00'))
    client.post(f'/api/v1/invoices/{doc_id}/approve', headers=HEADERS,
                json={'review_token': doc['review_token'], 'reviewer': 'Reviewer'})
    assert client.post('/api/v1/invoices/export', headers=HEADERS,
                       json={'document_ids': [doc_id, doc_id], 'reviewer': 'Reviewer'}).status_code == 400
    assert client.post('/api/v1/invoices/export', headers=HEADERS,
                       json={'document_ids': [doc_id, 'absent-id'], 'reviewer': 'Reviewer'}).status_code == 404
    assert client.get(f'/api/v1/invoices/{doc_id}', headers=HEADERS).json()['status'] == 'approved'
