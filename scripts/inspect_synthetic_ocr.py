"""Print the OCR text from a single synthetic benchmark PDF for source auditing.

Never run on confidential invoices: the document text is printed to the terminal.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from backend.invoice_processing.parsing import extract_pdf


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description="Print source OCR text for one synthetic test PDF; no Groq call")
    p.add_argument("file", help="Filename from scan-only manifest, e.g., v2_18_scanned.pdf")
    p.add_argument("--manifest", type=Path, default=ROOT / "samples/evaluation_v2/ground_truth_scans_only.json")
    args=p.parse_args(argv)
    manifest=args.manifest.resolve()
    data=json.loads(manifest.read_text(encoding="utf-8"))
    if "synthetic" not in str(data.get("dataset", "")).lower():
        p.error("Only the synthetic evaluation manifest is supported")
    approved={r["file"] for r in data.get("fixtures", []) if r.get("primary_scenario") == "scanned"}
    if args.file not in approved or Path(args.file).name != args.file:
        p.error("Select one synthetic scanned fixture from the scan-only manifest")
    path=(manifest.parent / args.file).resolve()
    if not path.is_relative_to(manifest.parent.resolve()):
        p.error("Unsafe fixture path")
    text, method = extract_pdf(path)
    print(f"File: {args.file}\nParser: {method}\nCharacters: {len(text)}\n")
    print(text[:20000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
