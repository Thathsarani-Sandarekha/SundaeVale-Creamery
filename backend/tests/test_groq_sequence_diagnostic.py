"""No-network tests for bounded Groq sequence diagnostics."""
from pathlib import Path
import json
import pytest

from scripts.diagnose_groq_sequence import diagnose, main
from backend.invoice_processing.extraction import GroqExtractionError
from backend.invoice_processing.schemas import InvoiceCandidate


def make_manifest(tmp_path, count=4):
    truth = {"supplier_name": "Example Dairy", "invoice_number": "INV-1", "currency": "USD", "total": "10.00", "subtotal": "10.00", "line_items": [{"description": "Milk", "quantity": "1", "unit_price": "10.00", "line_total": "10.00"}]}
    docs = []
    for n in range(count):
        fname = f"fixture-{n}.pdf"
        (tmp_path/fname).write_bytes(b"fake pdf")
        docs.append({"file": fname, "primary_scenario": "digital_layout", "expected": truth})
    manifest=tmp_path/"ground_truth.json"
    manifest.write_text(json.dumps({"fixtures": docs}), encoding="utf-8")
    return manifest, truth


def test_diagnostic_scores_successes_and_paces(tmp_path):
    manifest, truth = make_manifest(tmp_path)
    pauses = []
    result = diagnose(manifest=manifest, limit=3, offset=0, gap_seconds=2,
                      parser=lambda _: ("synthetic", "stub"),
                      extractor=lambda _: InvoiceCandidate.model_validate(truth),
                      sleeper=lambda s: pauses.append(s))
    assert result["attempted_count"] == result["success_count"] == 3
    assert result["stopped_early"] is False
    assert pauses == [2, 2]
    assert all(row["field_accuracy"] == 1 for row in result["results"])


def test_diagnostic_stops_on_429_without_retrying_or_leaking(tmp_path):
    manifest, _ = make_manifest(tmp_path)
    calls=[]
    def fake_groq(_):
        calls.append(1)
        raise GroqExtractionError("GROQ_HTTP_429")
    result = diagnose(manifest=manifest, limit=4, offset=0, gap_seconds=0,
                      parser=lambda _: ("secret document", "stub"),
                      extractor=fake_groq, sleeper=lambda _: None)
    assert len(calls) == 1
    assert result["selected_count"] == 4
    assert result["attempted_count"] == 1
    assert result["stopped_early"] is True
    assert result["results"][0]["error_code"] == "GROQ_HTTP_429"
    assert "secret document" not in json.dumps(result)


def test_invalid_limits_rejected(tmp_path):
    manifest, _ = make_manifest(tmp_path)
    with pytest.raises(ValueError, match="limit"):
        diagnose(manifest=manifest, limit=20, offset=0, gap_seconds=0)


def test_external_permission_required(tmp_path):
    with pytest.raises(SystemExit):
        main(["--manifest", str(tmp_path/"not-an-invoice.json")])
