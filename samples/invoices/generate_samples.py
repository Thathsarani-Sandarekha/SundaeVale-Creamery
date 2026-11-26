"""Regenerate fictional, redistribution-safe supplier-invoice PDF fixtures.

Requires reportlab and PyMuPDF (`pip install reportlab pymupdf`).
"""
from __future__ import annotations
import shutil
from pathlib import Path
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

ROOT = Path(__file__).resolve().parent

DATA = [
    {"file": "01_blue_meadow_dairy.pdf", "supplier": "Blue Meadow Dairy Co.", "number": "BMD-2026-0141", "date": "2026-11-02", "due": "2026-12-02", "items": [("Whole milk - gallon", "48", "3.50", "168.00"), ("Heavy cream - gallon", "20", "9.00", "180.00")], "subtotal": "348.00", "tax": "0.00", "shipping": "18.00", "discount": "0.00", "total": "366.00"},
    {"file": "03_frostpak_incorrect_total.pdf", "supplier": "FrostPak Packaging LLC", "number": "FPK-2026-091", "date": "2026-11-05", "due": "2026-12-05", "items": [("12 oz printed ice cream cups", "500", "0.20", "100.00"), ("Compostable lids", "500", "0.08", "40.00")], "subtotal": "140.00", "tax": "9.80", "shipping": "12.00", "discount": "0.00", "total": "180.00"},
    {"file": "05_ember_cocoa_missing_date.pdf", "supplier": "Ember Cocoa Supply Inc.", "number": "ECS-2026-032", "date": None, "due": "2026-12-12", "items": [("Dutch cocoa - 25 lb bag", "4", "45.00", "180.00")], "subtotal": "180.00", "tax": "0.00", "shipping": "0.00", "discount": "0.00", "total": "180.00"},
    {"file": "06_lakeshore_logistics.pdf", "supplier": "Lakeshore Logistics LLC", "number": "LL-2026-230", "date": "2026-11-09", "due": "2026-11-30", "items": [("Refrigerated delivery - Route A", "2", "125.00", "250.00"), ("Dry ice handling", "4", "12.50", "50.00")], "subtotal": "300.00", "tax": "0.00", "shipping": "0.00", "discount": "0.00", "total": "300.00"},
    {"file": "02_orchard_valley_scanned.pdf", "supplier": "Orchard Valley Ingredients", "number": "OVI-2026-055", "date": "2026-11-03", "due": "2026-12-03", "items": [("Frozen strawberry puree - case", "12", "22.00", "264.00")], "subtotal": "264.00", "tax": "0.00", "shipping": "14.00", "discount": "0.00", "total": "278.00"},
]


def create_pdf(data, target):
    c = canvas.Canvas(str(target), pagesize=(612, 792))
    c.setFillColor(HexColor("#162D40"))
    c.rect(0, 704, 612, 88, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont("Helvetica-Bold", 23)
    c.drawString(44, 744, "SUPPLIER INVOICE")
    c.setFont("Helvetica", 10)
    c.drawString(44, 724, "Synthetic document  |  SundaeVale Creamery  |  Portfolio demonstration")
    c.setFillColor(HexColor("#172B40"))
    c.setFont("Helvetica-Bold", 15)
    c.drawString(44, 663, data["supplier"])
    c.setFont("Helvetica", 10)
    c.drawString(44, 645, "Bill To: SundaeVale Creamery | Accounts Payable")
    rows = [
        f"Supplier: {data['supplier']}", f"Invoice Number: {data['number']}",
        *([f"Invoice Date: {data['date']}"] if data["date"] else []),
        f"Due Date: {data['due']}", "Currency: USD",
    ]
    y = 606
    for line in rows:
        c.drawString(44, y, line)
        y -= 21
    y -= 19
    c.setFillColor(HexColor("#EAF1F5"))
    c.rect(40, y - 9, 532, 25, fill=1, stroke=0)
    c.setFillColor(HexColor("#172B40"))
    c.setFont("Courier-Bold", 8.5)
    c.drawString(47, y, "Item | Quantity | Unit Price | Amount")
    c.setFont("Courier", 8.5)
    y -= 34
    for item in data["items"]:
        c.drawString(47, y, " | ".join(item))
        y -= 26
    y -= 28
    c.setFont("Helvetica", 10)
    for key, label in [("subtotal", "Subtotal"), ("tax", "Tax"), ("shipping", "Shipping"), ("discount", "Discount"), ("total", "Total")]:
        if key == "total":
            c.setFont("Helvetica-Bold", 12)
        c.drawString(373, y, f"{label}: {data[key]}")
        y -= 23
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(HexColor("#627588"))
    c.drawString(44, 82, "All company and supplier identities are fictional. No actual purchase or payment occurred.")
    c.drawString(44, 65, "Generated for Document AI extraction, validation and audit testing only.")
    c.save()


def create_scanned(original_path, dest_path):
    import fitz
    source = fitz.open(str(original_path))
    rendered = source[0].get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False)
    scan_pdf = fitz.open()
    page = scan_pdf.new_page(width=612, height=792)
    page.insert_image(page.rect, stream=rendered.tobytes("png"))
    scan_pdf.save(str(dest_path))
    scan_pdf.close()
    source.close()


def main():
    for data in DATA:
        path = ROOT / data["file"]
        if "scanned" in path.name:
            source = ROOT / ".temporary_digital.pdf"
            create_pdf(data, source)
            create_scanned(source, path)
            source.unlink()
        else:
            create_pdf(data, path)
    shutil.copyfile(ROOT / "01_blue_meadow_dairy.pdf", ROOT / "04_blue_meadow_duplicate.pdf")
    print("Generated 6 fictional PDF invoices (including one image-only scan)")


if __name__ == "__main__":
    main()
