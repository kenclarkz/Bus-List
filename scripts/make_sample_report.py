"""Generate sample pre-formatted prep report PDFs for testing the import.

Usage:
    python scripts/make_sample_report.py [output.pdf]           # ECHO prep report
    python scripts/make_sample_report.py --wash [output.pdf]    # Vehicle Wash Report

Generates a PDF in the ECHO East Coast Transportation prep report format.
Optionally includes a 'Replace X with Y' style substitution row.
"""
import os
import sys

try:
    import fitz
except ImportError:
    sys.exit("PyMuPDF required: pip install -r requirements.txt")


# ECHO-format data: (prep_time, unit, location_code, vehicle_type, service_type,
# trips, option_note)
ROWS = [
    ("01:45", "9205", "JAXSUV", "SUVSUB", "Departure", "4", "Check AC before dispatch"),
    ("02:00", "9203", "JAXSUV", "SUVSUB", "As Directed", "1", ""),
    ("04:00", "4301", "JAXUNF", "TRANSITB", "Shuttle", "2", "Wipe windshield"),
    ("04:00", "4303", "JAXUNF", "TRANSITB", "Shuttle", "2", ""),
    ("04:00", "4304", "JAXUNF", "TRANSITB", "Shuttle", "2", ""),
    ("04:15", "7101", "JAXMINIC", "MINIC34", "Transfer", "2", "Low tire - air up"),
    ("04:30", "5100", "JAXUNF", "ADAMINIVAN", "Shuttle", "2", ""),
    ("06:00", "4302", "JAXUNF", "TRANSITB", "Shuttle", "2", ""),
    ("07:00", "8406", "JAXMC", "MOTORC", "Hourly", "1", ""),
    ("09:30", "9421", "JAXMB", "MINIBUS", "Hourly", "1", ""),
    ("09:30", "9440", "JAXMB", "MINIC40", "Hourly", "2", ""),
    ("09:45", "9331", "JAXVAN", "Van", "Hourly", "1", "Refill hand sanitizer"),
    ("10:00", "8437", "JAXADAMC", "ADAMOTORC", "Hourly", "1", ""),
    ("12:14", "9205", "JAXSUV", "SUVSUB", "Airport Arrival", "4", ""),
    ("13:52", "9233", "JAXSUV", "SUVYUKON", "Airport Arrival", "2", ""),
    ("15:00", "4302", "JAXUNF", "TRANSITB", "Shuttle", "2", ""),
]

HEADERS = ["Prep Time", "Vehicle", "Vehicle Type", "Type", "Trips #", "Option"]
COL_WIDTHS = [60, 90, 80, 90, 45, 120]
ROW_H = 30  # tall enough for 2-line Vehicle cells


