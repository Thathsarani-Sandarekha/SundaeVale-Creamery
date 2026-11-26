"""One-job worker; never process PDFs inside an upload request."""
from __future__ import annotations
import argparse
import os
import time
from .logging_config import configure_logging, log_event
from sqlalchemy import select
from pydantic import ValidationError

from .extraction import GroqExtractionError, extract_invoice
from .parsing import ParseFailure, extract_pdf
from .store import InvoiceDocument, InvoiceStore
from .validation import issues_for, normalize_identity
from .schemas import Issue


def process_one(store: InvoiceStore, *, parser=extract_pdf, extractor=extract_invoice, mode: str | None = None) -> str | None:
    mode = mode or os.getenv("INVOICE_EXTRACTION_MODE", "rules")
    with store.Session.begin() as db:
        job = db.scalar(select(InvoiceDocument).where(InvoiceDocument.status == "queued")
                        .order_by(InvoiceDocument.created_at, InvoiceDocument.id).limit(1))
        if job is None:
            return None
        job.status = "processing"
        job_id = job.id
        reference = job.original_path
        store.audit(db, job_id, "processing_started")
    started = time.perf_counter()
    try:
        with store.storage.materialize(reference) as file_path:
            text, parser_method = parser(file_path)
        extraction, model_method = extractor(text, mode)
        with store.Session.begin() as db:
            job = db.get(InvoiceDocument, job_id)
            supplier = normalize_identity(extraction.supplier_name)
            number = normalize_identity(extraction.invoice_number)
            duplicate = store.duplicate_exists(db, supplier, number, job_id)
            flags = issues_for(extraction, duplicate=duplicate)
            # OCR/LLM-derived values must be verified against the original PDF.
            # This workflow flag is cleared by an explicit review save, not by AI.
            if parser_method.startswith("docling-ocr") or model_method != "rules-baseline":
                flags.append(Issue(code="VERIFY_EXTRACTION", severity="review",
                                   field="extraction", message="Check OCR/AI extracted fields against the original PDF and save review before approval."))
            job.status = "review_required" if flags else "processed"
            job.extraction_method = f"{parser_method}+{model_method}"
            job.extraction_json = extraction.model_dump_json()
            job.issues_json = "[" + ",".join(flag.model_dump_json() for flag in flags) + "]"
            job.supplier_key = supplier
            job.invoice_key = number
            job.error_code = None
            store.audit(db, job_id, "processed", {"issue_codes": [f.code for f in flags], "mode": job.extraction_method})
        log_event("invoice_processed", job_id, elapsed_ms=round((time.perf_counter()-started)*1000),
                  method=f"{parser_method}+{model_method}", issue_count=len(flags))
    except (ParseFailure, GroqExtractionError, ValidationError, ValueError) as exc:
        safe_code = str(exc) if isinstance(exc, ParseFailure) else type(exc).__name__.upper()
        with store.Session.begin() as db:
            job = db.get(InvoiceDocument, job_id)
            job.status = "failed"
            job.error_code = safe_code[:60]
            details = {"error_code": job.error_code}
            # Keep validation diagnostics useful without storing supplier names,
            # raw invoice text, extracted values, or model provider responses.
            if isinstance(exc, ValidationError):
                details["validation_fields"] = sorted({
                    ".".join(map(str, error["loc"])) for error in exc.errors()
                })
                details["validation_types"] = sorted({
                    error["type"] for error in exc.errors()
                })
            store.audit(db, job_id, "processing_failed", details)
        log_event("invoice_processing_failed", job_id, error_code=type(exc).__name__, elapsed_ms=round((time.perf_counter()-started)*1000))
    except Exception:
        with store.Session.begin() as db:
            job = db.get(InvoiceDocument, job_id)
            job.status = "failed"
            job.error_code = "UNEXPECTED_PROCESSING_ERROR"
            store.audit(db, job_id, "processing_failed", {"error_code": job.error_code})
        log_event("invoice_processing_failed", job_id, error_code="UNEXPECTED_PROCESSING_ERROR", elapsed_ms=round((time.perf_counter()-started)*1000))
    return job_id


def main():
    p = argparse.ArgumentParser(description="SundaeVale background invoice worker")
    p.add_argument("--all", action="store_true", help="Process all currently queued jobs")
    p.add_argument("--watch", action="store_true", help="Continuously poll queued jobs (one worker replica only)")
    p.add_argument("--poll-seconds", type=float, default=3.0)
    args = p.parse_args()
    if args.poll_seconds < 1 or args.poll_seconds > 60:
        p.error("--poll-seconds must be between 1 and 60")
    configure_logging()
    from .settings import load_project_env
    load_project_env()
    store = InvoiceStore()
    jobs = 0
    while True:
        job_id = process_one(store)
        if job_id is None:
            if args.watch:
                time.sleep(args.poll_seconds)
                continue
            break
        jobs += 1
        print(f"Processed job {job_id}")
        if not (args.all or args.watch):
            break
    print(f"Processed {jobs} job(s)")


if __name__ == "__main__":
    main()
