"""Ensure whitespace-only classification never hides wrong supplier identity."""

from scripts.report_ocr_text_differences import compare_text_fields


def test_classifies_spacing_separately_from_wrong_supplier():
    manifest = {"fixtures": [{"file": "scan.pdf", "primary_scenario": "scanned",
                              "expected": {"supplier_name": "Orchard Valley Ingredients",
                                           "line_items": [{"description": "Frozen strawberry puree"},
                                                          {"description": "Blueberry concentrate"}]}}]}
    checkpoint = {"results": {"scan.pdf": {"status": "ok", "candidate": {
        "supplier_name": "SundaeValeCreamery",
        "line_items": [{"description": "Frozenstrawberrypuree"},
                       {"description": "Blueberry concentrate"}]
    }}}}
    report = compare_text_fields(manifest, checkpoint)
    assert report["counts"] == {"substantive_or_missing": 1, "whitespace_only": 1, "exact": 1}
    assert report["per_invoice"][0]["issues"][0]["field"] == "supplier_name"


def test_unprocessed_documents_are_not_scored():
    manifest = {"fixtures": [{"file": "scan.pdf", "primary_scenario": "scanned",
                              "expected": {"supplier_name": "X", "line_items": []}}]}
    report = compare_text_fields(manifest, {"results": {}})
    assert report["scored_scans"] == 0
    assert report["counts"] == {"unprocessed": 1}
