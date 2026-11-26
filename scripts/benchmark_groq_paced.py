"""Resumable, paced evaluation on synthetic invoices without changing application records.

Run from repository root. Checkpoints contain extracted *synthetic invoice fields*;
do not use this benchmark on actual supplier documents or commit its output.
No automatic retry on provider errors; user explicitly starts each subsequent batch.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.invoice_processing.evaluation import (_field_values, _fraction, _prf,
                                                    _score_fields)
from backend.invoice_processing.extraction import GroqExtractionError, extract_invoice
from backend.invoice_processing.parsing import ParseFailure, extract_pdf
from backend.invoice_processing.schemas import InvoiceCandidate
from backend.invoice_processing.settings import load_project_env
from backend.invoice_processing.validation import issues_for, normalize_identity

STOP_ERRORS = {"GROQ_HTTP_429", "GROQ_HTTP_400", "GROQ_HTTP_401", "GROQ_HTTP_403",
               "GROQ_HTTP_404", "GROQ_HTTP_422", "GROQ_CONFIGURATION_MISSING"}
FIELDS = ("file", "status", "method", "latency_ms", "field_accuracy", "differing_fields",
          "expected_issues", "predicted_issues", "error_code")


def _atomic_json(path: Path, payload: dict) -> None:
    """Replace checkpoint atomically so a crash cannot truncate prior progress."""
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _scanned(record: dict) -> bool:
    return record.get("primary_scenario") == "scanned" or "scanned" in record["file"].lower()


def _manifest_records(manifest: Path, include_scans: bool) -> tuple[dict, list[dict]]:
    source = json.loads(manifest.read_text(encoding="utf-8"))
    fixtures = source.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("Manifest must contain a nonempty fixtures list")
    seen = set()
    selected = []
    for rec in fixtures:
        name = rec.get("file")
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError("Manifest filenames must be unique and nonempty")
        seen.add(name)
        path = (manifest.parent / name).resolve()
        if not path.is_relative_to(manifest.parent.resolve()):
            raise ValueError("Unsafe fixture path")
        if include_scans or not _scanned(rec):
            selected.append(rec)
    if not selected:
        raise ValueError("No eligible fixtures")
    return source, selected


def _report(records: list[dict], results: dict, mode: str) -> tuple[dict, list[dict]]:
    """Aggregate from checkpoint in manifest order, including duplicate chronology.

    Success-only metrics are named explicitly; failed or pending invoices are
    never silently treated as correct or added to those denominators.
    """
    totals = {"correct": 0, "compared": 0, "tp": 0, "fp": 0, "fn": 0}
    issue_tp = issue_fp = issue_fn = dup_tp = dup_fp = dup_fn = 0
    identities = set()
    scored = []
    per_invoice = []
    for rec in records:
        name = rec["file"]
        prior = results.get(name)
        if prior is None:
            per_invoice.append({"file": name, "status": "pending"})
            continue
        if prior.get("status") != "ok":
            per_invoice.append({"file": name, "status": "failed",
                                "error_code": prior.get("error_code", "UNKNOWN")})
            continue
        expected = InvoiceCandidate.model_validate(rec["expected"])
        actual = InvoiceCandidate.model_validate(prior["candidate"])
        s = _score_fields(expected, actual)
        truth, pred = _field_values(expected), _field_values(actual)
        differing = sorted(f for f in truth.keys() | pred.keys() if truth.get(f) != pred.get(f))
        for key in totals:
            totals[key] += s[key]
        identity = (normalize_identity(actual.supplier_name), normalize_identity(actual.invoice_number))
        is_dup = bool(identity[0] and identity[1] and identity in identities)
        if identity[0] and identity[1]:
            identities.add(identity)
        expected_codes = set(rec.get("expected_issue_codes", []))
        predicted_codes = {issue.code for issue in issues_for(actual, duplicate=is_dup)}
        issue_tp += len(predicted_codes & expected_codes)
        issue_fp += len(predicted_codes - expected_codes)
        issue_fn += len(expected_codes - predicted_codes)
        exp_dup = "POSSIBLE_DUPLICATE" in expected_codes
        pred_dup = "POSSIBLE_DUPLICATE" in predicted_codes
        dup_tp += int(exp_dup and pred_dup)
        dup_fp += int(not exp_dup and pred_dup)
        dup_fn += int(exp_dup and not pred_dup)
        detail = {"file": name, "status": "ok", "method": prior["method"],
                  "latency_ms": prior["latency_ms"], "field_accuracy": _fraction(s["correct"], s["compared"]),
                  "differing_fields": differing, "expected_issues": sorted(expected_codes),
                  "predicted_issues": sorted(predicted_codes)}
        scored.append(detail)
        per_invoice.append(detail)
    times = sorted(float(x["latency_ms"]) for x in scored)
    idx = max(0, int(len(times) * .95 + .999999) - 1)
    failures = sum(r["status"] == "failed" for r in per_invoice)
    pending = sum(r["status"] == "pending" for r in per_invoice)
    summary = {
        "mode": mode, "eligible_count": len(records), "scored_count": len(scored),
        "failure_count": failures, "pending_count": pending,
        "attempted_count": len(scored) + failures,
        "attempted_coverage_pct": _fraction((len(scored) + failures) * 100, len(records)),
        "successful_coverage_pct": _fraction(len(scored) * 100, len(records)),
        "benchmark_complete": pending == 0 and failures == 0,
        "field_accuracy_on_successes": _fraction(totals["correct"], totals["compared"]),
        "field_precision_recall_f1_on_successes": _prf(totals["tp"], totals["fp"], totals["fn"]),
        "issue_detection_f1_on_successes": _prf(issue_tp, issue_fp, issue_fn),
        "duplicate_detection_f1_on_successes": _prf(dup_tp, dup_fp, dup_fn),
        "latency_ms_on_successes": {"median": round(median(times), 2) if times else None,
                                    "p95": times[idx] if times else None},
        "limitations": ("Synthetic, generated-not-independently-reviewed ground truth; "
                        "metrics exclude failed and pending documents. "
                        "Duplicate detection depends on successful processing of prior invoices."),
    }
    return summary, per_invoice


def run(*, manifest: Path, mode: str, include_scans: bool, max_documents: int,
        gap_seconds: float, output_dir: Path, retry_failures: bool = False,
        parser: Callable = extract_pdf, extractor: Callable = extract_invoice,
        sleeper: Callable = time.sleep) -> dict:
    if mode not in ("groq", "hybrid"):
        raise ValueError("Mode must be groq or hybrid")
    if not 1 <= max_documents <= 10 or not 0 <= gap_seconds <= 120:
        raise ValueError("max-documents must be 1..10 and gap-seconds must be 0..120")
    manifest = manifest.resolve()
    _source, records = _manifest_records(manifest, include_scans)
    fingerprint = hashlib.sha256(manifest.read_bytes()).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_file = output_dir / "checkpoint.json"
    if checkpoint_file.exists():
        checkpoint = json.loads(checkpoint_file.read_text(encoding="utf-8"))
        if (checkpoint.get("manifest_sha256") != fingerprint
                or checkpoint.get("mode") != mode
                or checkpoint.get("include_scans") != include_scans):
            raise ValueError("Existing checkpoint belongs to a different manifest/mode/scan selection")
        results = checkpoint.get("results", {})
        if not isinstance(results, dict):
            raise ValueError("Malformed checkpoint")
    else:
        results = {}
        checkpoint = {"format": 1, "manifest_sha256": fingerprint, "mode": mode,
                      "include_scans": include_scans, "results": results}
    attempted_this_run = 0
    new_successes = 0
    stop_code = None
    events = []
    for rec in records:
        name = rec["file"]
        old = results.get(name)
        if old and (old["status"] == "ok" or not retry_failures):
            continue
        if attempted_this_run >= max_documents:
            break
        if attempted_this_run:
            sleeper(gap_seconds)
        path = (manifest.parent / name).resolve()
        start = perf_counter()
        attempted_this_run += 1
        try:
            if not path.is_file():
                raise ParseFailure("FIXTURE_NOT_FOUND")
            text, parser_name = parser(path)
            candidate, extraction_name = extractor(text, mode)
            # Validate against the schema before putting candidate in a checkpoint.
            candidate = InvoiceCandidate.model_validate(candidate)
            results[name] = {"status": "ok", "candidate": candidate.model_dump(mode="json"),
                             "method": f"{parser_name}+{extraction_name}",
                             "latency_ms": round((perf_counter() - start) * 1000, 2)}
            new_successes += 1
            events.append({"file": name, "status": "ok"})
        except (ParseFailure, GroqExtractionError, ValueError, OSError) as exc:
            code = str(exc) if isinstance(exc, (ParseFailure, GroqExtractionError)) else type(exc).__name__.upper()
            results[name] = {"status": "failed", "error_code": code}
            events.append({"file": name, "status": "failed", "error_code": code})
            if code in STOP_ERRORS or code.startswith("GROQ_HTTP_5") or code in ("GROQ_TIMEOUT", "GROQ_NETWORK_ERROR"):
                stop_code = code
        # Persist after every file, including errors; no invoice database mutations.
        _atomic_json(checkpoint_file, checkpoint)
        if stop_code:
            break
    summary, rows = _report(records, results, mode)
    summary.update({"attempted_this_run": attempted_this_run, "successes_this_run": new_successes,
                    "stopped_on_error": stop_code, "recent_events": events})
    _atomic_json(output_dir / "summary.json", summary)
    with (output_dir / "per-invoice.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            cleaned = dict(row)
            for attr in ("differing_fields", "expected_issues", "predicted_issues"):
                cleaned[attr] = ";".join(row.get(attr, []))
            writer.writerow(cleaned)
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Resumable Groq benchmark using synthetic invoices")
    p.add_argument("--manifest", type=Path, default=ROOT / "samples/evaluation_v2/ground_truth.json")
    p.add_argument("--mode", choices=("groq", "hybrid"), default="groq")
    p.add_argument("--include-scans", action="store_true")
    p.add_argument("--allow-external", action="store_true")
    p.add_argument("--max-documents", type=int, default=8)
    p.add_argument("--gap-seconds", type=float, default=6.0)
    p.add_argument("--retry-failures", action="store_true", help="Explicitly reattempt previous failures once per invocation")
    p.add_argument("--output-dir", type=Path, default=None)
    args = p.parse_args(argv)
    if not args.allow_external:
        p.error("--allow-external is required; synthetic invoice text is sent to Groq")
    output = args.output_dir or ROOT / "evaluation-results/v2" / f"paced-{args.mode}"
    load_project_env()
    try:
        result = run(manifest=args.manifest, mode=args.mode, include_scans=args.include_scans,
                     max_documents=args.max_documents, gap_seconds=args.gap_seconds,
                     output_dir=output, retry_failures=args.retry_failures)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        p.error(f"Benchmark setup error: {type(exc).__name__}")
    print(json.dumps(result, indent=2))
    print(f"Saved: {output / 'checkpoint.json'}, {output / 'summary.json'}, {output / 'per-invoice.csv'}")
    return 2 if result["stopped_on_error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
