"""Audit completed, synthetic scanned-invoice extraction without new Groq calls.

Reads the exact checkpoints written by scripts/benchmark_groq_paced.py.
Does not modify production invoices or database records.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.invoice_processing.evaluation import _field_values, _score_fields
from backend.invoice_processing.schemas import InvoiceCandidate


def field_group(field: str) -> str:
    parts = field.split(".")
    if len(parts) == 3 and parts[0] == "line_items" and parts[1].isdigit():
        return "line_items.*." + parts[2]
    return field


def scan_audit(manifest: Path, checkpoint_file: Path, *, show_values: bool = False) -> dict:
    manifest = manifest.resolve()
    source = json.loads(manifest.read_text(encoding="utf-8"))
    if "synthetic" not in str(source.get("dataset", "")).lower():
        raise ValueError("This audit only accepts synthetic evaluation manifests")
    fixtures = source.get("fixtures", [])
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("No labeled fixtures")
    if any(f.get("primary_scenario") != "scanned" for f in fixtures):
        raise ValueError("Use the scan-only ground_truth_scans_only.json manifest")
    checkpoint = json.loads(checkpoint_file.resolve().read_text(encoding="utf-8"))
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if checkpoint.get("manifest_sha256") != digest:
        raise ValueError("Checkpoint and manifest do not match; do not mix benchmark runs")
    if checkpoint.get("mode") != "groq" or checkpoint.get("include_scans") is not True:
        raise ValueError("Expected completed Groq scan checkpoint")
    results = checkpoint.get("results", {})
    by_group = Counter()
    docs_by_group = defaultdict(set)
    per_file = []
    good = 0
    failures = 0
    pending = 0
    counts = Counter()
    for rec in fixtures:
        name = rec["file"]
        result = results.get(name)
        if result is None:
            pending += 1
            per_file.append({"file": name, "status": "pending"})
            continue
        if result.get("status") != "ok":
            failures += 1
            per_file.append({"file": name, "status": "failed", "error_code": result.get("error_code", "UNKNOWN")})
            continue
        exp = InvoiceCandidate.model_validate(rec["expected"])
        act = InvoiceCandidate.model_validate(result["candidate"])
        expected = _field_values(exp)
        actual = _field_values(act)
        scores = _score_fields(exp, act)
        mismatches = []
        for key in sorted(expected.keys() | actual.keys()):
            if expected.get(key) == actual.get(key):
                continue
            group = field_group(key)
            by_group[group] += 1
            docs_by_group[group].add(name)
            detail = {"field": key, "group": group}
            if show_values:
                detail["expected"] = expected.get(key)
                detail["extracted"] = actual.get(key)
            mismatches.append(detail)
        counts["compared"] += scores["compared"]
        counts["correct"] += scores["correct"]
        good += 1
        per_file.append({"file": name, "status": "ok", "layout": rec.get("layout"),
                         "field_accuracy": round(scores["correct"] / scores["compared"], 4),
                         "mismatches": mismatches})
    groups = [{"field": key, "mismatched_occurrences": value,
               "affected_invoices": len(docs_by_group[key])}
              for key, value in by_group.most_common()]
    return {"source": "synthetic scan checkpoint, no external calls", "total": len(fixtures),
            "scored": good, "failures": failures, "pending": pending,
            "field_accuracy": round(counts["correct"] / counts["compared"], 4) if counts["compared"] else None,
            "mismatch_groups": groups, "per_invoice": per_file,
            "limitations": "Generated synthetic labels are not independently reviewed. Field-level exact matching may penalize harmless wording differences in descriptions."}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Analyze all scan mismatches from an existing completed Groq checkpoint without spending API credits")
    p.add_argument("--manifest", type=Path, default=ROOT / "samples/evaluation_v2/ground_truth_scans_only.json")
    p.add_argument("--checkpoint", type=Path, default=ROOT / "evaluation-results/v2/paced-scans/checkpoint.json")
    p.add_argument("--show-values", action="store_true", help="Show ground-truth and extracted values; use only on synthetic test invoices")
    p.add_argument("--output", type=Path, default=None, help="Optional JSON report path")
    args = p.parse_args(argv)
    try:
        report = scan_audit(args.manifest, args.checkpoint, show_values=args.show_values)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        p.error(f"Audit input error: {exc}")
    print(f"Synthetic scanned invoices: {report['scored']}/{report['total']} scored; "
          f"{report['failures']} failed; {report['pending']} pending")
    print(f"Field accuracy: {report['field_accuracy']}")
    print("\nMismatch groups (field occurrences / invoices affected):")
    for row in report["mismatch_groups"]:
        print(f"  {row['field']:<30} {row['mismatched_occurrences']:>3} / {row['affected_invoices']:>2}")
    print("\nPer-invoice mismatches:")
    for rec in report["per_invoice"]:
        print(f"  {rec['file']} [{rec['status']}]" + (f" accuracy={rec['field_accuracy']}" if 'field_accuracy' in rec else ""))
        for mismatch in rec.get("mismatches", []):
            print(f"    {mismatch['field']}")
            if args.show_values:
                print(f"      expected: {mismatch['expected']!r}")
                print(f"      extracted: {mismatch['extracted']!r}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nSaved {args.output}")
    return 0 if report["failures"] == report["pending"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
