"""Generate a sample pre-formatted prep report PDF for testing the import.

Usage:
    python scripts/make_sample_report.py [output.pdf]

Generates a PDF in the ECHO East Coast Transportation prep report format.
Optionally includes a 'Replace X with Y' style substitution row.
"""
import os
import sys

try:
    import fitz
except ImportError:
    sys.exit("PyMuPDF required: pip install -r requirements.txt")


# ECHO-format data: (prep_time, unit, location_code, vehicle_type, service_type, trips)
ROWS = [
    ("01:45", "9205", "JAXSUV", "SUVSUB", "Departure", "4"),
    ("02:00", "9203", "JAXSUV", "SUVSUB", "As Directed", "1"),
    ("04:00", "4301", "JAXUNF", "TRANSITB", "Shuttle", "2"),
    ("04:00", "4303", "JAXUNF", "TRANSITB", "Shuttle", "2"),
    ("04:00", "4304", "JAXUNF", "TRANSITB", "Shuttle", "2"),
    ("04:15", "7101", "JAXMINIC", "MINIC34", "Transfer", "2"),
    ("04:30", "5100", "JAXUNF", "ADAMINIVAN", "Shuttle", "2"),
    ("06:00", "4302", "JAXUNF", "TRANSITB", "Shuttle", "2"),
    ("07:00", "8406", "JAXMC", "MOTORC", "Hourly", "1"),
    ("09:30", "9421", "JAXMB", "MINIBUS", "Hourly", "1"),
    ("09:30", "9440", "JAXMB", "MINIC40", "Hourly", "2"),
    ("09:45", "9331", "JAXVAN", "Van", "Hourly", "1"),
    ("10:00", "8437", "JAXADAMC", "ADAMOTORC", "Hourly", "1"),
    ("12:14", "9205", "JAXSUV", "SUVSUB", "Airport Arrival", "4"),
    ("13:52", "9233", "JAXSUV", "SUVYUKON", "Airport Arrival", "2"),
    ("15:00", "4302", "JAXUNF", "TRANSITB", "Shuttle", "2"),
]

HEADERS = ["Prep Time", "Vehicle", "Vehicle Type", "Type", "Trips #"]
COL_WIDTHS = [60, 90, 80, 90, 45]
ROW_H = 30  # tall enough for 2-line Vehicle cells


def _draw_table(page, x0, y0, headers, rows, col_widths, row_h):
    """Draw a bordered table so PyMuPDF find_tables() can detect it."""
    total_w = sum(col_widths)

    # Draw all cell borders
    num_rows = 1 + len(rows)
    for ri in range(num_rows):
        ry = y0 + ri * row_h
        cx = x0
        for w in col_widths:
            shape = page.new_shape()
            shape.draw_rect(fitz.Rect(cx, ry, cx + w, ry + row_h))
            shape.finish(color=(0, 0, 0))
            shape.commit()
            cx += w

    # Header text
    cx = x0
    for h, w in zip(headers, col_widths):
        page.insert_text((cx + 3, y0 + 14), h, fontsize=8)
        cx += w

    # Data text
    for ri, row in enumerate(rows):
        ry = y0 + (ri + 1) * row_h
        cx = x0
        for ci, (cell, w) in enumerate(zip(row, col_widths)):
            if ci == 1 and "\n" in cell:
                # Vehicle cell: unit on line 1, location on line 2
                lines = cell.split("\n", 1)
                page.insert_text((cx + 3, ry + 14), lines[0], fontsize=8)
                page.insert_text((cx + 3, ry + 24), lines[1], fontsize=7)
            else:
                page.insert_text((cx + 3, ry + 14), cell, fontsize=8)
            cx += w


def build(path):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 50

    # Title
    page.insert_text((170, y), "ECHO East Coast Transportation", fontsize=11)
    y += 18
    page.insert_text((195, y), "VEHICLE PREP REPORT", fontsize=14)
    y += 20
    page.insert_text((40, y), "08/31/2026", fontsize=10)
    page.insert_text((200, y), "MONDAY", fontsize=10)
    y += 25

    # Build table rows: Vehicle col has "unit-\nlocation" format
    table_rows = []
    for (prep_time, unit, loc, vtype, stype, trips) in ROWS:
        table_rows.append([prep_time, f"{unit}-\n{loc}", vtype, stype, trips])

    _draw_table(page, 40, y, HEADERS, table_rows, COL_WIDTHS, ROW_H)
    doc.save(path)
    print(f"Wrote {path} ({len(ROWS)} vehicles, ECHO format)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sample_prep_report.pdf"
    build(out)
