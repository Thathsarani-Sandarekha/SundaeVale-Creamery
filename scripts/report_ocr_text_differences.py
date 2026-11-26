"""Offline scanned-text error taxonomy from a saved benchmark checkpoint.

The original exact-match evaluation is unchanged. This report only categorizes
supplier/description errors as whitespace-only vs substantive; all invoice
identifiers, dates and financial values remain strictly evaluated elsewhere.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _collapse_spaces(value: str | None) -> str:
    return "".join((value or "").split())


def compare_text_fields(manifest: dict, checkpoint: dict) -> dict:
    records = manifest.get("fixtures", [])
    results = checkpoint.get("results", {})
    outcome = Counter()
    per_invoice = []
    for record in records:
        name = record["file"]
        if record.get("primary_scenario") != "scanned":
            continue
        result = results.get(name, {})
        if result.get("status") != "ok":
            outcome["unprocessed"] += 1
            continue
        expected = record["expected"]
        actual = result["candidate"]
        comparisons = [("supplier_name", expected.get("supplier_name"), actual.get("supplier_name"))]
        expected_lines = expected.get("line_items", [])
        actual_lines = actual.get("line_items", [])
        for idx, exp in enumerate(expected_lines):
            got = actual_lines[idx] if idx < len(actual_lines) else {}
            comparisons.append((f"line_items.{idx}.description", exp.get("description"), got.get("description")))
        invoice_issues = []
        for field, want, got in comparisons:
            if want == got:
                category = "exact"
            elif want is not None and got is not None and _collapse_spaces(want) == _collapse_spaces(got):
                category = "whitespace_only"
            else:
                category = "substantive_or_missing"
            outcome[category] += 1
            if category != "exact":
                invoice_issues.append({"field": field, "category": category,
                                       "expected": want, "extracted": got})
        per_invoice.append({"file": name, "issues": invoice_issues})
    return {"scored_scans": len(per_invoice), "counts": dict(outcome), "per_invoice": per_invoice,
            "warning": "Supplemental diagnostic only: do not replace strict field-accuracy metrics with this report."}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Classify scanned-text mismatches without API calls")
    parser.add_argument("--manifest", type=Path, default=Path("samples/evaluation_v2/ground_truth.json"))
    parser.add_argument("--checkpoint", type=Path, default=Path("evaluation-results/v2/paced-scans-geometry200/checkpoint.json"))
    parser.add_argument("--show-values", action="store_true", help="Display synthetic expected/extracted values")
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    checkpoint = json.loads(args.checkpoint.read_text(encoding="utf-8"))
    report = compare_text_fields(manifest, checkpoint)
    print(f"Scanned documents scored: {report['scored_scans']}")
    for key in ("exact", "whitespace_only", "substantive_or_missing", "unprocessed"):
        print(f"  {key}: {report['counts'].get(key, 0)}")
    for document in report["per_invoice"]:
        if not document["issues"]:
            continue
        print(f"{document['file']}:")
        for issue in document["issues"]:
            message = f"  {issue['field']}: {issue['category']}"
            if args.show_values:
                message += f"  expected={issue['expected']!r} extracted={issue['extracted']!r}"
            print(message)
    print(report["warning"])


if __name__ == "__main__":
    main()
