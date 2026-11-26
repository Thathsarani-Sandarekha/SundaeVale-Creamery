"""Compare saved synthetic scan extraction against source-backed grounding.

No Groq calls, checkpoint writes, database access, or changes to existing
invoice records. May perform local Docling OCR on the selected synthetic PDF.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.invoice_processing.ocr_grounding import ground_explicit_fields
from backend.invoice_processing.evaluation import _field_values, _score_fields
from backend.invoice_processing.parsing import extract_pdf
from backend.invoice_processing.schemas import InvoiceCandidate
from backend.invoice_processing.validation import issues_for


def preview(manifest: Path, checkpoint_path: Path, name: str, parser=extract_pdf) -> dict:
    source = json.loads(manifest.read_text(encoding="utf-8"))
    if source.get("label_review_status") != "generated_not_independently_reviewed":
        raise ValueError("Expected original synthetic scan dataset")
    entries = {record["file"]: record for record in source["fixtures"]}
    record = entries.get(name)
    if record is None or record.get("primary_scenario") != "scanned":
        raise ValueError("File must be a scanned invoice listed in the synthetic manifest")
    saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if saved.get("manifest_sha256") != hashlib.sha256(manifest.read_bytes()).hexdigest():
        raise ValueError("Benchmark checkpoint belongs to a different manifest")
    prior = saved.get("results", {}).get(name, {})
    if prior.get("status") != "ok":
        raise ValueError("No successful cached extraction for selected invoice")
    pdf = (manifest.parent / name).resolve()
    if not pdf.is_relative_to(manifest.parent.resolve()):
        raise ValueError("Unsafe file path")
    text, method = parser(pdf)
    before = InvoiceCandidate.model_validate(prior["candidate"])
    after = ground_explicit_fields(text, before)
    expected = InvoiceCandidate.model_validate(record["expected"])
    target = _field_values(expected)
    def summarize(candidate: InvoiceCandidate):
        values = _field_values(candidate)
        s = _score_fields(expected, candidate)
        return {"field_accuracy": round(s["correct"] / s["compared"], 4),
                "different_fields": sorted(f for f in target.keys() | values.keys()
                                           if target.get(f) != values.get(f)),
                "validation_issues": sorted({i.code for i in issues_for(candidate)})}
    changes = []
    original, corrected = _field_values(before), _field_values(after)
    for field in sorted(original.keys() | corrected.keys()):
        if original.get(field) != corrected.get(field):
            changes.append({"field": field, "before": original.get(field),
                            "after": corrected.get(field)})
    return {"file": name, "source_method": method, "baseline": summarize(before),
            "source_grounded_preview": summarize(after), "changes": changes,
            "warning": "Offline replay; simulated preview only. Production extraction and saved benchmark remain unchanged."}


def main():
    p = argparse.ArgumentParser(description="Offline OCR grounding preview using previously saved synthetic extraction")
    p.add_argument("file", help="Synthetic scan filename, e.g. v2_18_scanned.pdf")
    p.add_argument("--manifest", type=Path, default=ROOT / "samples/evaluation_v2/ground_truth_scans_only.json")
    p.add_argument("--checkpoint", type=Path, default=ROOT / "evaluation-results/v2/paced-scans/checkpoint.json")
    args = p.parse_args()
    try:
        result = preview(args.manifest.resolve(), args.checkpoint.resolve(), args.file)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        p.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
