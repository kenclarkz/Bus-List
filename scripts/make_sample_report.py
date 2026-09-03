"""Generate a sample pre-formatted prep report PDF for testing the import.

Usage:
    python scripts/make_sample_report.py [output.pdf]

Optionally include a 'Replace X with Y' style substitution row.
"""
import os
import sys

try:
    import fitz
except ImportError:
    sys.exit("PyMuPDF required: pip install -r requirements.txt")


ROWS = [
    ("Unit", "Type", "Route"),
    ("Bus 142", "Coach", "Downtown 12"),
    ("Unit 155", "Van", "Airport 3"),
    ("187", "Coach", "Express 7"),
    ("Bus 220", "Shuttle", "Campus"),
    ("190", "Van", "Replace 155"),
]


def build(path):
    margins = 72
    line_h = 40
    doc = fitz.open()
    page = doc.new_page()
    y = margins
    for (u, t, r) in ROWS:
        page.insert_text((margins, y), f"{u:<12}{t:<12}{r}")
        y += line_h
    doc.save(path)
    print(f"Wrote {path} ({len(ROWS)-1} vehicles)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sample_prep_report.pdf"
    build(out)
