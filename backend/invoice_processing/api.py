"""SundaeVale Invoice API (milestone 1: ingestion, status, validation)."""
from __future__ import annotations
import csv
import hashlib
import io
import json
import logging
import os
import secrets
import uuid
from pathlib import Path

from fastapi import FastAPI, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
from urllib.parse import quote
from .storage import MAX_PDF_BYTES, StorageError
from pydantic import ValidationError
from pypdf import PdfReader
from sqlalchemy import select

from .store import InvoiceDocument, InvoiceStore, AuditEvent
from .review import (ReviewUpdate, ReviewAction, RejectAction, ExportRequest,
                     token_for, changed_fields, safe_csv_cell, decode_extraction)
from .validation import issues_for, normalize_identity

logger = logging.getLogger(__name__)
MAX_BATCH_SIZE = 10


def create_app(database_url: str | None = None, storage_dir: Path | None = None, api_key: str | None = None, storage=None) -> FastAPI:
    key = api_key if api_key is not None else os.getenv("INVOICE_API_KEY", "")
    if not key or key.strip().upper().startswith(("YOUR_", "REPLACE_WITH")):
        raise RuntimeError("Set INVOICE_API_KEY to a private development key before running the invoice API.")
    store = InvoiceStore(database_url=database_url, storage_dir=storage_dir, storage=storage)
    app = FastAPI(title="SundaeVale Invoice Processing API", version="0.1.0")
    app.state.invoice_store = store

    def authorize(x_api_key: str | None = Header(default=None)):
        if not x_api_key or not secrets.compare_digest(x_api_key, key):
            raise HTTPException(status_code=401, detail="Invalid API key")

    def serialize(doc: InvoiceDocument):
        return {
            "id": doc.id,
            "filename": doc.filename,
            "status": doc.status,
            "sha256": doc.sha256,
            "created_at": doc.created_at.isoformat(),
            "extraction_method": doc.extraction_method,
            "extraction": json.loads(doc.extraction_json) if doc.extraction_json else None,
            "issues": json.loads(doc.issues_json) if doc.issues_json else [],
            "error_code": doc.error_code,
            "review_token": token_for(doc),
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        }

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "sundaevale-invoices"}

    @app.post("/api/v1/invoices/upload", dependencies=[Depends(authorize)], status_code=202)
    async def upload(files: list[UploadFile] = File(...)):
        if not files or len(files) > MAX_BATCH_SIZE:
            raise HTTPException(status_code=400, detail=f"Upload 1-{MAX_BATCH_SIZE} PDFs per batch")
        validated: list[tuple[str, bytes, str]] = []
        for f in files:
            raw_name = f.filename or ""
            clean_name = Path(raw_name.replace("\\", "/")).name[:255]
            if not clean_name.casefold().endswith(".pdf"):
                raise HTTPException(status_code=400, detail="Only PDF invoices are accepted")
            data = await f.read(MAX_PDF_BYTES + 1)
            await f.close()
            if len(data) > MAX_PDF_BYTES:
                raise HTTPException(status_code=413, detail="PDF exceeds 8 MB limit")
            if not data.startswith(b"%PDF-"):
                raise HTTPException(status_code=400, detail="Invalid PDF signature")
            # Reject malformed / encrypted / huge page-count files before persisting.
            try:
                from io import BytesIO
                reader = PdfReader(BytesIO(data), strict=True)
                if reader.is_encrypted:
                    raise ValueError("Encrypted PDF")
                if len(reader.pages) > 25:
                    raise HTTPException(status_code=413, detail="PDF exceeds 25 pages")
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=400, detail="PDF cannot be parsed or is encrypted") from exc
            validated.append((clean_name, data, hashlib.sha256(data).hexdigest()))
        results = []
        paths = []
        try:
            with store.Session.begin() as db:
                for clean_name, data, checksum in validated:
                    doc_id = str(uuid.uuid4())
                    target = store.storage.save(doc_id, data)
                    paths.append(target)
                    job = InvoiceDocument(id=doc_id, filename=clean_name, original_path=str(target),
                                          sha256=checksum, status="queued")
                    db.add(job)
                    store.audit(db, doc_id, "uploaded", {"file_size": len(data)})
                    results.append({"id": doc_id, "filename": clean_name, "status": "queued"})
        except Exception:
            for path in paths:
                try:
                    store.storage.delete(path)
                except Exception:
                    logger.error("invoice_storage_cleanup_failed")
            raise HTTPException(status_code=500, detail="Unable to save invoice batch")
        return {"uploaded": results, "count": len(results)}

    @app.get("/api/v1/invoices", dependencies=[Depends(authorize)])
    def list_invoices():
        with store.Session() as db:
            documents = db.scalars(select(InvoiceDocument).order_by(InvoiceDocument.created_at.desc()).limit(100)).all()
            return {"documents": [serialize(x) for x in documents],
                    "demo_mode": os.getenv("SUNDAEVALE_DEMO_MODE", "0") == "1"}

    @app.get("/api/v1/invoices/{document_id}", dependencies=[Depends(authorize)])
    def get_invoice(document_id: str):
        with store.Session() as db:
            doc = db.get(InvoiceDocument, document_id)
            if doc is None:
                raise HTTPException(status_code=404, detail="Invoice not found")
            return serialize(doc)


    def require_doc(db, document_id: str) -> InvoiceDocument:
        doc = db.get(InvoiceDocument, document_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Invoice not found")
        return doc

    def require_editable(doc: InvoiceDocument, token: str) -> None:
        if doc.status not in ("processed", "review_required"):
            raise HTTPException(status_code=409, detail="Only processed or review-required invoices can be reviewed")
        if not secrets.compare_digest(token_for(doc), token):
            raise HTTPException(status_code=409, detail="Invoice changed. Refresh before reviewing.")

    @app.get("/api/v1/invoices/{document_id}/pdf", dependencies=[Depends(authorize)])
    def original_pdf(document_id: str):
        with store.Session() as db:
            doc = require_doc(db, document_id)
            try:
                data = store.storage.read(doc.original_path)
            except (FileNotFoundError, StorageError):
                raise HTTPException(status_code=404, detail="Original not found") from None
            except Exception:
                logger.error("invoice_storage_read_failed")
                raise HTTPException(status_code=503, detail="Original storage unavailable") from None
            return Response(data, media_type="application/pdf", headers={
                                    "Content-Disposition": "inline; filename*=UTF-8''" + quote(doc.filename, safe=""),
                                    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                    "Content-Security-Policy": "sandbox",
                                })

    @app.get("/api/v1/invoices/{document_id}/audit", dependencies=[Depends(authorize)])
    def invoice_audit(document_id: str):
        with store.Session() as db:
            require_doc(db, document_id)
            rows = db.scalars(select(AuditEvent).where(AuditEvent.document_id == document_id)
                              .order_by(AuditEvent.created_at, AuditEvent.id)).all()
            return {"events": [
                {"event": row.event, "created_at": row.created_at.isoformat(),
                 "details": json.loads(row.details_json)} for row in rows
            ]}

    @app.patch("/api/v1/invoices/{document_id}/review", dependencies=[Depends(authorize)])
    def update_review(document_id: str, body: ReviewUpdate):
        with store.Session.begin() as db:
            doc = require_doc(db, document_id)
            require_editable(doc, body.review_token)
            old = decode_extraction(doc)
            new = body.extraction
            supplier, number = normalize_identity(new.supplier_name), normalize_identity(new.invoice_number)
            duplicate = store.duplicate_exists(db, supplier, number, doc.id)
            flags = issues_for(new, duplicate=duplicate)
            fields = changed_fields(old, new)
            doc.extraction_json = new.model_dump_json()
            doc.issues_json = json.dumps([x.model_dump() for x in flags])
            doc.status = "review_required" if flags else "processed"
            doc.supplier_key = supplier
            doc.invoice_key = number
            store.audit(db, doc.id, "review_saved", {
                "actor_label_unverified": body.reviewer,
                "changed_fields": fields,
                "issue_codes": [x.code for x in flags],
            })
            db.flush()
            return serialize(doc)

    @app.post("/api/v1/invoices/{document_id}/approve", dependencies=[Depends(authorize)])
    def approve_invoice(document_id: str, body: ReviewAction):
        with store.Session.begin() as db:
            doc = require_doc(db, document_id)
            require_editable(doc, body.review_token)
            current = decode_extraction(doc)
            # Enforce the model/OCR review requirement server-side too. The UI
            # alone cannot authorize approval when review has not been saved.
            pending = {x.get("code") for x in json.loads(doc.issues_json or "[]")}
            if "VERIFY_EXTRACTION" in pending:
                raise HTTPException(status_code=409, detail="Verify extracted fields and save review before approval")
            flags = issues_for(current, duplicate=store.duplicate_exists(
                db, normalize_identity(current.supplier_name), normalize_identity(current.invoice_number), doc.id))
            if flags:
                raise HTTPException(status_code=409, detail={
                    "message": "Resolve validation issues before approval",
                    "issues": [f.model_dump() for f in flags],
                })
            doc.status = "approved"
            doc.issues_json = "[]"
            store.audit(db, doc.id, "approved", {"actor_label_unverified": body.reviewer})
            db.flush()
            return serialize(doc)

    @app.post("/api/v1/invoices/{document_id}/reject", dependencies=[Depends(authorize)])
    def reject_invoice(document_id: str, body: RejectAction):
        with store.Session.begin() as db:
            doc = require_doc(db, document_id)
            require_editable(doc, body.review_token)
            doc.status = "rejected"
            store.audit(db, doc.id, "rejected", {
                "actor_label_unverified": body.reviewer, "reason": body.reason,
            })
            db.flush()
            return serialize(doc)

    @app.post("/api/v1/invoices/export", dependencies=[Depends(authorize)])
    def export_csv(body: ExportRequest):
        if len(set(body.document_ids)) != len(body.document_ids):
            raise HTTPException(status_code=400, detail="Document IDs cannot be repeated")
        with store.Session.begin() as db:
            docs = [require_doc(db, document_id) for document_id in body.document_ids]
            if any(doc.status not in ("approved", "exported") for doc in docs):
                raise HTTPException(status_code=409, detail="Only approved invoices can be exported")
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer)
            writer.writerow(["document_id", "supplier_name", "invoice_number", "invoice_date", "due_date",
                             "currency", "subtotal", "tax", "shipping", "discount", "total",
                             "line_item_description", "quantity", "unit_price", "line_total"])
            for doc in docs:
                candidate = decode_extraction(doc)
                base = [doc.id, candidate.supplier_name, candidate.invoice_number,
                        candidate.invoice_date, candidate.due_date, candidate.currency,
                        candidate.subtotal, candidate.tax, candidate.shipping, candidate.discount, candidate.total]
                for line in candidate.line_items or [None]:
                    extra = [line.description, line.quantity, line.unit_price, line.line_total] if line else [None]*4
                    writer.writerow([safe_csv_cell(v) for v in [*base, *extra]])
                if doc.status != "exported":
                    doc.status = "exported"
                store.audit(db, doc.id, "exported", {"actor_label_unverified": body.reviewer,
                                                      "format": "csv", "line_item_rows": len(candidate.line_items) or 1})
            response = buffer.getvalue()
        return Response(content=response, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="sundaevale-approved-invoices.csv"',
                                 "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    return app


# Run from repo root: uvicorn backend.invoice_processing.server:app --host 127.0.0.1 --port 8001
