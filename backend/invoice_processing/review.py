"""Review models and server-side helpers for the local invoice workflow."""
from __future__ import annotations

import hashlib
import json
from pydantic import BaseModel, ConfigDict, Field
from .schemas import InvoiceCandidate


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_token: str = Field(min_length=10, max_length=128)
    reviewer: str = Field(min_length=2, max_length=80)
    extraction: InvoiceCandidate


class ReviewAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_token: str = Field(min_length=10, max_length=128)
    reviewer: str = Field(min_length=2, max_length=80)


class RejectAction(ReviewAction):
    reason: str = Field(min_length=4, max_length=500)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_ids: list[str] = Field(min_length=1, max_length=100)
    reviewer: str = Field(min_length=2, max_length=80)


def token_for(doc) -> str:
    """Optimistic review token: invalidated by changes to the record."""
    content = "\0".join([
        doc.id, doc.status, doc.updated_at.replace(tzinfo=None).isoformat() if doc.updated_at else "",
        doc.extraction_json or "", doc.issues_json or "",
    ])
    return hashlib.sha256(content.encode()).hexdigest()


def decode_extraction(doc) -> InvoiceCandidate:
    if not doc.extraction_json:
        raise ValueError("Invoice has no extracted fields")
    return InvoiceCandidate.model_validate_json(doc.extraction_json)


def changed_fields(old: InvoiceCandidate, new: InvoiceCandidate) -> list[str]:
    """Audit field names only, not sensitive values."""
    first = old.model_dump(mode="json")
    second = new.model_dump(mode="json")
    return [field for field in first if first[field] != second[field]]


def safe_csv_cell(value: object) -> str:
    """Prevent spreadsheet formula execution in exported text cells."""
    if value is None:
        return ""
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text
