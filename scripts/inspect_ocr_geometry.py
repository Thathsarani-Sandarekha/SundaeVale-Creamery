"""Inspect spatial OCR tokens for synthetic scans without calling Groq.

Output is deliberately an inspection aid, not automatic financial extraction:
OCR box geometry may be incomplete or incorrect. Use it to decide whether a
layout-aware table reconstruction is feasible before changing production logic.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def inspect(path: Path, dpi: int = 160) -> list[dict]:
    try:
        import fitz
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError("Install backend/requirements-ocr.txt and pymupdf to inspect OCR geometry") from exc
    tokens = []
    engine = RapidOCR()
    with fitz.open(str(path)) as doc:
        if len(doc) > 2:
            raise ValueError("Diagnostic limited to two-page synthetic fixtures")
        for index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            output = engine(pix.tobytes("png"))
            boxes = getattr(output, "boxes", None)
            texts = getattr(output, "txts", None)
            if boxes is None or texts is None:
                continue
            for word, corners in zip(texts, boxes):
                if not word:
                    continue
                xs = [float(x) for x, _ in corners]
                ys = [float(y) for _, y in corners]
                tokens.append({"page": index + 1, "y": round(sum(ys)/len(ys), 1),
                               "x": round(min(xs), 1), "text": str(word)})
    return sorted(tokens, key=lambda item: (item["page"], round(item["y"] / 10), item["x"]))


def main() -> int:
    p = argparse.ArgumentParser(description="Visual-geometry diagnostic for fictional scanned PDFs only")
    p.add_argument("pdf", type=Path)
    p.add_argument("--dpi", type=int, choices=[120, 160, 200], default=160)
    p.add_argument("--allow-synthetic", action="store_true")
    args = p.parse_args()
    if not args.allow_synthetic:
        p.error("Use --allow-synthetic only with the fictional evaluation invoices")
    if not args.pdf.name.startswith("v2_") or "scanned" not in args.pdf.name:
        p.error("Only v2_*_scanned.pdf synthetic fixtures are supported")
    try:
        tokens = inspect(args.pdf, dpi=args.dpi)
    except (ValueError, RuntimeError) as exc:
        p.error(str(exc))
    for item in tokens:
        print(f"p{item['page']}  x={item['x']:7.1f} y={item['y']:7.1f}  {item['text']}")
    print(f"Tokens detected: {len(tokens)}. Coordinates are raster pixels, not validated table cells.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
