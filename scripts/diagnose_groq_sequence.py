"""Bounded, privacy-safe Groq batch probe for *synthetic* SundaeVale fixtures.

Does not mutate invoice jobs, approvals, or the application's database.
Stops immediately on 429 and non-retryable authentication/request errors.
This is a diagnostic, not the final full-set evaluator.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.invoice_processing.evaluation import _field_values, _score_fields
from backend.invoice_processing.extraction import GroqExtractionError, extract_groq
from backend.invoice_processing.parsing import ParseFailure, extract_pdf
from backend.invoice_processing.schemas import InvoiceCandidate
from backend.invoice_processing.settings import load_project_env

STOP_CODES = {"GROQ_HTTP_400", "GROQ_HTTP_401", "GROQ_HTTP_403", "GROQ_HTTP_404", "GROQ_HTTP_422", "GROQ_HTTP_429", "GROQ_CONFIGURATION_MISSING"}


def diagnose(*, manifest: Path, limit: int, offset: int, gap_seconds: float,
             parser=extract_pdf, extractor=extract_groq, sleeper=time.sleep) -> dict:
    """Attempt only up to `limit` digital documents. No automatic retries."""
    if not (1 <= limit <= 10):
        raise ValueError("--limit must be between 1 and 10")
    if offset < 0 or not (0 <= gap_seconds <= 120):
        raise ValueError("--offset must be nonnegative and --gap-seconds between 0 and 120")
    manifest = manifest.resolve()
    source = json.loads(manifest.read_text(encoding="utf-8"))
    choices = [rec for rec in source["fixtures"] if rec.get("primary_scenario") != "scanned"]
    selection = choices[offset:offset + limit]
    if not selection:
        raise ValueError("No digital invoices in requested slice")
    rows = []
    for i, record in enumerate(selection):
        # Verify fixture path remains inside the synthetic dataset directory.
        path = (manifest.parent / record["file"]).resolve()
        if not path.is_relative_to(manifest.parent):
            raise ValueError("Unsafe fixture path")
        if i:
            sleeper(gap_seconds)
        t0 = perf_counter()
        try:
            text, parser_method = parser(path)
            candidate = extractor(text)
            expected = InvoiceCandidate.model_validate(record["expected"])
            actual_fields = _field_values(candidate)
            expected_fields = _field_values(expected)
            row = {
                "file": path.name,
                "status": "ok",
                "method": parser_method + "+groq-structured",
                "field_accuracy": round(_score_fields(expected, candidate)["correct"] / _score_fields(expected, candidate)["compared"], 4),
                "differing_fields": sorted(f for f in expected_fields.keys() | actual_fields.keys() if expected_fields.get(f) != actual_fields.get(f)),
                "elapsed_ms": round((perf_counter() - t0) * 1000, 2),
            }
        except (ParseFailure, GroqExtractionError) as exc:
            code = str(exc)  # These exception classes carry only defined safe codes.
            row = {"file": path.name, "status": "failed", "error_code": code,
                   "elapsed_ms": round((perf_counter() - t0) * 1000, 2)}
        except Exception as exc:
            # Avoid logging provider response bodies, document text or secrets.
            row = {"file": path.name, "status": "failed", "error_code": type(exc).__name__.upper(),
                   "elapsed_ms": round((perf_counter() - t0) * 1000, 2)}
        rows.append(row)
        if row.get("error_code") in STOP_CODES:
            break
    return {
        "purpose": "bounded synthetic Groq diagnostic; not a full benchmark",
        "selected_count": len(selection),
        "attempted_count": len(rows),
        "success_count": sum(r["status"] == "ok" for r in rows),
        "failure_count": sum(r["status"] == "failed" for r in rows),
        "stopped_early": len(rows) < len(selection),
        "results": rows,
        "note": "No invoice records modified. No automatic retries. Remaining unattempted files are not included in accuracy.",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bounded Groq diagnostic for synthetic SundaeVale PDFs")
    p.add_argument("--manifest", type=Path, default=ROOT / "samples/evaluation_v2/ground_truth.json")
    p.add_argument("--allow-external", action="store_true", help="Explicitly authorize sending synthetic PDF text to Groq")
    p.add_argument("--limit", type=int, default=5, help="Attempt no more than this many documents (max 10)")
    p.add_argument("--offset", type=int, default=0, help="Start at this index among digital fixtures")
    p.add_argument("--gap-seconds", type=float, default=4.0, help="Minimum delay between calls")
    p.add_argument("--output", type=Path, default=ROOT / "evaluation-results/diagnostics/groq-sequence.json")
    args = p.parse_args(argv)
    if not args.allow_external:
        p.error("--allow-external required: synthetic invoice text will be sent to Groq")
    load_project_env()
    try:
        report = diagnose(manifest=args.manifest, limit=args.limit, offset=args.offset,
                          gap_seconds=args.gap_seconds)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        # Don't emit contents of documents, environment variables, or provider responses.
        p.error(type(exc).__name__)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Saved: {args.output}")
    return 0 if report["failure_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
