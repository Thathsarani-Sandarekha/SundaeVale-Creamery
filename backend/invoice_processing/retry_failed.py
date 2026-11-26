"""Explicitly retry selected failed demo invoices without deleting audit/history.

Run from repository root:
    python -m backend.invoice_processing.retry_failed 01_blue_meadow_dairy.pdf 03_frostpak_incorrect_total.pdf
"""
from __future__ import annotations
import argparse
from sqlalchemy import select

from .settings import load_project_env
from .store import InvoiceDocument, InvoiceStore


def requeue_failed(store: InvoiceStore, filenames: list[str]) -> int:
    """Requeue only failed rows for the explicitly named filenames."""
    if not filenames:
        return 0
    with store.Session.begin() as session:
        docs = session.scalars(select(InvoiceDocument).where(
            InvoiceDocument.filename.in_(filenames),
            InvoiceDocument.status == "failed",
        )).all()
        for doc in docs:
            previous_error = doc.error_code
            doc.status = "queued"
            doc.error_code = None
            store.audit(session, doc.id, "manually_requeued", {"previous_error_code": previous_error})
        return len(docs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Retry selected failed invoice files only")
    parser.add_argument("filenames", nargs="+", help="Exact filenames of failed invoices to retry")
    args = parser.parse_args()
    load_project_env()
    count = requeue_failed(InvoiceStore(), args.filenames)
    print(f"Requeued {count} failed invoice job(s)")


if __name__ == "__main__":
    main()
