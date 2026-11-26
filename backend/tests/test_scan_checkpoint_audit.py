from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

from scripts.audit_scanned_checkpoint import field_group, scan_audit


def _fixtures(tmp_path):
    manifest=tmp_path/'manifest.json'
    source={"dataset":"synthetic scanned invoice test", "fixtures":[{"file":"scan.pdf", "primary_scenario":"scanned", "layout":"grid", "expected":{"supplier_name":"Seller LLC", "invoice_number":"A-1", "currency":"USD", "line_items":[{"description":"Milk", "quantity":"2", "unit_price":"5.00", "line_total":"10.00"}],"subtotal":"10.00","total":"10.00"}}]}
    manifest.write_text(json.dumps(source),encoding="utf-8")
    ck=tmp_path/'checkpoint.json'
    results={"format":1,"manifest_sha256":hashlib.sha256(manifest.read_bytes()).hexdigest(),"mode":"groq", "include_scans":True,"results":{"scan.pdf":{"status":"ok","method":"docling-ocr+groq-structured","latency_ms":12000,"candidate":{"supplier_name":"Buyer Inc", "invoice_number":"A-1", "currency":"USD", "line_items":[{"description":"Milk", "quantity":"2.0", "unit_price":"5", "line_total":"10.00"}],"subtotal":"10.0","total":"10.00"}}}}
    ck.write_text(json.dumps(results),encoding="utf-8")
    return manifest,ck


def test_field_grouping():
    assert field_group('line_items.10.description') == 'line_items.*.description'
    assert field_group('supplier_name') == 'supplier_name'


def test_scanned_report_does_not_penalize_decimal_format(tmp_path):
    manifest,checkpoint=_fixtures(tmp_path)
    result=scan_audit(manifest,checkpoint)
    assert result['scored']==1
    assert result['field_accuracy']<1
    assert [r['field'] for r in result['mismatch_groups']]==['supplier_name']
    assert all('expected' not in e for e in result['per_invoice'][0]['mismatches'])


def test_scanned_report_optional_values(tmp_path):
    manifest,checkpoint=_fixtures(tmp_path)
    result=scan_audit(manifest,checkpoint,show_values=True)
    mismatch=result['per_invoice'][0]['mismatches'][0]
    assert mismatch['expected']=='Seller LLC'
    assert mismatch['extracted']=='Buyer Inc'


def test_manifest_mismatch_rejected(tmp_path):
    manifest,checkpoint=_fixtures(tmp_path)
    data=json.loads(checkpoint.read_text()); data['manifest_sha256']='bad'; checkpoint.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Checkpoint and manifest'):
        scan_audit(manifest,checkpoint)


def test_non_synthetic_rejected(tmp_path):
    manifest,checkpoint=_fixtures(tmp_path)
    data=json.loads(manifest.read_text());data['dataset']='real supplier invoices';manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='synthetic'):
        scan_audit(manifest,checkpoint)
