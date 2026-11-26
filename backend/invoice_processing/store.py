"""SQLite for local checks; DATABASE_URL=postgresql+psycopg://... for application deployment."""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, String, Text, DateTime, select, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .storage import configured_storage


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class InvoiceDocument(Base):
    __tablename__ = "invoice_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    original_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    extraction_method: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extraction_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    issues_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (Index("ix_invoice_identity", "supplier_key", "invoice_key"),)


class AuditEvent(Base):
    __tablename__ = "invoice_audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), index=True)
    event: Mapped[str] = mapped_column(String(60))
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InvoiceStore:
    def __init__(self, database_url: str | None = None, storage_dir: Path | None = None, storage=None):
        self.storage_dir = Path(storage_dir or os.getenv("INVOICE_STORAGE_DIR", ".invoice_data/uploads")).resolve()
        self.storage = storage if storage is not None else configured_storage(self.storage_dir)
        if database_url is None:
            database_url = os.getenv("DATABASE_URL", "sqlite:///.invoice_data/invoices.db")
        if database_url.startswith("sqlite:///"):
            Path(database_url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(database_url, future=True, connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {})
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)  # Development bootstrap; migrate using Alembic before production.

    def audit(self, session, document_id, event, details=None):
        session.add(AuditEvent(document_id=document_id, event=event, details_json=json.dumps(details or {})))

    def duplicate_exists(self, session, supplier_key, invoice_key, excluding_id):
        if not supplier_key or not invoice_key:
            return False
        query = select(InvoiceDocument.id).where(
            InvoiceDocument.supplier_key == supplier_key,
            InvoiceDocument.invoice_key == invoice_key,
            InvoiceDocument.id != excluding_id,
            InvoiceDocument.status.in_(["review_required", "processed", "approved", "exported"]),
        ).limit(1)
        return session.scalar(query) is not None
