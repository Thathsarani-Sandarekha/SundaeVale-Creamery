"""No-network integrity checks for the 40-case synthetic invoice evaluation set."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

from backend.invoice_processing.schemas import InvoiceCandidate
from backend.invoice_processing.validation import issues_for, normalize_identity
from backend.invoice_processing.evaluation import evaluate

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "samples" / "evaluation_v2"


def manifest():
    return json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))


def test_v2_has_40_unique_fictional_cases_across_scenarios():
    data = manifest()
    fixtures = data["fixtures"]
    assert len(fixtures) == len({case["file"] for case in fixtures}) == 40
    assert dict(Counter(x["primary_scenario"] for x in fixtures)) == data["scenario_counts"]
    assert len({x["layout"] for x in fixtures}) >= 5
    assert sum(x.get("adversarial_note", False) for x in fixtures) == 2
    for case in fixtures:
        assert (DATA / case["file"]).exists()
        assert case["file"].endswith(".pdf")
        InvoiceCandidate.model_validate(case["expected"])
        assert case["expected"].get("currency") in (None,"USD")


def test_v2_scans_are_image_only_pdfs_and_digital_have_text():
    for case in manifest()["fixtures"]:
        reader = PdfReader(str(DATA / case["file"]), strict=True)
        assert len(reader.pages) == 1
        extracted_text = (reader.pages[0].extract_text() or "").strip()
        if case["primary_scenario"] == "scanned":
            assert extracted_text == "", case["file"]
            assert "/XObject" in reader.pages[0]["/Resources"]
        else:
            assert "SundaeVale Creamery" in extracted_text


def test_v2_validation_labels_match_printed_financial_fixture_values():
    seen = set()
    files_seen = set()
    for case in manifest()["fixtures"]:
        invoice = InvoiceCandidate.model_validate(case["expected"])
        identity = (normalize_identity(invoice.supplier_name), normalize_identity(invoice.invoice_number))
        duplicate = bool(all(identity) and identity in seen)
        actual_codes = {issue.code for issue in issues_for(invoice, duplicate=duplicate)}
        assert actual_codes == set(case["expected_issue_codes"]), case["file"]
        if case.get("duplicate_of"):
            assert case["duplicate_of"] in files_seen
            assert duplicate
        if all(identity):
            seen.add(identity)
        files_seen.add(case["file"])


def test_v2_rules_eval_has_expected_coverage_without_network():
    result = evaluate(DATA / "ground_truth.json", mode="rules")
    assert result["total_fixtures"] == 40
    assert result["scored_count"] == 32
    assert result["skipped_count"] == 8
    assert {r["reason"] for r in result["skipped"]} == {"scan_excluded_add_--include-scans"}
    # Harder layouts should expose the limitations of the intentionally basic regex baseline.
    assert result["field_accuracy"] is not None and result["field_accuracy"] < 1.0
