"""Repeatable, local invoice-field evaluation against synthetic ground-truth fixtures.

Metrics are computed only on successful documents. Failed/OCR-unavailable documents
are listed as skipped with reasons and not silently counted as correct/incorrect.
Scores on this tiny fixed-layout fixture set do NOT indicate real-world accuracy.
"""
from __future__ import annotations
import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from decimal import Decimal
from statistics import median
from time import perf_counter
from typing import Any

from .extraction import extract_invoice, GroqExtractionError
from .parsing import extract_pdf, ParseFailure
from .schemas import InvoiceCandidate
from .settings import load_project_env
from .validation import issues_for, normalize_identity

FIELDS = ("supplier_name", "invoice_number", "invoice_date", "due_date", "currency",
          "subtotal", "tax", "shipping", "discount", "total")
LINE_FIELDS = ("description", "quantity", "unit_price", "line_total")


def _value(value: Any) -> str | None:
    """Canonicalize typed values before scoring exact field matches.

    Pydantic stores money and quantities as Decimal, so differences in decimal
    scale (180 vs 180.0 vs 180.00) are NOT extraction errors. Dates and
    identifiers remain exact comparisons and must not be silently altered.
    """
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    return str(value).strip()


def _field_values(candidate: InvoiceCandidate) -> dict[str, str | None]:
    # Keep native Decimal objects so equivalent numbers can be normalized.
    # mode="json" would stringify them before _value sees their type.
    payload = candidate.model_dump(mode="python")
    fields = {field: _value(payload.get(field)) for field in FIELDS}
    for idx, item in enumerate(payload["line_items"]):
        for field in LINE_FIELDS:
            fields[f"line_items.{idx}.{field}"] = _value(item.get(field))
    return fields


def _score_fields(expected: InvoiceCandidate, actual: InvoiceCandidate) -> dict[str, int]:
    truth, pred = _field_values(expected), _field_values(actual)
    scores = {"correct":0,"compared":0,"tp":0,"fp":0,"fn":0}
    for field in truth.keys() | pred.keys():
        target, output = truth.get(field), pred.get(field)
        scores["compared"] += 1
        if target == output:
            scores["correct"] += 1
            if target is not None:
                scores["tp"] += 1
        else:
            if output is not None:
                scores["fp"] += 1
            if target is not None:
                scores["fn"] += 1
    return scores


def _fraction(numerator: int, denominator: int) -> float | None:
    return round(numerator/denominator,4) if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    return round(2*precision*recall/(precision+recall),4) if precision+recall else 0.0


