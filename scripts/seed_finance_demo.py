"""Populate a separate, explicitly synthetic finance demo database.

This is intentionally NOT the application ingestion/approval workflow. It writes
fixture statuses for demonstration only; it must never be used on client data.
Run from the repository root: python -m scripts.seed_finance_demo
Requires reportlab (already in backend/requirements-dev.txt).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import uuid
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy import func, select

from backend.invoice_processing.schemas import InvoiceCandidate, InvoiceLine, Issue
from backend.invoice_processing.storage import LocalStorage
from backend.invoice_processing.store import AuditEvent, InvoiceDocument, InvoiceStore
from backend.invoice_processing.validation import issues_for, normalize_identity

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / ".invoice_data"
DEMO_DB = DEMO_DIR / "sundaevale_finance_demo.db"
DEMO_PDFS = DEMO_DIR / "finance_demo_uploads"
SEED_COUNT = 60

# All fictional supplier identities. Exact numeric values are not benchmark metrics.
SUPPLIERS = (
    ("Blue Meadow Dairy Co.", (("Whole milk, gallon", "3.85"), ("Heavy cream, gallon", "9.50"))),
    ("Orchard Valley Ingredients", (("Strawberry puree, case", "26.00"), ("Blueberry concentrate, case", "34.50"))),
    ("FrostPak Packaging LLC", (("Printed 12oz cups, carton", "82.00"), ("Lid packs, carton", "34.00"))),
    ("Lakeshore Logistics LLC", (("Chilled local delivery", "135.00"), ("Cold-chain packing", "38.00"))),
    ("Ember Cocoa Supply Inc.", (("Dutch cocoa, bag", "62.00"), ("Dark chocolate chips, case", "45.00"))),
    ("Northfield Refrigeration Services", (("Cold room maintenance", "245.00"), ("Refrigeration inspection", "90.00"))),
    ("Golden Spoon Equipment Co.", (("Scoop and utensil set", "52.00"), ("Display freezer supplies", "75.00"))),
    ("Pine Ridge Produce Co.", (("Fresh banana crate", "19.00"), ("Mango pulp, case", "28.00"))),
    ("Evergreen Paper Goods", (("Compostable napkin carton", "42.00"), ("Paper takeout bags, case", "37.00"))),
    ("Sweet River Syrups", (("Vanilla bean syrup, case", "58.00"), ("Caramel sauce, case", "44.00"))),
)


def q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def previous_month(today: date, months_back: int) -> tuple[int, int]:
    absolute_month = today.year * 12 + today.month - 1 - months_back
    return absolute_month // 12, absolute_month % 12 + 1


def fixtures(today: date) -> list[dict]:
    """60 deterministic, six-month invoice records, with distinct financial states."""
    result: list[dict] = []
    for i in range(SEED_COUNT):
        months_back = i // 10
        year, month = previous_month(today, months_back)
        # Spread dates across each month while keeping recent invoices in the past.
        inv_date = min(date(year, month, 1) + timedelta(days=1 + (i * 3) % 17), today)
        due = inv_date + timedelta(days=25 + (i % 4) * 5)
        if months_back == 0:  # Current-period due dates give the dashboard real examples.
            due = max(inv_date + timedelta(days=1), today + timedelta(days=(i % 12) - 3))
        supplier, catalog = SUPPLIERS[i % len(SUPPLIERS)]
        lines: list[InvoiceLine] = []
        for item_idx, (description, unit) in enumerate(catalog):
            qty = Decimal(2 + ((i * 3 + item_idx * 7) % 16))
            price = Decimal(unit)
            lines.append(InvoiceLine(description=description, quantity=qty,
                                     unit_price=price, line_total=q(qty * price)))
        subtotal = sum((line.line_total for line in lines), Decimal("0"))
        tax = q(subtotal * Decimal("0.06")) if i % 3 == 0 else Decimal("0.00")
        shipping = Decimal("18.00") if i % 4 == 0 else Decimal("0.00")
        discount = q(subtotal * Decimal("0.03")) if i % 8 == 0 else Decimal("0.00")
        total = q(subtotal + tax + shipping - discount)
        inv_no = f"SV-{year}{month:02d}-{i+1:04d}"
        # A duplicate of an existing *approved* invoice to demonstrate safeguards.
        if i == 6:
            first = result[1]
            supplier = first["invoice"].supplier_name
            inv_no = first["invoice"].invoice_number
            lines = [line.model_copy(deep=True) for line in first["invoice"].line_items]
            subtotal = first["invoice"].subtotal
            tax = first["invoice"].tax
            shipping = first["invoice"].shipping
            discount = first["invoice"].discount
            total = first["invoice"].total
            inv_date = first["invoice"].invoice_date
            due = first["invoice"].due_date
        if i in (2, 15, 28, 41, 54):
            total += Decimal("25.00")  # Deliberately invalid invoice total.
            status = "rejected"
        elif i in (4, 6, 11, 18, 25, 32, 39, 46, 53, 59):
            status = "review_required"
        elif i in (7, 22, 36, 57):
            status = "processed"
        elif i % 8 == 0:
            status = "exported"
        else:
            status = "approved"
        invoice = InvoiceCandidate(
            supplier_name=supplier, invoice_number=inv_no,
            invoice_date=inv_date, due_date=due, currency="USD",
            line_items=lines, subtotal=subtotal, tax=tax,
            shipping=shipping, discount=discount, total=total,
        )
        issues = issues_for(invoice, duplicate=(i == 6))
        if status == "review_required" and i != 6:
            issues.append(Issue(code="VERIFY_EXTRACTION", severity="review",
                                field="extraction", message="Verify the extracted invoice details against the original."))
        if status in ("approved", "exported", "processed"):
            assert not issues, (i, issues)
        if status == "rejected":
            assert any(x.code == "TOTAL_MISMATCH" for x in issues)
        if i == 6:
            assert any(x.code == "POSSIBLE_DUPLICATE" for x in issues)
        received = datetime.combine(min(inv_date + timedelta(days=1), today), time(hour=10 + (i % 8)), tzinfo=timezone.utc)
        result.append({"invoice": invoice, "status": status, "issues": issues,
                       "created": received, "index": i})
    return result


def render_invoice(candidate: InvoiceCandidate) -> bytes:
    """PDF display exactly matches seeded structured fields (including exceptions)."""
    output = io.BytesIO()
    c = Canvas(output, pagesize=(612, 792), pageCompression=1)
    c.setTitle(f"Synthetic demo invoice {candidate.invoice_number}")
    c.setFillColor(HexColor("#392d38"))
    c.rect(0, 701, 612, 91, fill=1, stroke=0)
    c.setFillColor(HexColor("#fff6e8"))
    c.setFont("Helvetica-Bold", 22)
    c.drawString(38, 751, "SUPPLIER INVOICE")
    c.setFont("Helvetica", 9)
    c.drawString(38, 726, "SYNTHETIC DEMONSTRATION DATA  |  NOT A REAL PURCHASE")
    c.setFillColor(HexColor("#392d38"))
    c.setFont("Helvetica-Bold", 16)
    c.drawString(38, 662, candidate.supplier_name or "")
    c.setFont("Helvetica", 10)
    c.drawString(38, 637, "Bill to: SundaeVale Creamery - Accounts Payable")
    c.drawString(38, 612, f"Invoice number: {candidate.invoice_number or 'Not supplied'}")
    c.drawString(38, 591, f"Invoice date: {candidate.invoice_date or 'Not supplied'}")
    c.drawString(38, 570, f"Due date: {candidate.due_date or 'Not supplied'}")
    c.drawString(38, 549, f"Currency: {candidate.currency or 'Not supplied'}")
    c.setFillColor(HexColor("#f0e4cf"))
    c.rect(38, 495, 536, 28, fill=1, stroke=0)
    c.setFillColor(HexColor("#392d38"))
    c.setFont("Helvetica-Bold", 9)
    for x, heading in ((45, "Item"), (338, "Qty"), (412, "Unit price"), (510, "Amount")):
        c.drawString(x, 505, heading)
    c.setFont("Helvetica", 9)
    y = 475
    for line in candidate.line_items:
        c.drawString(45, y, line.description[:45])
        c.drawRightString(365, y, str(line.quantity or ""))
        c.drawRightString(470, y, f"{line.unit_price:.2f}" if line.unit_price is not None else "")
        c.drawRightString(565, y, f"{line.line_total:.2f}" if line.line_total is not None else "")
        y -= 29
    y -= 20
    c.setStrokeColor(HexColor("#dcd6d0"))
    c.line(345, y + 17, 565, y + 17)
    c.setFillColor(HexColor("#392d38"))
    for field, label in (("subtotal", "Subtotal"), ("tax", "Tax"),
                         ("shipping", "Shipping"), ("discount", "Discount"), ("total", "Total")):
        if field == "total":
            c.setFont("Helvetica-Bold", 12)
        else:
            c.setFont("Helvetica", 10)
        value = getattr(candidate, field)
        c.drawString(370, y, label)
        c.drawRightString(565, y, f"{value:.2f}" if value is not None else "-")
        y -= 24
    c.setFillColor(HexColor("#777069"))
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(38, 83, "All supplier identities, amounts and approval statuses in this demonstration are fictional.")
    c.drawString(38, 67, "No real products, services or payments are represented by this document.")
    c.save()
    return output.getvalue()


def seed(*, today: date | None = None, database: Path = DEMO_DB, pdf_dir: Path = DEMO_PDFS) -> dict:
    """Only create fixture records in an isolated demo database, never active records."""
    database = database.resolve()
    pdf_dir = pdf_dir.resolve()
    # Tests can supply isolated temp directories explicitly, but production defaults
    # never use DATABASE_URL or INVOICE_STORAGE_DIR from the shell.
    database.parent.mkdir(parents=True, exist_ok=True)
    if database == (ROOT / ".invoice_data" / "invoices.db").resolve():
        raise ValueError("Refusing to write the default application database")
    if database == pdf_dir:
        raise ValueError("Database and PDF directory cannot be the same path")
    storage = LocalStorage(pdf_dir)
    store = InvoiceStore(database_url=f"sqlite:///{database.as_posix()}", storage_dir=pdf_dir, storage=storage)
    with store.Session() as session:
        existing = session.scalar(select(func.count()).select_from(InvoiceDocument))
    if existing:
        return {"status": "already_populated", "database": str(database), "records": int(existing)}
    rows = fixtures(today or date.today())
    paths = []
    try:
        with store.Session.begin() as session:
            for row in rows:
                inv: InvoiceCandidate = row["invoice"]
                identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sundaevale-finance-demo-{row['index']}"))
                payload = render_invoice(inv)
                ref = storage.save(identifier, payload)
                paths.append(ref)
                created = row["created"]
                status = row["status"]
                document = InvoiceDocument(
                    id=identifier,
                    filename=f"demo_supplier_invoice_{row['index']+1:03d}.pdf",
                    sha256=hashlib.sha256(payload).hexdigest(),
                    original_path=ref,
                    status=status,
                    extraction_method="synthetic-demo-fixture",
                    extraction_json=inv.model_dump_json(),
                    issues_json=json.dumps([x.model_dump() for x in row["issues"]]),
                    supplier_key=normalize_identity(inv.supplier_name),
                    invoice_key=normalize_identity(inv.invoice_number),
                    error_code=None,
                    created_at=created,
                    updated_at=created + timedelta(minutes=2),
                )
                session.add(document)
                session.add(AuditEvent(
                    document_id=identifier,
                    event="synthetic_demo_fixture_loaded",
                    details_json=json.dumps({"fictional": True, "seeded_state": status,
                                             "not_a_real_employee_approval": True}),
                    created_at=created,
                ))
    except Exception:
        for path in paths:
            storage.delete(path)
        raise
    counts = dict(Counter(row["status"] for row in rows))
    return {"status": "created", "database": str(database), "records": len(rows),
            "by_status": counts, "pdf_directory": str(pdf_dir)}


def main():
    parser = argparse.ArgumentParser(description="Create fictional finance dashboard data, isolated from real invoice records")
    parser.add_argument("--dry-run", action="store_true", help="Print fixture counts without database writes")
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps({"records": SEED_COUNT, "by_status": dict(Counter(r["status"] for r in fixtures(date.today()))),
                          "destination": str(DEMO_DB), "writes": False}, indent=2))
    else:
        print(json.dumps(seed(), indent=2))
        print("DEMO ONLY: Seeded approval states are fictional; no real invoice was processed or approved.")


if __name__ == "__main__":
    main()
