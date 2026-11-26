"""Synthetic demo fixture safety and accounting consistency checks."""
from collections import Counter
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select

from scripts.seed_finance_demo import fixtures, render_invoice, seed
from backend.invoice_processing.api import create_app
from backend.invoice_processing.schemas import InvoiceCandidate
from backend.invoice_processing.storage import LocalStorage
from backend.invoice_processing.store import InvoiceDocument, AuditEvent, InvoiceStore
from backend.invoice_processing.validation import issues_for


def test_demo_fixtures_cover_months_suppliers_approval_and_due_dates():
    today = date(2026, 11, 22)
    rows = fixtures(today)
    assert len(rows) == 60
    assert len({r['invoice'].invoice_date.strftime('%Y-%m') for r in rows}) == 6
    assert len({r['invoice'].supplier_name for r in rows}) >= 9
    assert len({r['invoice'].invoice_number for r in rows}) == 59  # one deliberate duplicate
    statuses = Counter(r['status'] for r in rows)
    assert statuses['approved'] > 25 and statuses['review_required'] >= 8
    assert statuses['exported'] >= 4 and statuses['rejected'] >= 4
    assert any(today <= r['invoice'].due_date <= date(2026, 11, 29) for r in rows)


def test_demo_approval_statuses_match_validation_and_exception_story():
    rows = fixtures(date(2026, 11, 22))
    for row in rows:
        inv, status = row['invoice'], row['status']
        check = issues_for(inv, duplicate=(row['index'] == 6))
        if status in ('approved', 'exported', 'processed'):
            assert not check
            assert not row['issues']
        if status == 'rejected':
            assert any(x.code == 'TOTAL_MISMATCH' for x in check)
        if row['index'] == 6:
            assert any(x.code == 'POSSIBLE_DUPLICATE' for x in check)


def test_generated_pdf_matches_grounded_fields():
    inv = fixtures(date(2026, 11, 22))[0]['invoice']
    from io import BytesIO
    reader = PdfReader(BytesIO(render_invoice(inv)))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert 'SYNTHETIC DEMONSTRATION DATA' in text
    assert inv.supplier_name in text
    assert inv.invoice_number in text
    assert f'{inv.total:.2f}' in text
    assert inv.line_items[0].description in text


def test_seed_isolated_idempotent_and_readable_through_api(tmp_path, monkeypatch):
    db = tmp_path / 'fixture_only.db'
    uploads = tmp_path / 'fixture_pdfs'
    result = seed(today=date(2026, 11, 22), database=db, pdf_dir=uploads)
    assert result['status'] == 'created' and result['records'] == 60
    again = seed(today=date(2026, 11, 22), database=db, pdf_dir=uploads)
    assert again['status'] == 'already_populated'
    assert len(list(uploads.glob('*.pdf'))) == 60
    store = InvoiceStore(f'sqlite:///{db.as_posix()}', uploads, LocalStorage(uploads))
    with store.Session() as session:
        documents = session.scalars(select(InvoiceDocument)).all()
        assert len(documents) == 60
        assert session.query(AuditEvent).count() == 60
        assert all(d.extraction_method == 'synthetic-demo-fixture' for d in documents)
        assert all(d.status != 'queued' for d in documents)
        assert all(json.loads(d.extraction_json)['currency'] == 'USD' for d in documents)
        # Never invent an authenticated employee signoff during fixture creation.
        assert all(e.event == 'synthetic_demo_fixture_loaded' for e in session.query(AuditEvent))
        target = documents[0]
    monkeypatch.setenv('SUNDAEVALE_DEMO_MODE', '1')
    app = create_app(f'sqlite:///{db.as_posix()}', uploads, 'demo-secret', LocalStorage(uploads))
    with TestClient(app) as client:
        r = client.get('/api/v1/invoices', headers={'x-api-key':'demo-secret'})
        assert r.status_code == 200
        assert r.json()['demo_mode'] is True
        assert len(r.json()['documents']) == 60
        pdf = client.get(f'/api/v1/invoices/{target.id}/pdf', headers={'x-api-key':'demo-secret'})
        assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF-')


def test_never_seed_default_application_database(tmp_path):
    from scripts.seed_finance_demo import ROOT
    with pytest.raises(ValueError, match='default application database'):
        seed(database=ROOT / '.invoice_data' / 'invoices.db', pdf_dir=tmp_path / 'other')
