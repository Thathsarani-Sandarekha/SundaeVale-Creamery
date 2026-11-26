"""Preview reads an existing checkpoint but never changes it."""
from pathlib import Path
import hashlib
import json

import pytest

from scripts.preview_ocr_guardrails import preview
from backend.tests.test_ocr_grounding import OCR_TEXT, candidate


def create_files(tmp_path: Path):
    name = "v2_18_scanned.pdf"
    (tmp_path / name).write_bytes(b"PDF stub")
    manifest = tmp_path / "ground_truth_scans_only.json"
    manifest.write_text(json.dumps({"label_review_status": "generated_not_independently_reviewed",
        "fixtures": [{"file": name, "primary_scenario": "scanned", "expected": {
            "supplier_name": "Lakeshore Logistics LLC", "invoice_number": "LL-2026-0219",
            "line_items": [{"description": "Pallet shipping", "quantity": "2", "unit_price": "45",
                            "line_total": "90"}], "subtotal": "90", "tax": "0",
            "shipping": "0", "discount": "0", "total": "90"}}]}), encoding="utf-8")
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({"manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "results": {name: {"status": "ok", "candidate": candidate().model_dump(mode="json")}}}), encoding="utf-8")
    return name, manifest, checkpoint


def test_preview_is_read_only_and_improves_grounded_fields(tmp_path):
    name, manifest, checkpoint = create_files(tmp_path)
    original = checkpoint.read_bytes()
    report = preview(manifest, checkpoint, name, parser=lambda path: (OCR_TEXT, "docling-ocr"))
    assert report["source_method"] == "docling-ocr"
    assert report["baseline"]["field_accuracy"] < report["source_grounded_preview"]["field_accuracy"]
    assert "line_items.0.quantity" in report["source_grounded_preview"]["different_fields"]
    assert checkpoint.read_bytes() == original


def test_preview_rejects_mismatched_checkpoint(tmp_path):
    name, manifest, checkpoint = create_files(tmp_path)
    data = json.loads(checkpoint.read_text())
    data["manifest_sha256"] = "bad"
    checkpoint.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="different manifest"):
        preview(manifest, checkpoint, name, parser=lambda path: (OCR_TEXT, "docling-ocr"))