# Vehicle Wash Report format data: (report_time, pickup_time, unit, location,
# vehicle_type, order_type, driver_code, reservation)
WASH_ROWS = [
    ("04:30", "05:00", "9101", "JAXSDN", "SEDAN", "As Directed", "291486*50", "295185*1"),
    ("05:00", "06:30", "9301", "JAXVAN", "Van.", "Shuttle", "290777*7", "290777*7"),
    ("05:00", "07:15", "7101", "JAXMINIC", "MINIC34", "Transfer", "LEOJEREZ", "295185*1"),
    ("05:30", "07:30", "9417", "JAXMB", "MINIBUS", "Hourly", "LAVERNEBELLAMY", "291281*2"),
    ("06:30", "08:30", "9440", "JAXMB", "MINIC40", "Hourly", "ARNALDOTORRES", "293194*1"),
    ("06:30", "08:30", "8492", "JAXMC", "MOTORC", "Hourly", "JEREMYHAWYER", "293194*2"),
    ("06:30", "07:00", "4301", "JAXUNF", "TRANSITB", "Shuttle", "JAMESMILNER", "294178*1"),
    ("06:30", "07:00", "4304", "JAXUNF", "TRANSITB", "Shuttle", "CATHERINEMARK", "294178*2"),
    ("06:30", "07:00", "4303", "JAXUNF", "TRANSITB", "Shuttle", "MONIQUEMYERS", "294178*3"),
    ("06:45", "09:00", "8438", "JAXADAMC", "ADAMOTORC", "Hourly", "COURTNEYGRIFFIN", "295372*1"),
    ("06:45", "09:00", "8493", "JAXMC", "MOTORC", "Hourly", "JEFFERYMATHIS", "295372*2"),
    ("07:30", "08:00", "9999", "", "COORDINATOR", "Shuttle", "THOMASFRAZIER", "294159*1"),
    ("07:45", "09:00", "9203", "JAXSUV", "SUVSUB", "Departure", "", "295861*1"),
    ("07:52", "09:07", "9204", "JAXSUV", "SUVSUB", "Airport Arrival", "", "296620*1"),
    ("08:30", "09:00", "4302", "JAXUNF", "TRANSITB", "Shuttle", "CATHERINEHART", "294178*5"),
    ("09:30", "09:30", "5308", "JAXUNF", "ADAMINIBUS", "As Directed", "", "292713*1"),
    ("10:15", "12:30", "9422", "JAXMB", "MINIBUS", "Hourly", "RAHDEEMJONES", "294405*6"),
    ("15:30", "16:00", "9341", "JAXMARRIOT", "Van.", "Shuttle", "MIKESTARKS", "294712*2"),
    ("16:00", "17:30", "9336", "JAXVAN", "Van.", "Shuttle", "", "293541*3"),
    ("16:00", "17:30", "9421", "JAXMB", "MINIBUS", "Shuttle", "LESLIEKING", "293541*6"),
    ("21:30", "21:30", "9321", "JAXEXEC", "VANSPRINTEREXEC", "Transfer", "", "296380*1"),
]

WASH_HEADERS = ["Report Time", "Pickup Time", "Vehicle Code+Type", "Order Type",
                "Driver Code", "Reservation #"]
WASH_COL_WIDTHS = [60, 60, 130, 75, 130, 80]


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
                # ECHO Vehicle cell: unit on line 1, location on line 2
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
    for (prep_time, unit, loc, vtype, stype, trips, option) in ROWS:
        table_rows.append([prep_time, f"{unit}-\n{loc}", vtype, stype, trips,
                           option])

    _draw_table(page, 40, y, HEADERS, table_rows, COL_WIDTHS, ROW_H)
    doc.save(path)
    print(f"Wrote {path} ({len(ROWS)} vehicles, ECHO format)")


def build_wash(path):
    doc = fitz.open()
    page = doc.new_page(width=792, height=612)
    y = 50

    # Title
    page.insert_text((170, y), "ECHO East Coast Transportation", fontsize=11)
    y += 18
    page.insert_text((205, y), "Vehicle Wash Report", fontsize=14)
    y += 20
    page.insert_text((40, y), "[From Date: 09/16/2026 00:00   To Date: 09/16/2026 23:59]",
                     fontsize=9)
    y += 20

    # Vehicle Code+Type cell: "unit-location [ TYPE ]" (or "unit [ TYPE ]")
    table_rows = []
    for (report_time, pickup_time, unit, loc, vtype, stype, driver, res) in WASH_ROWS:
        code = f"{unit}-{loc}" if loc else unit
        table_rows.append([report_time, pickup_time, f"{code} [ {vtype} ]",
                           stype, driver, res])

    _draw_table(page, 40, y, WASH_HEADERS, table_rows, WASH_COL_WIDTHS, ROW_H)
    doc.save(path)
    print(f"Wrote {path} ({len(WASH_ROWS)} vehicles, Vehicle Wash Report format)")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--wash"]
    wash = "--wash" in sys.argv[1:]
    out = args[0] if args else ("sample_vehicle_wash_report.pdf" if wash
                                else "sample_prep_report.pdf")
    if wash:
        build_wash(out)
    else:
        build(out)