def _prf(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    p = _fraction(tp,tp+fp)
    r = _fraction(tp,tp+fn)
    return {"precision":p,"recall":r,"f1":_f1(p,r)}


def evaluate(manifest: Path, *, mode: str = "rules", include_scans: bool = False,
             allow_external: bool = False, parser=extract_pdf, extractor=extract_invoice) -> dict:
    if mode not in ("rules", "groq", "hybrid"):
        raise ValueError("mode must be rules, groq, or hybrid")
    if mode in ("groq", "hybrid") and not allow_external:
        raise ValueError("Groq/hybrid evaluation requires --allow-external (synthetic documents only)")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not data.get("fixtures"):
        raise ValueError("No fixtures in manifest")
    totals = {"correct":0,"compared":0,"tp":0,"fp":0,"fn":0}
    issue_tp=issue_fp=issue_fn=0
    duplicate_tp=duplicate_fp=duplicate_fn=0
    scored=[]; skipped=[]; elapsed=[]; identities=set()
    for record in data["fixtures"]:
        path = manifest.parent / record["file"]
        if not include_scans and "scanned" in path.name:
            skipped.append({"file":path.name,"reason":"scan_excluded_add_--include-scans"})
            continue
        t0=perf_counter()
        try:
            if not path.is_file():
                raise ParseFailure("FIXTURE_NOT_FOUND")
            text, parser_method = parser(path)
            actual, extraction_method = extractor(text, mode)
            expected = InvoiceCandidate.model_validate(record["expected"])
        except (ParseFailure, GroqExtractionError, ValueError, OSError) as exc:
            # Distinguish provider HTTP 429/400/5xx, timeout, and invalid output
            # without writing confidential request or document content.
            if isinstance(exc, (ParseFailure, GroqExtractionError)):
                error_code = str(exc)
            else:
                error_code = type(exc).__name__.upper()
            skipped.append({"file":path.name,"reason":error_code})
            continue
        elapsed_ms=round((perf_counter()-t0)*1000,2)
        elapsed.append(elapsed_ms)
        s=_score_fields(expected,actual)
        expected_values, actual_values = _field_values(expected), _field_values(actual)
        differing_fields = sorted(field for field in expected_values.keys() | actual_values.keys()
                                  if expected_values.get(field) != actual_values.get(field))
        for key in totals:
            totals[key]+=s[key]
        key=(normalize_identity(actual.supplier_name), normalize_identity(actual.invoice_number))
        is_duplicate=bool(key[0] and key[1] and key in identities)
        if key[0] and key[1]:
            identities.add(key)
        predicted_codes=set(issue.code for issue in issues_for(actual,duplicate=is_duplicate))
        expected_codes=set(record.get("expected_issue_codes",[]))
        issue_tp+=len(predicted_codes & expected_codes)
        issue_fp+=len(predicted_codes-expected_codes)
        issue_fn+=len(expected_codes-predicted_codes)
        expected_dup="POSSIBLE_DUPLICATE" in expected_codes
        predicted_dup="POSSIBLE_DUPLICATE" in predicted_codes
        duplicate_tp+=int(expected_dup and predicted_dup)
        duplicate_fp+=int(not expected_dup and predicted_dup)
        duplicate_fn+=int(expected_dup and not predicted_dup)
        scored.append({
            "file":path.name,"method":f"{parser_method}+{extraction_method}",
            "latency_ms":elapsed_ms,
            "field_accuracy":_fraction(s["correct"],s["compared"]),
            "differing_fields":differing_fields,
            "field_tp":s["tp"],"field_fp":s["fp"],"field_fn":s["fn"],
            "expected_issues":sorted(expected_codes),"predicted_issues":sorted(predicted_codes),
        })
    latencies=sorted(elapsed)
    p95=(latencies[max(0,int(len(latencies)*.95+.999999)-1)] if latencies else None)
    reason_counts = dict(sorted(Counter(row["reason"] for row in skipped).items()))
    excluded_scans = reason_counts.get("scan_excluded_add_--include-scans", 0)
    attempted_count = len(data["fixtures"]) - excluded_scans
    nonexcluded_failures = len(skipped) - excluded_scans
    return {
        "dataset":data.get("dataset","synthetic invoices"),"mode":mode,
        # Overall coverage includes intentionally excluded scans; attempted coverage
        # only considers documents requested for this run.
        "coverage_pct": _fraction(len(scored) * 100, len(data["fixtures"])),
        "attempted_count": attempted_count,
        "attempted_coverage_pct": _fraction(len(scored) * 100, attempted_count),
        "failure_count": nonexcluded_failures,
        "skipped_reason_counts": reason_counts,
        "evaluation_complete": nonexcluded_failures == 0 and len(scored) == attempted_count,
        "limitations":"Tiny, synthetic, mostly regular invoice layouts. Groq/Docling not verified unless exercised live. No production accuracy claim.",
        "total_fixtures":len(data["fixtures"]),"scored_count":len(scored),"skipped_count":len(skipped),
        "field_accuracy":_fraction(totals["correct"],totals["compared"]),
        "field_precision_recall_f1":_prf(totals["tp"],totals["fp"],totals["fn"]),
        "issue_detection_precision_recall_f1":_prf(issue_tp,issue_fp,issue_fn),
        "duplicate_detection_precision_recall_f1":_prf(duplicate_tp,duplicate_fp,duplicate_fn),
        "latency_ms":{"median":round(median(elapsed),2) if elapsed else None,"p95":p95},
        "per_document":scored,"skipped":skipped,
    }


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description="Evaluate invoice extraction on labeled, fictional PDFs")
    p.add_argument("--manifest",type=Path,default=Path("samples/invoices/ground_truth.json"))
    p.add_argument("--mode",choices=("rules","groq","hybrid"),default="rules")
    p.add_argument("--include-scans",action="store_true")
    p.add_argument("--allow-external",action="store_true",help="Allow sending extracted fictional text to Groq")
    p.add_argument("--output-dir",type=Path,default=Path("evaluation-results"))
    args=p.parse_args(argv)
    load_project_env()
    try:
        result=evaluate(args.manifest,mode=args.mode,include_scans=args.include_scans,allow_external=args.allow_external)
    except (ValueError,OSError,json.JSONDecodeError) as exc:
        p.error(str(exc))
    args.output_dir.mkdir(parents=True,exist_ok=True)
    dest=args.output_dir/(args.mode+"-evaluation.json")
    dest.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    rows=result["per_document"]
    csv_path=args.output_dir/(args.mode+"-per-invoice.csv")
    with csv_path.open("w",encoding="utf-8",newline="") as out:
        writer=csv.DictWriter(out,fieldnames=["file","method","latency_ms","field_accuracy","field_tp","field_fp","field_fn","differing_fields","expected_issues","predicted_issues"])
        writer.writeheader()
        for row in rows:
            writer.writerow({**row,"differing_fields":";".join(row["differing_fields"]),"expected_issues":";".join(row["expected_issues"]),"predicted_issues":";".join(row["predicted_issues"])})
    print(json.dumps({k:result[k] for k in ("mode","scored_count","skipped_count","coverage_pct","attempted_count","attempted_coverage_pct","failure_count","skipped_reason_counts","evaluation_complete","field_accuracy","field_precision_recall_f1","issue_detection_precision_recall_f1","duplicate_detection_precision_recall_f1","latency_ms")},indent=2))
    print(f"Saved: {dest} and {csv_path}")
    return 0 if result["scored_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
