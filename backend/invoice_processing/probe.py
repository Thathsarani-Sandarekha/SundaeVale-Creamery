"""One-file non-mutating PDF/OCR/Groq smoke test. Never touches the invoice database."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from time import perf_counter

from .extraction import GroqExtractionError, extract_invoice
from .parsing import ParseFailure, extract_pdf
from .settings import load_project_env
from .validation import issues_for


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Inspect one synthetic invoice without changing the database")
    cli.add_argument("pdf", type=Path)
    cli.add_argument("--mode", choices=("rules", "groq", "hybrid"), default="rules")
    cli.add_argument("--allow-external", action="store_true", help="Explicitly allow Groq to receive the extracted PDF text")
    args = cli.parse_args(argv)
    load_project_env()
    if args.mode in ("groq", "hybrid") and not args.allow_external:
        cli.error("--mode groq/hybrid may send document text to Groq; add --allow-external for synthetic data only")
    if not args.pdf.is_file():
        cli.error("PDF path not found")
    started = perf_counter()
    try:
        text, parser = extract_pdf(args.pdf)
        candidate, extractor = extract_invoice(text, args.mode)
    except (ParseFailure, GroqExtractionError, ValueError) as exc:
        # Do not print model responses or document text on failure.
        print(json.dumps({"status":"failed","error_code":str(exc) if isinstance(exc,(ParseFailure,GroqExtractionError)) else type(exc).__name__.upper()}))
        return 2
    issues = issues_for(candidate)
    print(json.dumps({
        "status":"ok", "file":args.pdf.name,
        "method":f"{parser}+{extractor}",
        "elapsed_ms":round((perf_counter()-started)*1000,1),
        "extraction":candidate.model_dump(mode="json"),
        "issue_codes":[issue.code for issue in issues],
        "note":"Smoke test only: no invoice was saved, approved, or modified",
    },indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
