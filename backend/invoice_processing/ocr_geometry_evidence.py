"""Optional, conservative high-resolution OCR evidence for scanned invoice rows.

Never infer a missing financial field from arithmetic: every suggested value
must be directly recognized as its own OCR token. The numeric consistency
check only rejects malformed/ambiguous rows; it never manufactures a value.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import re
from pathlib import Path

GEOMETRY_MARKER = "SUPPLEMENTAL_200DPI_OCR_ROW_EVIDENCE"
_NUMBER = re.compile(r"^[+-]?\d[\d,]*(?:\.\d+)?$")
_HEADER_WORDS = ("qty", "quantity", "rate", "unit price", "net", "total", "product/service", "description")
_STOP_WORDS = ("net supplies", "subtotal", "grand total", "grandtotal", "tax:", "handling:", "deduction:", "total due")


def _decimal(word: str) -> Decimal | None:
    if not _NUMBER.fullmatch(word.strip()):
        return None
    try:
        return Decimal(word.replace(",", ""))
    except InvalidOperation:
        return None


def _clusters(tokens: list[dict], tolerance: float = 12) -> list[list[dict]]:
    """Group OCR boxes by approximate vertical center, preserving spatial order."""
    ordered = sorted(tokens, key=lambda v: (v["page"], v["y"], v["x"]))
    groups: list[list[dict]] = []
    for token in ordered:
        if (not groups or token["page"] != groups[-1][0]["page"]
                or abs(token["y"] - sum(t["y"] for t in groups[-1]) / len(groups[-1])) > tolerance):
            groups.append([token])
        else:
            groups[-1].append(token)
    return [sorted(g, key=lambda v: v["x"]) for g in groups]


def high_confidence_rows(tokens: list[dict]) -> list[dict]:
    """Produce evidence only when actual OCR sees description + qty + price + net.

    No matching by absolute x to the Docling headers: the observed source has
    shifted table cells. We use order within the same visual row, numeric
    consistency, and the source table header, never computed replacements.
    """
    evidence: list[dict] = []
    groups = _clusters(tokens)
    by_page: dict[int, list[list[dict]]] = {}
    for g in groups:
        by_page.setdefault(g[0]["page"], []).append(g)
    for page, rows in by_page.items():
        header_idx = None
        for idx, g in enumerate(rows):
            labels = " ".join(t["text"].strip().lower() for t in g)
            if ("qty" in labels or "quantity" in labels) and ("rate" in labels or "unit price" in labels) and ("net" in labels or "total" in labels):
                header_idx = idx
                break
        if header_idx is None:
            continue
        for g in rows[header_idx + 1:]:
            raw = " ".join(t["text"].strip() for t in g)
            if any(word in raw.lower() for word in _STOP_WORDS):
                break
            numeric = [(t, _decimal(t["text"])) for t in g]
            numeric = [(t, v) for t, v in numeric if v is not None]
            # Exactly three recognized tokens avoids guessing from incomplete
            # or misaligned OCR rows, financial summaries, and 4+ numeric cells.
            if len(numeric) != 3:
                continue
            first_x = numeric[0][0]["x"]
            descriptions = [t["text"].strip() for t in g
                            if t["x"] < first_x and _decimal(t["text"]) is None]
            description = " ".join(descriptions).strip()
            if not description or len(description) > 160 or not re.search(r"[A-Za-z]{3}", description):
                continue
            qty, rate, net = (value for _, value in numeric)
            if qty <= 0 or rate < 0 or net < 0 or abs(qty * rate - net) > Decimal("0.02"):
                continue
            evidence.append({"page": page, "description": description,
                             "quantity": numeric[0][0]["text"].strip(),
                             "unit_price": numeric[1][0]["text"].strip(),
                             "line_total": numeric[2][0]["text"].strip()})
            if len(evidence) >= 40:
                return evidence
    return evidence


def extract_geometry_rows(pdf: Path, dpi: int = 200) -> list[dict]:
    """Run additional OCR only when explicitly enabled for image-only PDFs."""
    try:
        import fitz
        from rapidocr import RapidOCR
    except ImportError:
        return []  # Optional dependency missing: the normal OCR path remains.
    engine = RapidOCR()
    tokens = []
    with fitz.open(str(pdf)) as document:
        if not document.page_count or document.page_count > 2:
            return []  # Hard performance bound. No hidden multi-page scanning.
        for page_index, page in enumerate(document):
            image = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            result = engine(image.tobytes("png"))
            # RapidOCR boxes are NumPy arrays; never use ``array or []`` or
            # ``not array`` because their truth value is ambiguous.
            words = getattr(result, "txts", None)
            boxes = getattr(result, "boxes", None)
            if words is None or boxes is None:
                continue
            for word, box in zip(words, boxes):
                if word is None or box is None:
                    continue
                if not str(word).strip() or len(box) == 0:
                    continue
                try:
                    xs = [float(p[0]) for p in box]
                    ys = [float(p[1]) for p in box]
                except (IndexError, ValueError, TypeError):
                    continue
                tokens.append({"page": page_index + 1, "x": min(xs),
                               "y": sum(ys) / len(ys), "text": str(word)})
                if len(tokens) > 1000:
                    return []  # Bound parsing complexity and prompt additions.
    return high_confidence_rows(tokens)


def append_geometry_evidence(text: str, rows: list[dict]) -> str:
    """Attach OCR observations as explicitly untrusted data, not instructions."""
    if not rows:
        return text
    evidence = json.dumps(rows[:40], ensure_ascii=True, separators=(",", ":"))
    if len(evidence) > 5000:
        return text
    return (text + "\n\n" + GEOMETRY_MARKER + ": " + evidence
            + "\nThese are observed source OCR tokens in visual order, not inferred values. "
            "Verify against the invoice; do not invent omitted values.\n")


def needs_geometry_evidence(markdown: str) -> bool:
    """Heuristic, not a confidence score: opt in only for an incomplete OCR table.

    Detect a labeled item table followed by a plausible line-item row with one
    or two standalone numeric cells. This conservatively avoids running a
    second OCR pass for complete tables or arbitrary free text. It may miss
    other imperfect scans; reviewers still inspect the original PDF.
    """
    saw_header = False
    for line in markdown.splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        header = " ".join(c.lower() for c in cells)
        if (any(w in header for w in ("qty", "quantity"))
                and any(w in header for w in ("rate", "unit price", "price"))
                and any(w in header for w in ("net", "total", "amount"))):
            saw_header = True
            continue
        if not saw_header:
            continue
        if any(stop in header for stop in _STOP_WORDS):
            break
        # Consider item rows only, not table separators or summary amounts.
        if not cells or not re.search(r"[A-Za-z]{3}", cells[0]):
            continue
        if cells[0].lower().startswith(("product", "item", "description")):
            continue
        numeric = [c for c in cells[1:] if _decimal(c) is not None]
        if 1 <= len(numeric) <= 2:
            return True
    return False
