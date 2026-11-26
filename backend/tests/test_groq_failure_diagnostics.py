"""Provider failure diagnostics must be safe, actionable, and network-free."""
import httpx
import pytest

from backend.invoice_processing.extraction import extract_groq, GroqExtractionError
from backend.invoice_processing.evaluation import evaluate
from backend.invoice_processing.probe import main as probe_main
from pathlib import Path
import json


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_http_status_classified_without_leaking_credentials(monkeypatch, status):
    monkeypatch.setenv("GROQ_API_KEY", "secret-never-print")
    monkeypatch.setenv("GROQ_MODEL", "fake-test-model")

    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            req = httpx.Request("POST", url)
            return httpx.Response(status, request=req, text="private error message should never print")

    monkeypatch.setattr(httpx, "Client", Client)
    with pytest.raises(GroqExtractionError) as info:
        extract_groq("Private synthetic invoice data")
    assert str(info.value) == f"GROQ_HTTP_{status}"
    assert "secret-never-print" not in str(info.value)
    assert "private error message" not in str(info.value)


def test_evaluation_counts_inference_failures_without_false_accuracy():
    fixture = Path(__file__).resolve().parents[2]/"samples/invoices/ground_truth.json"
    def parser(_): return "fake invoice", "stub"
    def extractor(_, mode):
        raise GroqExtractionError("GROQ_HTTP_429")
    result = evaluate(fixture, mode="groq", allow_external=True, parser=parser, extractor=extractor)
    assert result["scored_count"] == 0
    assert result["skipped_count"] == 6
    assert result["coverage_pct"] == 0
    assert result["attempted_count"] == 5
    assert result["attempted_coverage_pct"] == 0
    assert result["failure_count"] == 5
    assert result["evaluation_complete"] is False
    assert result["skipped_reason_counts"] == {"GROQ_HTTP_429": 5, "scan_excluded_add_--include-scans": 1}
    assert result["field_accuracy"] is None


def test_probe_exposes_safe_failure_code(monkeypatch,capsys):
    from backend.invoice_processing import probe
    sample = Path(__file__).resolve().parents[2]/"samples/invoices/01_blue_meadow_dairy.pdf"
    monkeypatch.setattr(probe, "extract_pdf", lambda _: ("synthetic text", "pypdf-digital"))
    def fail(_, mode): raise GroqExtractionError("GROQ_HTTP_429")
    monkeypatch.setattr(probe, "extract_invoice", fail)
    result=probe_main([str(sample), "--mode", "groq", "--allow-external"])
    assert result==2
    assert json.loads(capsys.readouterr().out)=={"status":"failed", "error_code":"GROQ_HTTP_429"}
