import json
from decimal import Decimal
from pathlib import Path
import pytest

from backend.invoice_processing.evaluation import evaluate, _score_fields
from backend.invoice_processing.schemas import InvoiceCandidate
from backend.invoice_processing.probe import main as probe_main

MANIFEST=Path(__file__).resolve().parents[2]/'samples'/'invoices'/'ground_truth.json'


def test_baseline_manifest_contains_real_synthetic_cases():
    payload=json.loads(MANIFEST.read_text())
    assert len(payload['fixtures'])==6
    assert 'TOTAL_MISMATCH' in next(x['expected_issue_codes'] for x in payload['fixtures'] if x['file'].startswith('03_'))
    assert 'POSSIBLE_DUPLICATE' in next(x['expected_issue_codes'] for x in payload['fixtures'] if x['file'].startswith('04_'))


def test_baseline_reproducible_without_ocr_or_llm():
    report=evaluate(MANIFEST)
    assert report['total_fixtures']==6
    assert report['scored_count']==5
    assert report['skipped_count']==1
    assert report['skipped'][0]['reason']=='scan_excluded_add_--include-scans'
    assert report['field_accuracy']==1.0
    assert report['field_precision_recall_f1']['f1']==1.0
    assert report['issue_detection_precision_recall_f1']['f1']==1.0
    assert report['duplicate_detection_precision_recall_f1']['f1']==1.0
    assert len(report['per_document'])==5


def test_incorrect_output_counts_fp_and_fn():
    expected=InvoiceCandidate(supplier_name='Blue Meadow',total=Decimal('100'))
    actual=InvoiceCandidate(supplier_name='Blue Meadow',total=Decimal('110'))
    counts=_score_fields(expected,actual)
    assert counts['tp']==1 and counts['fp']==1 and counts['fn']==1


def test_external_groq_requires_explicit_permission():
    with pytest.raises(ValueError, match='--allow-external'):
        evaluate(MANIFEST,mode='groq')


def test_probe_does_not_touch_database(capsys):
    path=MANIFEST.parent/'01_blue_meadow_dairy.pdf'
    assert probe_main([str(path),'--mode','rules'])==0
    payload=json.loads(capsys.readouterr().out)
    assert payload['status']=='ok'
    assert payload['extraction']['total']=='366.00'
    assert payload['issue_codes']==[]


def test_probe_scanned_reports_absent_ocr_without_synthesizing_result(monkeypatch,capsys):
    from backend.invoice_processing import probe
    from backend.invoice_processing.parsing import ParseFailure
    monkeypatch.setattr(probe,'extract_pdf',lambda _: (_ for _ in ()).throw(ParseFailure('OCR_UNAVAILABLE')))
    result=probe_main([str(MANIFEST.parent/'02_orchard_valley_scanned.pdf')])
    assert result==2
    assert json.loads(capsys.readouterr().out)['error_code']=='OCR_UNAVAILABLE'
