"""Print candidate OCR geometry evidence from ONE synthetic invoice; no Groq calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from backend.invoice_processing.ocr_geometry_evidence import extract_geometry_rows


def main(argv=None):
    p = argparse.ArgumentParser(description='Local 200 DPI OCR evidence preview (synthetic PDFs only)')
    p.add_argument('pdf', type=Path)
    p.add_argument('--allow-synthetic', action='store_true')
    a = p.parse_args(argv)
    if not a.allow_synthetic or not (a.pdf.name.startswith('v2_') and a.pdf.name.endswith('_scanned.pdf')):
        p.error('Only synthetic v2_*_scanned.pdf files are permitted with --allow-synthetic')
    if not a.pdf.is_file():
        p.error('PDF does not exist')
    rows = extract_geometry_rows(a.pdf, dpi=200)
    print(json.dumps({'file': a.pdf.name, 'rows': rows, 'count': len(rows),
                      'note': 'OCR evidence only. No inference, data writes, or Groq calls.'}, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
