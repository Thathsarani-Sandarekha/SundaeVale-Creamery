"""Offline tests of the checkpointed benchmark. Zero Groq network calls."""
import json
from pathlib import Path

import pytest

from scripts.benchmark_groq_paced import run, _report, _manifest_records
from backend.invoice_processing.extraction import GroqExtractionError
from backend.invoice_processing.schemas import InvoiceCandidate


def fixture(tmp_path: Path, n=5):
    records = []
    for i in range(n):
        name = f"invoice-{i}.pdf"
        (tmp_path / name).write_bytes(b"stub")
        records.append({"file": name, "primary_scenario": "digital_layout",
                        "expected": {"supplier_name": "Example Dairy", "invoice_number": f"INV-{i}",
                                     "invoice_date": "2026-10-01", "due_date": "2026-11-01",
                                     "currency": "USD", "subtotal": "10.00", "total": "10.00",
                                     "tax": "0.00", "shipping": "0.00", "discount": "0.00",
                                     "line_items": [{"description": "Milk", "quantity": "1", "unit_price": "10.00", "line_total": "10.00"}]},
                        "expected_issue_codes": []})
    manifest = tmp_path / "ground_truth.json"
    manifest.write_text(json.dumps({"fixtures": records}), encoding="utf-8")
    return manifest, records


def test_batched_resume_and_coverage(tmp_path):
    manifest, records = fixture(tmp_path)
    calls = []
    naps = []

    def extractor(text, mode):
        i = int(text)
        calls.append(i)
        return InvoiceCandidate.model_validate(records[i]["expected"]), "stub"

    def parser(path):
        return path.stem.split("-")[-1], "pdf"

    kw = dict(manifest=manifest, mode="groq", include_scans=False, gap_seconds=4,
              output_dir=tmp_path / "out", parser=parser, extractor=extractor, sleeper=naps.append)
    first = run(max_documents=3, **kw)
    assert first["scored_count"] == 3 and first["pending_count"] == 2
    assert first["benchmark_complete"] is False
    assert first["successful_coverage_pct"] == 60.0
    assert first["field_accuracy_on_successes"] == 1
    assert naps == [4, 4]
    second = run(max_documents=3, **kw)
    assert second["attempted_this_run"] == 2
    assert second["scored_count"] == 5 and second["benchmark_complete"]
    assert calls == [0, 1, 2, 3, 4]
    assert naps == [4, 4, 4]
    assert len((tmp_path / "out/per-invoice.csv").read_text().splitlines()) == 6


def test_429_stops_and_requires_explicit_retry(tmp_path):
    manifest, records = fixture(tmp_path, 3)
    calls = []
    def extractor(text, mode):
        calls.append(text)
        if len(calls) == 2:
            raise GroqExtractionError("GROQ_HTTP_429")
        return InvoiceCandidate.model_validate(records[int(text)]["expected"]), "stub"
    kw = dict(manifest=manifest, mode="groq", include_scans=False, gap_seconds=0,
              output_dir=tmp_path / "out", parser=lambda path: (path.stem.split("-")[-1], "stub"),
              extractor=extractor, sleeper=lambda _: None)
    first = run(max_documents=3, **kw)
    assert first["attempted_this_run"] == 2
    assert first["failure_count"] == 1 and first["pending_count"] == 1
    assert first["stopped_on_error"] == "GROQ_HTTP_429"
    # Skip a recorded failure unless the caller explicitly requests a retry.
    second = run(max_documents=3, **kw)
    assert second["scored_count"] == 2 and second["failure_count"] == 1
    third = run(max_documents=3, retry_failures=True, **kw)
    assert third["benchmark_complete"] and third["scored_count"] == 3


def test_mismatched_manifest_does_not_reset_checkpoint(tmp_path):
    manifest, records = fixture(tmp_path, 2)
    kw = dict(manifest=manifest, mode="groq", include_scans=False, gap_seconds=0,
              max_documents=1, output_dir=tmp_path / "out", sleeper=lambda _: None,
              parser=lambda _: ("anything", "stub"),
              extractor=lambda text, mode: (InvoiceCandidate.model_validate(records[0]["expected"]), "stub"))
    run(**kw)
    content = (tmp_path / "out/checkpoint.json").read_text()
    data = json.loads(manifest.read_text())
    data["dataset"] = "changed"
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="different manifest"):
        run(**kw)
    assert (tmp_path / "out/checkpoint.json").read_text() == content


def test_no_leak_of_document_text_in_failed_checkpoint(tmp_path):
    manifest, _ = fixture(tmp_path, 1)
    sensitive = "SENSITIVE DOCUMENT CONTENT THAT MUST NEVER BE LOGGED"
    result = run(manifest=manifest, mode="groq", include_scans=False, max_documents=1,
                 gap_seconds=0, output_dir=tmp_path / "out", parser=lambda _: (sensitive, "stub"),
                 extractor=lambda _t, _m: (_ for _ in ()).throw(GroqExtractionError("GROQ_HTTP_429")),
                 sleeper=lambda _: None)
    assert result["failure_count"] == 1
    assert sensitive not in (tmp_path / "out/checkpoint.json").read_text()


def test_reject_unsafe_manifest_path(tmp_path):
    manifest = tmp_path / "ground_truth.json"
    manifest.write_text(json.dumps({"fixtures": [{"file": "../secrets.pdf"}]}))
    with pytest.raises(ValueError, match="Unsafe"):
        _manifest_records(manifest.resolve(), False)


def test_requires_valid_batch_options(tmp_path):
    manifest, _ = fixture(tmp_path, 1)
    with pytest.raises(ValueError, match="max-documents"):
        run(manifest=manifest, mode="groq", include_scans=False, max_documents=11,
            gap_seconds=0, output_dir=tmp_path / "out")
