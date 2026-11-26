"""PDF text and OCR adapter. Scans require Docling, not imaginary OCR results."""
from __future__ import annotations
import os
from pathlib import Path
from pypdf import PdfReader


class ParseFailure(Exception):
    pass


def extract_pdf(path: Path) -> tuple[str, str]:
    """Prefer Docling when installed; otherwise extract selectable text using pypdf."""
    try:
        # Using pypdf upfront also checks encrypted inputs and page-count limits.
        reader = PdfReader(str(path), strict=True)
        if reader.is_encrypted:
            raise ParseFailure("ENCRYPTED_PDF")
        if len(reader.pages) > 25:
            raise ParseFailure("TOO_MANY_PAGES")
    except ParseFailure:
        raise
    except Exception as exc:
        raise ParseFailure("INVALID_PDF") from exc

    # Selectable-text PDFs are already machine-readable. Prefer their native text
    # over Docling Markdown, which can rearrange labels and invoice rows into
    # tables that the intentionally simple rules baseline does not recognize.
    # Reserve Docling + OCR for image-only (or near-empty) PDFs.
    native_text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if len(native_text) >= 30:
        return native_text, "pypdf-digital"

    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise ParseFailure("OCR_UNAVAILABLE") from exc

    try:
        result = DocumentConverter().convert(path, max_num_pages=25,
                                             max_file_size=8 * 1024 * 1024)
        text = result.document.export_to_markdown().strip()
        if len(text) < 30:
            raise ParseFailure("NO_TEXT_FOUND")
        geometry_mode = os.getenv("INVOICE_OCR_GEOMETRY", "0").strip().lower()
        # 'auto' is opt-in and triggers only for visibly incomplete OCR table
        # rows (e.g., a row with a net amount but no recognizable QTY/Rate).
        # '1' retains the existing eager experimental behavior.
        from .ocr_geometry_evidence import needs_geometry_evidence
        use_geometry = geometry_mode == "1" or (
            geometry_mode == "auto" and needs_geometry_evidence(text)
        )
        if use_geometry:
            from .ocr_geometry_evidence import extract_geometry_rows, append_geometry_evidence
            try:
                observations = extract_geometry_rows(path)
                if observations:
                    text = append_geometry_evidence(text, observations)
                    return text, ("docling-ocr+geometry200-auto"
                                  if geometry_mode == "auto" else "docling-ocr+geometry200")
            except Exception:
                # Supplemental OCR is best-effort; never discard the primary
                # text or turn a readable document into a failed job.
                pass
        return text, "docling-ocr"
    except ParseFailure:
        raise
    except Exception as exc:
        raise ParseFailure("DOCLING_FAILED") from exc
