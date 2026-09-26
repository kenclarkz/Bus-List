"""Tests for the Detailing Operations Dashboard."""
import io
import json
from datetime import date, datetime, timedelta

import pytest

from app import create_app
from app.models import db, Vehicle, Employee, ScheduleEntry, TaskCompletion, \
    Replacement, DailySchedule
from app.services.vehicles import find_or_create_vehicle
from app.services import timeutils
from app.services.pdf_parser import normalize_unit
from app.services.pdf_parser import parse_prep_report
from app.services import schedule as sched_svc


@pytest.fixture()
def app(tmp_path):
    db_path = tmp_path / "test.db"
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "SECRET_KEY": "test",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
    })
    with app.app_context():
        yield app


@pytest.fixture()
def client(app):
    # Default: signed in as the Employee account so existing route tests run
    # against the interactive board. Employees pick their name after login.
    c = app.test_client()
    c.post("/login", data={"username": "employee", "password": "employee"})
    with app.app_context():
        emp = Employee.query.filter_by(active=True).first()
        emp_id = str(emp.id) if emp else ""
    c.post("/select", data={"employee_id": emp_id})
    return c


@pytest.fixture()
def manager_client(app):
    c = app.test_client()
    c.post("/login", data={"username": "manager", "password": "manager"})
    return c


def seed_vehicle(app, unit="142", vtype="Coach", route="R12"):
    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        return find_or_create_vehicle(unit, vehicle_type=vtype, route=route)[0]


# ---------------------------------------------------------------------------
# Parser / normalization
# ---------------------------------------------------------------------------

def test_normalize_unit_variants():
    assert normalize_unit("BUS 142") == "142"
    assert normalize_unit("Unit 142") == "142"
    assert normalize_unit("142") == "142"
    assert normalize_unit("veh 155") == "155"
    assert normalize_unit(None) is None


def test_normalize_unit_trailing_dash():
    from app.services.pdf_parser import normalize_unit
    assert normalize_unit("9205-") == "9205"
    assert normalize_unit("4301-") == "4301"
    assert normalize_unit("187") == "187"


def test_normalize_type_preserves_uppercase_codes():
    from app.services.pdf_parser import normalize_type
    assert normalize_type("SUVSUB") == "SUVSUB"
    assert normalize_type("TRANSITB") == "TRANSITB"
    assert normalize_type("MINIBUS") == "MINIBUS"
    assert normalize_type("Van") == "Van"
    assert normalize_type("Shuttle") == "Shuttle"
    assert normalize_type(None) is None
    assert normalize_type("") is None


def test_text_pdf_parsing(app):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Unit   Type   Route")
    page.insert_text((72, 100), "142    Coach  R12")
    page.insert_text((72, 130), "155    Van    R3")
    buf = doc.tobytes()
    parsed, method, warnings = parse_prep_report(io.BytesIO(buf).read())
    assert "142" in parsed
    assert "155" in parsed
    assert method == "text"


def test_echo_format_parsing():
    """Test ECHO prep report format with multi-line Vehicle cells."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    # Draw bordered table in ECHO format
    headers = ["Prep Time", "Vehicle", "Vehicle Type", "Type", "Trips #",
               "Option"]
    col_widths = [60, 90, 80, 90, 45, 120]
    row_h = 30
    x0, y0 = 40, 60

    data_rows = [
        ["01:45", "9205-\nJAXSUV", "SUVSUB", "Departure", "4", "Check AC before dispatch"],
        ["02:00", "9203-\nJAXSUV", "SUVSUB", "As Directed", "1", ""],
        ["04:00", "4301-\nJAXUNF", "TRANSITB", "Shuttle", "2", "Wipe windshield"],
        ["04:15", "7101-\nJAXMINIC", "MINIC34", "Transfer", "2", ""],
        ["09:45", "9331-\nJAXVAN", "Van", "Hourly", "1", "Refill hand sanitizer"],
    ]

    # Draw header row
    all_rows = [headers] + data_rows
    for ri, row in enumerate(all_rows):
        ry = y0 + ri * row_h
        cx = x0
        for ci, (cell, w) in enumerate(zip(row, col_widths)):
            shape = page.new_shape()
            shape.draw_rect(fitz.Rect(cx, ry, cx + w, ry + row_h))
            shape.finish(color=(0, 0, 0))
            shape.commit()
            if ci == 1 and "\n" in cell:
                lines = cell.split("\n", 1)
                page.insert_text((cx + 3, ry + 14), lines[0], fontsize=8)
                page.insert_text((cx + 3, ry + 24), lines[1], fontsize=7)
            else:
                page.insert_text((cx + 3, ry + 14), cell, fontsize=8)
            cx += w

    buf = doc.tobytes()
    parsed, method, warnings = parse_prep_report(buf)

    assert len(parsed) == 5
    assert "9205" in parsed
    assert parsed["9205"].type == "SUVSUB"
    assert parsed["9205"].route == "Departure"
    assert "9203" in parsed
    assert parsed["9203"].type == "SUVSUB"
    assert parsed["9203"].route == "As Directed"
    assert "4301" in parsed
    assert parsed["4301"].type == "TRANSITB"
    assert parsed["4301"].route == "Shuttle"
    assert "7101" in parsed
    assert parsed["7101"].type == "MINIC34"
    assert parsed["7101"].route == "Transfer"
    assert "9331" in parsed
    assert parsed["9331"].type == "Van"
    assert parsed["9331"].route == "Hourly"

    assert parsed["9205"].prep_time == "01:45"
    assert parsed["4301"].prep_time == "04:00"

    # Option column becomes the vehicle note
    assert parsed["9205"].notes == "Check AC before dispatch"
    assert parsed["4301"].notes == "Wipe windshield"
    assert parsed["9331"].notes == "Refill hand sanitizer"
    assert parsed["9203"].notes is None
    assert parsed["7101"].notes is None


def _make_wash_report_pdf(rows):
    """Build a Vehicle Wash Report format PDF using a bordered table.

    rows: list of (report_time, pickup_time, unit, loc, vtype, order_type,
                   driver_code, reservation)
    """
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=792, height=612)
    headers = ["Report Time", "Pickup Time", "Vehicle Code+Type", "Order Type",
               "Driver Code", "Reservation #"]
    col_widths = [60, 60, 130, 75, 130, 80]
    row_h = 28
    x0, y0 = 40, 60

    all_rows = [headers]
    for (rpt, pkup, unit, loc, vtype, stype, driver, res) in rows:
        code = f"{unit}-{loc}" if loc else unit
        all_rows.append([rpt, pkup, f"{code} [ {vtype} ]", stype, driver, res])

    for ri, row in enumerate(all_rows):
        ry = y0 + ri * row_h
        cx = x0
        for ci, (cell, w) in enumerate(zip(row, col_widths)):
            shape = page.new_shape()
            shape.draw_rect(fitz.Rect(cx, ry, cx + w, ry + row_h))
            shape.finish(color=(0, 0, 0))
            shape.commit()
            page.insert_text((cx + 3, ry + 14), cell, fontsize=8)
            cx += w
    return doc.tobytes()


def test_wash_format_parsing():
    """The Vehicle Wash Report format is parsed into unit/type/route plus the
    report time, pickup time and driver code columns."""
    data = _make_wash_report_pdf([
        ("04:30", "05:00", "9101", "JAXSDN", "SEDAN", "As Directed",
         "291486*50", "295185*1"),
        ("05:30", "07:30", "9417", "JAXMB", "MINIBUS", "Hourly",
         "LAVERNEBELLAMY", "291281*2"),
        ("09:30", "09:30", "5308", "JAXUNF", "ADAMINIBUS", "As Directed",
         "", "292713*1"),
    ])
    parsed, method, warnings = parse_prep_report(data, "wash.pdf")

    assert method == "text"
    assert not warnings
    assert len(parsed) == 3

    v = parsed["9101"]
    assert v.type == "SEDAN"
    assert v.route == "As Directed"
    assert v.prep_time == "04:30"
    assert v.pickup_time == "05:00"
    assert v.driver_code == "291486*50"
    assert v.notes == "Res # 295185*1"

    v = parsed["9417"]
    assert v.type == "MINIBUS"
    assert v.route == "Hourly"
    assert v.prep_time == "05:30"
    assert v.pickup_time == "07:30"
    assert v.driver_code == "LAVERNEBELLAMY"

    v = parsed["5308"]
    assert v.pickup_time == "09:30"
    assert v.driver_code is None


def test_wash_import_end_to_end_and_dashboard(client, app):
    """Importing a Vehicle Wash Report stores report time / pickup time /
    driver code on the board and the dashboard shows them."""
    data = _make_wash_report_pdf([
        ("04:30", "05:00", "9101", "JAXSDN", "SEDAN", "As Directed",
         "291486*50", "295185*1"),
        ("05:00", "07:15", "7101", "JAXMINIC", "MINIC34", "Transfer",
         "LEOJEREZ", "295185*1"),
    ])

    r = client.post("/import", data={
        "pdf": (io.BytesIO(data), "wash_report.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    assert b"Import Preview" in r.data
    assert b"291486*50" in r.data
    assert b"LEOJEREZ" in r.data

    with app.app_context():
        from app.models import PrepReportImport
        iid = PrepReportImport.query.first().id

    r = client.post(f"/import/{iid}/apply")
    assert r.status_code == 302

    with app.app_context():
        sched = DailySchedule.query.filter_by(work_date=date.today()).first()
        assert sched is not None
        fields = {}
        for e in sched.entries:
            v = Vehicle.query.get(e.vehicle_id)
            fields[v.unit_number] = (e.prep_time, e.pickup_time, e.driver_code)
        assert fields.get("9101") == ("04:30", "05:00", "291486*50")
        assert fields.get("7101") == ("05:00", "07:15", "LEOJEREZ")

    html = client.get("/").data.decode()
    # The report's 24-hour times are displayed as Eastern 12-hour AM/PM...
    assert "Report 4:30 AM" in html
    assert "Pickup 5:00 AM" in html
    assert "Driver 291486*50" in html
    assert "Report 5:00 AM" in html
    assert "Pickup 7:15 AM" in html
    assert "Driver LEOJEREZ" in html
    # ...while the stored value keeps the exact timestamp from the report.
    with app.app_context():
        entry = (ScheduleEntry.query
                 .filter(ScheduleEntry.prep_time == "04:30").first())
        assert entry is not None
        assert entry.pickup_time == "05:00"
    assert "Res # 295185*1" not in html


def test_sample_vehicle_wash_report_in_repo():
    """The repo-shipped Vehicle Wash Report template parses cleanly."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "sample_vehicle_wash_report.pdf")
    assert os.path.isfile(path)
    with open(path, "rb") as f:
        parsed, method, warnings = parse_prep_report(f.read(),
                                                     "sample_vehicle_wash_report.pdf")
    assert method == "text"
    assert not warnings
    assert len(parsed) >= 30
    v = parsed["9101"]
    assert v.prep_time == "04:30"
    assert v.pickup_time == "05:00"
    assert v.notes == "Res # 291486*50"
    v = parsed["7101"]
    assert v.prep_time == "05:00"
    assert v.pickup_time == "07:15"
    assert v.driver_code == "LEOJEREZ"


# ---------------------------------------------------------------------------
# OCR fallback (scanned PDFs with no selectable text)
# ---------------------------------------------------------------------------

class _FakeTesseractNotFoundError(Exception):
    pass


def _fake_pytesseract_module(result=None, exc=None):
    import types
    mod = types.ModuleType("pytesseract")
    mod.TesseractNotFoundError = _FakeTesseractNotFoundError

    def image_to_string(img):
        if exc is not None:
            raise exc
        return result or ""

    mod.image_to_string = image_to_string
    return mod


def _fake_pil_module():
    import types

    class _Img:
        def convert(self, *a, **k):
            return self

    class _Image:
        @staticmethod
        def open(fp):
            return _Img()

        @staticmethod
        def new(*a, **k):
            return _Img()

    mod = types.ModuleType("PIL")
    mod.Image = _Image
    return mod


def _blank_scanned_pdf():
    """A PDF page with zero selectable text (simulates a scan)."""
    import fitz
    doc = fitz.open()
    doc.new_page()
    return doc.tobytes()


def test_scanned_pdf_without_ocr_libraries_warns(monkeypatch):
    """When OCR libraries are missing, warn with install guidance instead of
    silently failing (the reported bug)."""
    monkeypatch.setitem(__import__("sys").modules, "pytesseract", None)
    monkeypatch.setitem(__import__("sys").modules, "PIL", None)

    parsed, method, warnings = parse_prep_report(_blank_scanned_pdf(), "scan.pdf")

    assert method == "ocr"
    assert parsed == {}
    assert any("tesseract" in w.lower() or "pip install" in w.lower()
               for w in warnings)


def test_scanned_pdf_ocr_extracts_vehicles(monkeypatch):
    """With OCR available, a scanned PDF is read and vehicles are extracted,
    flagged as uncertain for manual review."""
    monkeypatch.setitem(
        __import__("sys").modules,
        "pytesseract",
        _fake_pytesseract_module(
            result="BUS  142  Coach  R12\n"
                   "Unit 155  Van   R3\n"
                   "Header text no vehicle\n"
        ),
    )
    monkeypatch.setitem(__import__("sys").modules, "PIL", _fake_pil_module())

    parsed, method, warnings = parse_prep_report(_blank_scanned_pdf(), "scan.pdf")

    assert method == "ocr"
    assert "142" in parsed
    assert parsed["142"].type == "Coach"
    assert parsed["142"].route == "R12"
    assert parsed["142"].uncertain is True
    assert "155" in parsed


def test_scanned_pdf_without_tesseract_binary_warns(monkeypatch):
    """If pytesseract is present but the tesseract-ocr binary is missing, call
    out the missing system tool."""
    monkeypatch.setitem(
        __import__("sys").modules,
        "pytesseract",
        _fake_pytesseract_module(exc=_FakeTesseractNotFoundError("not found")),
    )
    monkeypatch.setitem(__import__("sys").modules, "PIL", _fake_pil_module())

    parsed, method, warnings = parse_prep_report(_blank_scanned_pdf(), "scan.pdf")

    assert method == "ocr"
    assert parsed == {}
    assert any("tesseract-ocr" in w for w in warnings)


# ---------------------------------------------------------------------------
# Import preview / apply
# ---------------------------------------------------------------------------

def test_import_apply_end_to_end(client, app):
    # Build a PDF with units
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Unit  Type  Route")
    page.insert_text((72, 100), "100   Coach  R1")
    page.insert_text((72, 130), "200   Van    R2")
    data = doc.tobytes()

    r = client.post("/import", data={
        "pdf": (io.BytesIO(data), "prep.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    assert b"Import Preview" in r.data

    # find the import id in the page and apply
    with app.app_context():
        from app.models import PrepReportImport
        imp = PrepReportImport.query.first()
        iid = imp.id

    r = client.post(f"/import/{iid}/apply")
    assert r.status_code == 302

    with app.app_context():
        assert Vehicle.query.filter_by(unit_number="100").first() is not None
        assert Vehicle.query.filter_by(unit_number="200").first() is not None
        sched = DailySchedule.query.filter_by(work_date=date.today()).first()
        assert sched is not None
        assert len(sched.entries) == 2
        entry = sched.entries[0]
        assert len(entry.tasks) == 8


def test_import_detects_substitution(app):
    """A 'Replace X' style row is surfaced in the import preview."""
    from app.services.pdf_parser import parse_prep_report
    from app.services import schedule as ss
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Unit  Type  Route")
    page.insert_text((72, 100), "142   Coach  Downtown")
    page.insert_text((72, 130), "190   Van    Replace 155")
    data = doc.tobytes()

    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        loc = vehicles_loc(app)
        find_or_create_vehicle("142", vehicle_type="Coach", route="Downtown",
                               location_id=loc.id)
        find_or_create_vehicle("155", vehicle_type="Van", location_id=loc.id)

        parsed, _, _ = parse_prep_report(data, "p.pdf")
        preview = ss.build_preview(parsed, location=loc)
        assert any(r["original"] == "190" for r in preview["replacements"])
        assert any(r["original"] == "142" for r in preview["updated"]) or \
            any(r["unit"] == "142" for r in preview["unchanged"])


def test_import_echo_format_end_to_end(client, app):
    """Import an ECHO-format PDF through the full import flow."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    headers = ["Prep Time", "Vehicle", "Vehicle Type", "Type", "Trips #"]
    col_widths = [60, 90, 80, 90, 45]
    row_h = 30
    x0, y0 = 40, 60

    data_rows = [
        ["01:45", "100-\nJAXUNF", "TRANSITB", "Shuttle", "2"],
        ["02:00", "200-\nJAXSUV", "SUVSUB", "Hourly", "1"],
    ]

    all_rows = [headers] + data_rows
    for ri, row in enumerate(all_rows):
        ry = y0 + ri * row_h
        cx = x0
        for ci, (cell, w) in enumerate(zip(row, col_widths)):
            shape = page.new_shape()
            shape.draw_rect(fitz.Rect(cx, ry, cx + w, ry + row_h))
            shape.finish(color=(0, 0, 0))
            shape.commit()
            if ci == 1 and "\n" in cell:
                lines = cell.split("\n", 1)
                page.insert_text((cx + 3, ry + 14), lines[0], fontsize=8)
                page.insert_text((cx + 3, ry + 24), lines[1], fontsize=7)
            else:
                page.insert_text((cx + 3, ry + 14), cell, fontsize=8)
            cx += w

    data = doc.tobytes()

    r = client.post("/import", data={
        "pdf": (io.BytesIO(data), "echo_prep.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    assert b"Import Preview" in r.data
    assert b"TRANSITB" in r.data
    assert b"SUVSUB" in r.data

    # Apply the import
    with app.app_context():
        from app.models import PrepReportImport
        imp = PrepReportImport.query.first()
        iid = imp.id

    r = client.post(f"/import/{iid}/apply")
    assert r.status_code == 302

    with app.app_context():
        assert Vehicle.query.filter_by(unit_number="100").first() is not None
        assert Vehicle.query.filter_by(unit_number="200").first() is not None
        sched = DailySchedule.query.filter_by(work_date=date.today()).first()
        assert sched is not None
        assert len(sched.entries) == 2
        prep_times = {}
        for e in sched.entries:
            v = Vehicle.query.get(e.vehicle_id)
            prep_times[v.unit_number] = e.prep_time
        assert prep_times.get("100") == "01:45"
        assert prep_times.get("200") == "02:00"


def test_import_echo_format_notes_on_dashboard(client, app):
    """The ECHO report's Option column is stored as a note on the vehicle and
    shown on the dashboard."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    headers = ["Prep Time", "Vehicle", "Vehicle Type", "Type", "Trips #",
               "Option"]
    col_widths = [60, 90, 80, 90, 45, 120]
    row_h = 30
    x0, y0 = 40, 60

    data_rows = [
        ["01:45", "100-\nJAXUNF", "TRANSITB", "Shuttle", "2", "Check AC before dispatch"],
        ["02:00", "200-\nJAXSUV", "SUVSUB", "Hourly", "1", ""],
    ]

    all_rows = [headers] + data_rows
    for ri, row in enumerate(all_rows):
        ry = y0 + ri * row_h
        cx = x0
        for ci, (cell, w) in enumerate(zip(row, col_widths)):
            shape = page.new_shape()
            shape.draw_rect(fitz.Rect(cx, ry, cx + w, ry + row_h))
            shape.finish(color=(0, 0, 0))
            shape.commit()
            if ci == 1 and "\n" in cell:
                lines = cell.split("\n", 1)
                page.insert_text((cx + 3, ry + 14), lines[0], fontsize=8)
                page.insert_text((cx + 3, ry + 24), lines[1], fontsize=7)
            else:
                page.insert_text((cx + 3, ry + 14), cell, fontsize=8)
            cx += w

    data = doc.tobytes()

    r = client.post("/import", data={
        "pdf": (io.BytesIO(data), "echo_notes.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    assert b"Import Preview" in r.data
    assert b"Check AC before dispatch" in r.data

    with app.app_context():
        from app.models import PrepReportImport
        imp = PrepReportImport.query.first()
        iid = imp.id

    r = client.post(f"/import/{iid}/apply")
    assert r.status_code == 302

    with app.app_context():
        v = Vehicle.query.filter_by(unit_number="100").first()
        assert v is not None
        assert v.notes == "Check AC before dispatch"
        v2 = Vehicle.query.filter_by(unit_number="200").first()
        assert v2.notes is None

    # Dashboard shows the note for the vehicle that had one
    html = client.get("/").data.decode()
    assert "Check AC before dispatch" in html


def test_import_delete_removes_imported_vehicles(client, app):
    """Deleting a prep report import also removes the vehicles it introduced."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Unit  Type  Route")
    page.insert_text((72, 100), "610   Coach  R1")
    page.insert_text((72, 130), "620   Van    R2")
    data = doc.tobytes()

    r = client.post("/import", data={
        "pdf": (io.BytesIO(data), "prep2.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200

    with app.app_context():
        from app.models import PrepReportImport
        imp = PrepReportImport.query.first()
        iid = imp.id

    r = client.post(f"/import/{iid}/apply")
    assert r.status_code == 302

    with app.app_context():
        assert Vehicle.query.filter_by(unit_number="610").first() is not None
        assert Vehicle.query.filter_by(unit_number="620").first() is not None

    r = client.post(f"/import/{iid}/delete")
    assert r.status_code == 302

    with app.app_context():
        from app.models import PrepReportImport, ScheduleEntry
        assert PrepReportImport.query.get(iid) is None
        assert Vehicle.query.filter_by(unit_number="610").first() is None
        assert Vehicle.query.filter_by(unit_number="620").first() is None
        assert ScheduleEntry.query.filter_by(vehicle_id=0).count() == 0


def test_imported_pdf_saved_and_viewable(client, app):
    """Uploaded prep report PDFs are saved to disk and viewable afterwards."""
    import os
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Unit  Type  Route")
    page.insert_text((72, 100), "710   Coach  R1")
    data = doc.tobytes()

    r = client.post("/import", data={
        "pdf": (io.BytesIO(data), "viewme.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200

    with app.app_context():
        from app.models import PrepReportImport
        imp = PrepReportImport.query.first()
        assert imp.file_path and os.path.isfile(imp.file_path)
        saved_path = imp.file_path
        saved_bytes = open(saved_path, "rb").read()
        assert saved_bytes == data

    # The viewer page embeds the original PDF and offers a way back.
    r = client.get(f"/import/{imp.id}/view")
    assert r.status_code == 200
    assert r.headers["Content-Type"] != "application/pdf"
    assert f"/import/{imp.id}/pdf".encode() in r.data

    # The original PDF is served back identically.
    r = client.get(f"/import/{imp.id}/pdf")
    assert r.status_code == 200
    assert r.data == data
    assert r.headers["Content-Type"] == "application/pdf"

    # History page links to the original PDF.
    r = client.get("/history")
    assert b"View PDF" in r.data
    assert f"/import/{imp.id}/view".encode() in r.data

    # Deleting the import removes the saved file.
    r = client.post(f"/import/{imp.id}/delete")
    assert r.status_code == 302
    assert not os.path.isfile(saved_path)


def test_delete_import_clears_employee_current_vehicle(client, app):
    """Deleting an import must clear the 'currently working' vehicle for any
    employee who had checked tasks on that day's board."""
    import fitz
    from app.models import Employee

    with app.app_context():
        emp = Employee(name="Jane Smith")
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Unit  Type  Route")
    page.insert_text((72, 100), "710   Coach  R1")
    data = doc.tobytes()

    r = client.post("/import", data={
        "pdf": (io.BytesIO(data), "prep3.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200

    with app.app_context():
        from app.models import PrepReportImport
        imp = PrepReportImport.query.first()
        iid = imp.id

    r = client.post(f"/import/{iid}/apply")
    assert r.status_code == 302

    # Find the entry and check a task as the employee
    with app.app_context():
        from app.models import PrepReportImport
        v = Vehicle.query.filter_by(unit_number="710").first()
        assert v is not None
        entry = ScheduleEntry.query.filter_by(vehicle_id=v.id).first()
        assert entry is not None
        eid = entry.id

    r = client.post(f"/task/{eid}/Sweep",
                    data={"checked": "true", "employee_id": str(emp_id)})
    assert r.status_code == 200

    with app.app_context():
        emp = Employee.query.get(emp_id)
        assert emp.current_vehicle_id is not None
        assert Vehicle.query.get(emp.current_vehicle_id).unit_number == "710"

    r = client.post(f"/import/{iid}/delete")
    assert r.status_code == 302

    with app.app_context():
        emp = Employee.query.get(emp_id)
        assert emp.current_vehicle_id is None


def test_delete_import_with_replaced_vehicle(client, app):
    """Deleting an import whose vehicles were involved in a replacement must
    not 500 on the NOT NULL replacement foreign keys (regression fix)."""
    import fitz
    from app.models import PrepReportImport, Replacement

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Unit  Type  Route")
    page.insert_text((72, 100), "910   Coach  R1")
    data = doc.tobytes()

    r = client.post("/import", data={
        "pdf": (io.BytesIO(data), "prep_replaced.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200

    with app.app_context():
        iid = PrepReportImport.query.first().id

    r = client.post(f"/import/{iid}/apply")
    assert r.status_code == 302

    # Replace the freshly-imported vehicle so a Replacement row references it.
    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        find_or_create_vehicle("912")
        v = Vehicle.query.filter_by(unit_number="910").first()
        vid = v.id
        entry = ScheduleEntry.query.filter_by(vehicle_id=v.id).first()
        eid = entry.id

    r = client.post(f"/schedule/{eid}/replace", data={
        "replacement_unit": "912",
        "reason": "down for service",
    })
    assert r.status_code == 302

    with app.app_context():
        assert Replacement.query.filter(
            db.or_(Replacement.original_vehicle_id == vid,
                   Replacement.replacement_vehicle_id == vid)).count() == 1

    # Deleting the import must succeed and detach the replacement rows.
    r = client.post(f"/import/{iid}/delete")
    assert r.status_code == 302

    with app.app_context():
        assert PrepReportImport.query.get(iid) is None
        assert Vehicle.query.filter_by(unit_number="910").first() is None
        assert not Replacement.query.filter(
            db.or_(Replacement.original_vehicle_id == vid,
                   Replacement.replacement_vehicle_id == vid)).all()
        # The non-imported replacement vehicle survives.
        assert Vehicle.query.filter_by(unit_number="912").first() is not None


def test_today_board_tracks_import(client, app):
    """Today's total is 0 until a prep report is imported and applied, and
    resets to 0 once the import is deleted (matches the separate Vehicles tab,
    which always lists all vehicles)."""
    import fitz
    import re

    def today_total():
        html = client.get("/").data.decode()
        m = re.search(
            r'<div class="num">(\d+)</div><div class="lbl">Total Vehicles',
            html)
        return int(m.group(1)) if m else None

    # No import yet -> Today shows zero
    assert today_total() == 0

    # Build and upload a prep report with two vehicles
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Unit  Type  Route")
    page.insert_text((72, 100), "710   Coach  R1")
    page.insert_text((72, 130), "720   Van    R2")
    data = doc.tobytes()

    r = client.post("/import", data={
        "pdf": (io.BytesIO(data), "prep3.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200

    with app.app_context():
        from app.models import PrepReportImport
        iid = PrepReportImport.query.first().id

    r = client.post(f"/import/{iid}/apply")
    assert r.status_code == 302
    assert today_total() == 2

    # Delete the import -> Today resets to zero
    r = client.post(f"/import/{iid}/delete")
    assert r.status_code == 302
    assert today_total() == 0


# ---------------------------------------------------------------------------
# Delete previous day's work
# ---------------------------------------------------------------------------

def test_delete_previous_day_removes_schedule_entries_and_tasks(client, app):
    """Deleting a previous day removes its schedule, entries and tasks but
    keeps the vehicle records and service history."""
    from datetime import timedelta
    past_day = date.today() - timedelta(days=1)
    with app.app_context():
        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(d=past_day, location=loc)
        v, _ = find_or_create_vehicle("301", location_id=loc.id)
        entry = ss.ensure_entry(sched, v)
        ss.toggle_task(entry.id, "Sweep", True)
        sched.finalized = True
        sched.summary = json.dumps(
            dict(total=1, completed=1, incomplete=0, overall=100))
        db.session.commit()
        sched_id = sched.id
        entry_id = entry.id
        v_id = v.id
        assert DailySchedule.query.get(sched_id) is not None
        assert ScheduleEntry.query.get(entry_id) is not None
        assert TaskCompletion.query.filter_by(entry_id=entry_id).count() > 0

    r = client.post(f"/schedule/{sched_id}/delete")
    assert r.status_code == 302

    with app.app_context():
        assert DailySchedule.query.get(sched_id) is None
        assert ScheduleEntry.query.get(entry_id) is None
        assert TaskCompletion.query.filter_by(entry_id=entry_id).count() == 0
        # The vehicle itself survives deletion of the day's work.
        assert Vehicle.query.get(v_id) is not None


def test_delete_schedule_clears_employee_current_vehicle(client, app):
    """Deleting a previous day frees employees who were working its vehicles."""
    from datetime import timedelta
    past_day = date.today() - timedelta(days=2)
    with app.app_context():
        from app.models import Employee
        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        loc = vehicles_loc(app)
        emp = Employee(name="Jane Smith")
        db.session.add(emp)
        db.session.commit()
        sched = ss.get_or_create_schedule(d=past_day, location=loc)
        v, _ = find_or_create_vehicle("302", location_id=loc.id)
        entry = ss.ensure_entry(sched, v)
        ss.toggle_task(entry.id, "Sweep", True, employee_id=emp.id)
        # Simulate the employee still being assigned to this vehicle.
        emp.current_vehicle_id = v.id
        db.session.commit()
        sched_id = sched.id
        assert emp.current_vehicle_id == v.id

    r = client.post(f"/schedule/{sched_id}/delete")
    assert r.status_code == 302

    with app.app_context():
        from app.models import Employee
        assert Employee.query.get(emp.id).current_vehicle_id is None


def test_cannot_delete_todays_schedule(client, app):
    """Today's board cannot be deleted so operators don't wipe live work."""
    from app.services import schedule as ss
    with app.app_context():
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        sched_id = sched.id

    r = client.post(f"/schedule/{sched_id}/delete")
    assert r.status_code == 302

    with app.app_context():
        assert DailySchedule.query.get(sched_id) is not None


# ---------------------------------------------------------------------------
# Manual add vehicle to board
# ---------------------------------------------------------------------------

def test_manual_add_new_vehicle_to_board(client, app):
    """A brand-new vehicle can be added to today's board manually (no import)."""
    r = client.post("/schedule/add", data={
        "unit_number": "555",
        "vehicle_type": "Coach",
        "route": "Downtown",
        "prep_time": "04:30",
    })
    assert r.status_code == 302
    with app.app_context():
        v = Vehicle.query.filter_by(unit_number="555").first()
        assert v is not None
        assert v.vehicle_type.name == "Coach"
        assert v.route == "Downtown"
        sched = DailySchedule.query.filter_by(work_date=date.today()).first()
        entry = ScheduleEntry.query.filter_by(vehicle_id=v.id).first()
        assert entry is not None
        assert entry.prep_time == "04:30"
        assert entry.status == "pending"
        assert len(entry.tasks) == 8


def test_manual_add_existing_vehicle_to_board(client, app):
    """Adding a vehicle that already exists reuses it instead of duplicating."""
    with app.app_context():
        v, _ = find_or_create_vehicle("640", vehicle_type="Van", route="R3",
                                      location_id=vehicles_loc(app).id)
        vid = v.id
    r = client.post("/schedule/add", data={"unit_number": "640"})
    assert r.status_code == 302
    with app.app_context():
        # Same vehicle, not a duplicate.
        assert Vehicle.query.filter_by(unit_number="640").count() == 1
        assert Vehicle.query.get(vid) is not None
        sched = DailySchedule.query.filter_by(work_date=date.today()).first()
        assert ScheduleEntry.query.filter_by(
            schedule_id=sched.id, vehicle_id=vid).first() is not None


def test_manual_add_requires_unit(client, app):
    """Adding without a unit number redirects and creates nothing."""
    r = client.post("/schedule/add", data={})
    assert r.status_code == 302
    with app.app_context():
        assert ScheduleEntry.query.count() == 0


def test_manual_add_vehicle_shows_on_dashboard_without_import(client, app):
    """A manually added vehicle is visible on the Today board even when no
    prep report has been imported (previously the board stayed empty)."""
    # No import -> board empty
    html = client.get("/").data.decode()
    assert 'class="num">0</div><div class="lbl">Total Vehicles' in html

    client.post("/schedule/add", data={"unit_number": "566"})

    html = client.get("/").data.decode()
    assert 'class="num">1</div><div class="lbl">Total Vehicles' in html
    assert "566" in html


def test_manual_add_vehicle_to_specific_date(client, app):
    """A vehicle can be added to tomorrow's board explicitly."""
    from datetime import timedelta
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    r = client.post("/schedule/add", data={
        "date": tomorrow,
        "unit_number": "577",
    })
    assert r.status_code == 302
    with app.app_context():
        sched = DailySchedule.query.filter_by(
            work_date=(date.today() + timedelta(days=1))).first()
        assert sched is not None
        v = Vehicle.query.filter_by(unit_number="577").first()
        assert ScheduleEntry.query.filter_by(
            schedule_id=sched.id, vehicle_id=v.id).first() is not None


# ---------------------------------------------------------------------------
# Checklist + progress
# ---------------------------------------------------------------------------

def test_checklist_toggle(client, app):
    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        from app.services import schedule as ss
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v, _ = find_or_create_vehicle("300", location_id=loc.id)
        entry = ss.ensure_entry(sched, v)
        entry_id = entry.id

    r = client.post(f"/task/{entry_id}/Sweep", data={"checked": "true"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["done"] == 1
    assert body["total"] == 8
    assert body["pct"] == 12

    with app.app_context():
        e = ScheduleEntry.query.get(entry_id)
        assert e.status == "in_progress"
        sweep = next(t for t in e.tasks if t.task_name == "Sweep")
        assert sweep.completed is True
        assert sweep.completed_at is not None
        assert v_last_washed(app, "300") is None


def test_wash_requires_all_outside_tasks(client, app):
    from app.models import ServiceRecord

    with app.app_context():
        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v, _ = find_or_create_vehicle("301", location_id=loc.id)
        entry = ss.ensure_entry(sched, v)
        entry_id = entry.id
        vehicle_id = v.id

    for task_name in ("Sweep", "Mop", "Windows", "Seats", "Bathroom",
                      "Dump", "Bay Checked"):
        response = client.post(f"/task/{entry_id}/{task_name}",
                               data={"checked": "true"})
        assert response.status_code == 200

    with app.app_context():
        vehicle = Vehicle.query.get(vehicle_id)
        assert vehicle.last_washed is None
        assert ServiceRecord.query.filter_by(
            vehicle_id=vehicle_id, service_type="wash").count() == 0

    response = client.post(f"/task/{entry_id}/Final%20Inspection",
                           data={"checked": "true"})
    assert response.status_code == 200

    with app.app_context():
        vehicle = Vehicle.query.get(vehicle_id)
        assert vehicle.last_washed is not None
        assert ServiceRecord.query.filter_by(
            vehicle_id=vehicle_id, service_type="wash").count() == 1

    client.post(f"/task/{entry_id}/Sweep", data={"checked": "true"})
    with app.app_context():
        assert ServiceRecord.query.filter_by(
            vehicle_id=vehicle_id, service_type="wash").count() == 1


def vehicles_loc(app):
    from app.services import vehicles
    return vehicles.default_location()


def v_last_washed(app, unit):
    v = Vehicle.query.filter_by(unit_number=unit).first()
    return v.last_washed


def sched_svc_entry_tasks(app, entry_id):
    from app.models import ScheduleEntry
    return ScheduleEntry.query.get(entry_id).tasks


# ---------------------------------------------------------------------------
# Replacement
# ---------------------------------------------------------------------------

def test_replacement(client, app):
    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        from app.services import schedule as ss
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v1, _ = find_or_create_vehicle("400", location_id=loc.id)
        v2, _ = find_or_create_vehicle("410", location_id=loc.id)
        v1_id, v2_id = v1.id, v2.id
        entry = ss.ensure_entry(sched, v1)
        entry_id = entry.id
        # complete a couple tasks on original
        ss.toggle_task(entry.id, "Sweep", True)
        ss.toggle_task(entry.id, "Mop", True)

    r = client.post(f"/schedule/{entry_id}/replace", data={
        "replacement_unit": "410",
        "reason": "down for service",
    })
    assert r.status_code == 302

    with app.app_context():
        rep = Replacement.query.first()
        assert rep is not None
        assert rep.original_vehicle.unit_number == "400"
        assert rep.replacement_vehicle.unit_number == "410"
        # replacement entry should carry completed tasks forward
        repl_entry = ScheduleEntry.query.filter_by(vehicle_id=v2_id).first()
        assert repl_entry.is_replacement is True
        done = [t for t in repl_entry.tasks if t.completed]
        assert len(done) == 2


def test_replacement_greys_out_original_and_links_units(client, app):
    """Replacing a vehicle greys out the original row and labels both sides:
    the replacement states which specific vehicle it replaced and the original
    shows which vehicle replaced it."""
    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        from app.services import schedule as ss
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v1, _ = find_or_create_vehicle("400", location_id=loc.id)
        v2, _ = find_or_create_vehicle("410", location_id=loc.id)
        entry = ss.ensure_entry(sched, v1)
        entry_id = entry.id
        orig_entry_id = entry.id

    r = client.post(f"/schedule/{entry_id}/replace", data={
        "replacement_unit": "410",
        "reason": "down for service",
    })
    assert r.status_code == 302

    html = client.get("/").data.decode()

    # The original vehicle row is greyed out and shows the replacer.
    assert "row-replaced" in html
    assert f'id="row-{orig_entry_id}"' in html
    assert "Replaced by 410" in html
    # The replacement vehicle states exactly which vehicle it replaced.
    assert "Replacement for 400" in html


def test_replaced_vehicle_counts_toward_completion(client, app):
    """A vehicle that gets replaced counts toward the day's completion: the
    original row contributes full progress and shows as completed even though
    work was moved to the replacement, so the day can reach 100%."""
    with app.app_context():
        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v1, _ = find_or_create_vehicle("860", location_id=loc.id)
        v2, _ = find_or_create_vehicle("870", location_id=loc.id)
        entry = ss.ensure_entry(sched, v1)
        entry_id = entry.id
        ss.toggle_task(entry.id, "Sweep", True)
        _, total, _ = ss.entry_progress(entry)
        assert total > 0

    r = client.post(f"/schedule/{entry_id}/replace", data={
        "replacement_unit": "870",
        "reason": "down for service",
    })
    assert r.status_code == 302

    with app.app_context():
        # The original row is treated as fully complete...
        from app.app import build_schedule_view
        view = build_schedule_view(sched_svc.get_or_create_schedule(location=vehicles_loc(app)))
        orig = next(r for r in view if r["vehicle"].unit_number == "860")
        assert orig["replaced_by"] is not None
        assert orig["is_complete"] is True
        assert orig["done"] == orig["total"]
        assert orig["pct"] == 100
        # ...while the replacement still tracks real progress from carried tasks.
        repl = next(r for r in view if r["vehicle"].unit_number == "870")
        assert repl["is_complete"] is False
        assert repl["done"] == 1

    # Dashboard counts the replaced vehicle toward completion.
    html = client.get("/").data.decode()
    assert "Replaced by 870" in html
    assert "Replacement for 860" in html


# ---------------------------------------------------------------------------
# End day / finalize
# ---------------------------------------------------------------------------

def test_end_day(client, app):
    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        from app.services import schedule as ss
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v, _ = find_or_create_vehicle("500", location_id=loc.id)
        entry = ss.ensure_entry(sched, v)
        ss.toggle_task(entry.id, "Sweep", True)

    r = client.get("/end")
    assert r.status_code == 200

    today = date.today().isoformat()
    r = client.post("/end", data={"date": today, "confirm": "yes"})
    assert r.status_code == 302
    with app.app_context():
        sched = DailySchedule.query.filter_by(work_date=date.today()).first()
        assert sched.finalized is True


def test_print_report(client, app):
    r = client.get(f"/print/{date.today().isoformat()}")
    assert r.status_code == 200


def test_auto_end_day_job_finalizes_open_today(app):
    """The 11:50 PM auto end-of-day job finalizes today's schedule when no
    employee ended it, and computes the same summary as the End My Day route."""
    from app.app import _auto_end_day_job
    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        loc = vehicles_loc(app)
        sched = sched_svc.get_or_create_schedule(location=loc)
        v, _ = find_or_create_vehicle("501", location_id=loc.id)
        sched_svc.ensure_entry(sched, v)
        assert sched.finalized is False

    count = _auto_end_day_job(app)
    assert count == 1

    with app.app_context():
        sched = DailySchedule.query.filter_by(work_date=date.today()).first()
        assert sched.finalized is True
        assert sched.finalized_at is not None
        summary = json.loads(sched.summary)
        assert summary["total"] == 1
        assert summary["completed"] == 0
        assert summary["incomplete"] == 1
        # Running again is a no-op: already finalized means nothing to do.
        assert _auto_end_day_job(app) == 0


def test_auto_end_day_job_spares_days_employees_ended(app):
    """A today schedule that an employee already finalized is left untouched."""
    from app.app import _auto_end_day_job, finalize_day
    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        loc = vehicles_loc(app)
        sched = sched_svc.get_or_create_schedule(location=loc)
        v, _ = find_or_create_vehicle("502", location_id=loc.id)
        sched_svc.ensure_entry(sched, v)
        finalize_day(sched, at=datetime.utcnow())
        finalized_at = sched.finalized_at

        assert _auto_end_day_job(app) == 0
        sched = DailySchedule.query.filter_by(work_date=date.today()).first()
        assert sched.finalized is True
        assert sched.finalized_at == finalized_at


def test_auto_end_day_job_ignores_past_and_future_days(app):
    """The job only closes today's open schedule; other days are untouched."""
    from app.app import _auto_end_day_job
    from datetime import timedelta
    with app.app_context():
        loc = vehicles_loc(app)
        yesterday = sched_svc.get_or_create_schedule(
            d=date.today() - timedelta(days=1), location=loc)
        tomorrow = sched_svc.get_or_create_schedule(
            d=date.today() + timedelta(days=1), location=loc)
        assert yesterday.finalized is False
        assert tomorrow.finalized is False

        assert _auto_end_day_job(app) == 0
        assert sched_svc.get_or_create_schedule(
            d=date.today() - timedelta(days=1), location=loc).finalized is False
        assert sched_svc.get_or_create_schedule(
            d=date.today() + timedelta(days=1), location=loc).finalized is False


def test_auto_end_day_time_helper_defaults_and_parses(monkeypatch):
    """AUTO_END_DAY_TIME controls the daily cutoff; invalid input falls back
    to the 11:50 PM default."""
    from app.app import _auto_end_time
    monkeypatch.delenv("AUTO_END_DAY_TIME", raising=False)
    assert _auto_end_time() == (23, 50)
    monkeypatch.setenv("AUTO_END_DAY_TIME", "11:50")
    assert _auto_end_time() == (11, 50)
    monkeypatch.setenv("AUTO_END_DAY_TIME", "not-a-time")
    assert _auto_end_time() == (23, 50)
    monkeypatch.setenv("AUTO_END_DAY_TIME", "27:99")
    assert _auto_end_time() == (23, 59)


def test_auto_end_day_scheduler_registers_daily_2350_job(app):
    """Starting the scheduler wires a job named auto_end_day on a cron trigger
    for the configured cutoff."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from app.app import _start_auto_end_day_scheduler

    scheduler = _start_auto_end_day_scheduler(app)
    try:
        job = scheduler.get_job("auto_end_day")
        assert job is not None
        assert isinstance(job.trigger, CronTrigger)
    finally:
        scheduler.shutdown(wait=False)


def test_auto_end_day_scheduler_graceful_without_apscheduler(monkeypatch, app):
    """The site must still boot when APScheduler isn't installed: the
    scheduler startup degrades (returns None) instead of crashing the app."""
    import sys

    import app.app as app_module

    for key in [k for k in sys.modules if k == "apscheduler"
                or k.startswith("apscheduler.")]:
        monkeypatch.setitem(sys.modules, key, None)
    with app.app_context():
        scheduler = app_module._start_auto_end_day_scheduler(app)
        assert scheduler is None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_settings_lifecycle(manager_client, app):
    r = manager_client.post("/settings", data={
        "recent_days": "3",
        "due_soon_days": "10",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop,Windows",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_setting("recent_days") == "3"
        assert s.get_checklist_inside() == ["Sweep", "Mop", "Windows"]
        assert s.get_checklist_outside() == ["Dump"]
        assert s.get_checklist() == ["Sweep", "Mop", "Windows", "Dump"]


def test_dark_mode_defaults_off_and_renders_theme(manager_client):
    r = manager_client.get("/")
    assert b'data-theme="off"' in r.data
    r = manager_client.get("/settings")
    assert b"dark_mode" in r.data


def test_dark_mode_can_be_turned_on(manager_client, app):
    r = manager_client.post("/settings", data={
        "dark_mode": "on",
        "recent_days": "2",
        "due_soon_days": "7",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "on"
    assert b'data-theme="dark"' in manager_client.get("/").data
    assert b'data-theme="dark"' in manager_client.get("/settings").data


def test_dark_mode_survives_saving_other_settings(manager_client, app):
    manager_client.post("/settings", data={"dark_mode": "on"})
    r = manager_client.post("/settings", data={
        "recent_days": "3",
        "due_soon_days": "7",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "on"
    assert b'data-theme="dark"' in manager_client.get("/").data


def test_dark_mode_rejects_unknown_values(manager_client, app):
    manager_client.post("/settings", data={"dark_mode": "hotdog-pink"})
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "off"


def test_futuristic_theme_can_be_turned_on(manager_client, app):
    r = manager_client.post("/settings", data={
        "dark_mode": "futuristic",
        "recent_days": "2",
        "due_soon_days": "7",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "futuristic"
    assert b'data-theme="futuristic"' in manager_client.get("/").data
    assert b'data-theme="futuristic"' in manager_client.get("/settings").data


def test_futuristic_theme_survives_saving_other_settings(manager_client, app):
    manager_client.post("/settings", data={"dark_mode": "futuristic"})
    r = manager_client.post("/settings", data={
        "recent_days": "3",
        "due_soon_days": "7",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "futuristic"
    assert b'data-theme="futuristic"' in manager_client.get("/").data
    assert b'data-theme="futuristic"' in manager_client.get("/settings").data


def test_halloween_theme_can_be_turned_on(manager_client, app):
    r = manager_client.post("/settings", data={
        "dark_mode": "halloween",
        "recent_days": "2",
        "due_soon_days": "7",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "halloween"
    assert b'data-theme="halloween"' in manager_client.get("/").data
    assert b'data-theme="halloween"' in manager_client.get("/settings").data


def test_halloween_theme_survives_saving_other_settings(manager_client, app):
    manager_client.post("/settings", data={"dark_mode": "halloween"})
    r = manager_client.post("/settings", data={
        "recent_days": "3",
        "due_soon_days": "7",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "halloween"
    assert b'data-theme="halloween"' in manager_client.get("/").data
    assert b'data-theme="halloween"' in manager_client.get("/settings").data


def test_bloomberg_theme_can_be_turned_on(manager_client, app):
    r = manager_client.post("/settings", data={
        "dark_mode": "bloomberg",
        "recent_days": "2",
        "due_soon_days": "7",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "bloomberg"
    assert b'data-theme="bloomberg"' in manager_client.get("/").data
    assert b'data-theme="bloomberg"' in manager_client.get("/settings").data
    assert "Bloomberg Terminal" in manager_client.get("/settings").data.decode()


def test_bloomberg_theme_survives_saving_other_settings(manager_client, app):
    manager_client.post("/settings", data={"dark_mode": "bloomberg"})
    r = manager_client.post("/settings", data={
        "recent_days": "3",
        "due_soon_days": "7",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "bloomberg"
    assert b'data-theme="bloomberg"' in manager_client.get("/").data
    assert b'data-theme="bloomberg"' in manager_client.get("/settings").data


def test_retro_theme_can_be_turned_on(manager_client, app):
    r = manager_client.post("/settings", data={
        "dark_mode": "retro",
        "recent_days": "2",
        "due_soon_days": "7",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "retro"
    assert b'data-theme="retro"' in manager_client.get("/").data
    assert b'data-theme="retro"' in manager_client.get("/settings").data
    assert "Retro 90s" in manager_client.get("/settings").data.decode()


def test_retro_theme_survives_saving_other_settings(manager_client, app):
    manager_client.post("/settings", data={"dark_mode": "retro"})
    r = manager_client.post("/settings", data={
        "recent_days": "3",
        "due_soon_days": "7",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "retro"
    assert b'data-theme="retro"' in manager_client.get("/").data
    assert b'data-theme="retro"' in manager_client.get("/settings").data


def test_holographic_theme_can_be_turned_on(manager_client, app):
    r = manager_client.post("/settings", data={
        "dark_mode": "holographic",
        "recent_days": "2",
        "due_soon_days": "7",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "holographic"
    assert b'data-theme="holographic"' in manager_client.get("/").data
    assert b'data-theme="holographic"' in manager_client.get("/settings").data
    assert "Holographic" in manager_client.get("/settings").data.decode()


def test_holographic_theme_survives_saving_other_settings(manager_client, app):
    manager_client.post("/settings", data={"dark_mode": "holographic"})
    r = manager_client.post("/settings", data={
        "recent_days": "3",
        "due_soon_days": "7",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "holographic"
    assert b'data-theme="holographic"' in manager_client.get("/").data
    assert b'data-theme="holographic"' in manager_client.get("/settings").data


def test_theme_is_per_user(client, manager_client, app):
    """Manager, employee and driver each keep their own theme, and a change by
    one never affects the others."""
    from app.models import Employee
    # Manager prefers dark.
    manager_client.post("/settings", data={"dark_mode": "on"})
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("manager") == "on"
        # The employee has their own theme, unaffected by the manager.
        emp = Employee.query.filter_by(active=True).first()
        assert s.get_user_theme("employee", emp.id) == "off"

    # Employee (as a specific named person) prefers futuristic via Settings.
    with app.app_context():
        emp = Employee.query.filter_by(active=True).first()
        emp_id = str(emp.id)
    r = client.post("/settings", data={"dark_mode": "futuristic"})
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("employee", int(emp_id)) == "futuristic"
        assert s.get_user_theme("manager") == "on"

    # Each role sees their own theme rendered.
    assert b'data-theme="futuristic"' in client.get("/").data
    assert b'data-theme="dark"' in manager_client.get("/").data

    # A different employee (not yet chosen) still falls back to the global.
    d = app.test_client()
    d.post("/login", data={"username": "employee", "password": "employee"})
    with app.app_context():
        other_emp = Employee(name="Other Person", active=True)
        db.session.add(other_emp)
        db.session.commit()
        other_id = str(other_emp.id)
    d.post("/select", data={"employee_id": other_id})
    assert b'data-theme="off"' in d.get("/").data


def test_driver_can_set_own_theme(app):
    d = app.test_client()
    d.post("/login", data={"username": "driver", "password": "driver"})
    # Drivers can open Settings (their own theme page), not just the board.
    assert d.get("/settings").status_code == 200
    r = d.post("/settings", data={"dark_mode": "on"})
    assert r.status_code == 302
    assert b'data-theme="dark"' in d.get("/driver").data
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_theme("driver") == "on"
        assert s.get_user_theme("manager") == "off"


def test_theme_chooser_is_in_settings_not_topbar(client, app):
    html = client.get("/").data.decode()
    assert "theme-form" not in html
    assert 'name="dark_mode"' not in html
    html = client.get("/settings").data.decode()
    assert "Appearance" in html
    assert 'name="dark_mode"' in html


def test_layout_defaults_to_classic(manager_client):
    """The current design is the default: no layout -> classic top bar."""
    r = manager_client.get("/")
    assert b'data-layout="classic"' in r.data
    assert b'class="topbar"' in r.data
    assert b"sp-sidebar" not in r.data


def test_layout_can_be_switched_to_sidepanel(manager_client, app):
    r = manager_client.post("/settings", data={"layout": "sidepanel"})
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_layout("manager") == "sidepanel"
    # The rendered page now uses the new sidebar design.
    page = manager_client.get("/").data
    assert b'data-layout="sidepanel"' in page
    assert b"sp-sidebar" in page
    assert b'sp-shell' in page
    # The settings page reflects the saved choice.
    r = manager_client.get("/settings")
    assert b'name="layout"' in r.data
    assert b'value="sidepanel" selected' in r.data


def test_layout_survives_saving_theme_and_other_settings(manager_client, app):
    manager_client.post("/settings", data={"layout": "sidepanel"})
    r = manager_client.post("/settings", data={
        "dark_mode": "on",
        "recent_days": "3",
        "due_soon_days": "7",
        "location": "Main Depot",
        "checklist_inside": "Sweep,Mop",
        "checklist_outside": "Dump",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_layout("manager") == "sidepanel"
        assert s.get_user_theme("manager") == "on"
    assert b'data-layout="sidepanel"' in manager_client.get("/").data


def test_layout_rejects_unknown_values(manager_client, app):
    manager_client.post("/settings", data={"layout": "flying-dashboard"})
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_layout("manager") == "classic"


def test_layout_is_per_user(client, manager_client, app):
    """Manager, employee and driver each keep their own layout."""
    manager_client.post("/settings", data={"layout": "sidepanel"})
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_layout("manager") == "sidepanel"
        emp = Employee.query.filter_by(active=True).first()
        assert s.get_user_layout("employee", emp.id) == "classic"
    # The manager's choice never changes the employee's layout.
    with app.app_context():
        from app.services import settings as s
        emp = Employee.query.filter_by(active=True).first()
        assert s.get_user_layout("employee", emp.id) == "classic"
    # Each role sees their own layout rendered.
    assert b'data-layout="classic"' in client.get("/").data
    assert b'data-layout="sidepanel"' in manager_client.get("/").data
    assert b"sp-sidebar" in manager_client.get("/").data


def test_driver_can_set_own_layout(app):
    d = app.test_client()
    d.post("/login", data={"username": "driver", "password": "driver"})
    assert d.get("/settings").status_code == 200
    r = d.post("/settings", data={"layout": "sidepanel"})
    assert r.status_code == 302
    assert b'data-layout="sidepanel"' in d.get("/driver").data
    assert b"sp-sidebar" in d.get("/driver").data
    with app.app_context():
        from app.services import settings as s
        assert s.get_user_layout("driver") == "sidepanel"
        assert s.get_user_layout("manager") == "classic"


def test_layout_chooser_is_in_settings_not_topbar(client, app):
    html = client.get("/").data.decode()
    assert 'name="layout"' not in html
    html = client.get("/settings").data.decode()
    assert 'name="layout"' in html
    assert "Side Panel (new)" in html


def test_categorized_checklist_setting_and_defaults(app):
    """Verify the default Inside/Outside split and that custom values stick."""
    from app.services import settings as s
    with app.app_context():
        assert s.get_checklist_inside() == [
            "Sweep", "Mop", "Windows", "Seats", "Bathroom"]
        assert s.get_checklist_outside() == [
            "Dump", "Bay Checked", "Final Inspection"]
        # Combined flat list preserves order inside then outside
        assert s.get_checklist() == [
            "Sweep", "Mop", "Windows", "Seats", "Bathroom",
            "Dump", "Bay Checked", "Final Inspection"]

        # Custom categorized values
        s.set_setting("checklist_inside", "Vacuum,Wipe Seats")
        s.set_setting("checklist_outside", "Wash Body,Windows")
        assert s.get_checklist_inside() == ["Vacuum", "Wipe Seats"]
        assert s.get_checklist_outside() == ["Wash Body", "Windows"]
        assert s.get_checklist() == ["Vacuum", "Wipe Seats", "Wash Body", "Windows"]


def test_categorized_type_checklist_parsing(app):
    """VehicleType.checklist stored with Inside/Outside prefixes parses and
    formats correctly."""
    from app.services.vehicles import get_or_create_vehicle_type
    from app.services import settings as s
    with app.app_context():
        vt = get_or_create_vehicle_type("PARSEBUS")
        vt.checklist = "Inside: Vacuum, Glass | Outside: Bay Checked"
        db.session.commit()
        cat = s.get_type_categorized_checklist(vt)
        assert cat["inside"] == ["Vacuum", "Glass"]
        assert cat["outside"] == ["Bay Checked"]
        assert s.get_type_checklist(vt) == ["Vacuum", "Glass", "Bay Checked"]

        # Flat legacy string -> all inside
        vt.checklist = "Sweep,Windows"
        db.session.commit()
        cat = s.get_type_categorized_checklist(vt)
        assert cat["inside"] == ["Sweep", "Windows"]
        assert cat["outside"] == []


def test_settings_page_renders_inside_outside(manager_client, app):
    r = manager_client.get("/settings")
    assert r.status_code == 200
    assert b"Inside tasks" in r.data
    assert b"Outside tasks" in r.data


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

def test_vehicle_crud(manager_client, app):
    r = manager_client.post("/vehicles/new", data={
        "unit_number": "999",
        "vehicle_type": "Bus",
        "route": "D1",
        "status": "Active",
        "notes": "test",
    })
    assert r.status_code == 302
    with app.app_context():
        v = Vehicle.query.filter_by(unit_number="999").first()
        assert v is not None
        vid = v.id
    r = manager_client.get(f"/vehicles/{vid}")
    assert r.status_code == 200
    assert b"999" in r.data
    r = manager_client.get("/vehicles")
    assert b"999" in r.data


def test_vehicle_list_shows_inactive_vehicles(manager_client, app):
    with app.app_context():
        v, _ = find_or_create_vehicle("888", vehicle_type="Van", route="R8")
        v.active = False
        db.session.commit()
    r = manager_client.get("/vehicles")
    assert r.status_code == 200
    assert b"888" in r.data
    assert b"Inactive" in r.data


def test_employees_and_history_pages(manager_client):
    assert manager_client.get("/employees").status_code == 200
    assert manager_client.get("/history").status_code == 200
    assert manager_client.get("/settings").status_code == 200


def test_manager_adds_employee(manager_client, app):
    r = manager_client.post("/employees", data={"name": "Sally Driver"})
    assert r.status_code == 302
    with app.app_context():
        emp = Employee.query.filter_by(name="Sally Driver").first()
        assert emp is not None
        assert emp.active is True
    body = manager_client.get("/employees").get_data(as_text=True)
    assert "Sally Driver" in body


def test_manager_removes_employee(manager_client, app):
    with app.app_context():
        emp = Employee(name="Tom Lee", active=True)
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id
    r = manager_client.post(f"/employees/{emp_id}/toggle-active")
    assert r.status_code == 302
    with app.app_context():
        emp = Employee.query.get(emp_id)
        assert emp.active is False
        assert emp.current_vehicle_id is None


def test_manager_removes_employee_frees_current_vehicle(manager_client, app):
    with app.app_context():
        v, _ = find_or_create_vehicle("777", vehicle_type="Van", route="R7")
        emp = Employee(name="Free Me", active=True, current_vehicle_id=v.id)
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id
    manager_client.post(f"/employees/{emp_id}/toggle-active")
    with app.app_context():
        assert Employee.query.get(emp_id).current_vehicle_id is None


def test_manager_reactivates_employee(manager_client, app):
    with app.app_context():
        emp = Employee(name="Back Again", active=False)
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id
    r = manager_client.post(f"/employees/{emp_id}/toggle-active")
    assert r.status_code == 302
    with app.app_context():
        assert Employee.query.get(emp_id).active is True


def test_employee_cannot_manage_staff(client, app):
    with app.app_context():
        emp = Employee(name="Worker Bee", active=True)
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id
    assert client.post(f"/employees/{emp_id}/toggle-active").status_code == 302
    with app.app_context():
        assert Employee.query.get(emp_id).active is True


def test_trash_page_lists_lots_before_any_pickup(client, app):
    page = client.get("/trash")
    assert page.status_code == 200
    assert "Last Pickup by Lot" in page.get_data(as_text=True)
    assert "Never" in page.get_data(as_text=True)


def test_trash_record_pickup(client, app):
    from app.models import TrashPickup
    page = client.post("/trash", data={"location_id": "", "notes": "Dumpster full"})
    assert page.status_code == 302
    with app.app_context():
        pickup = TrashPickup.query.first()
        assert pickup is not None
        assert pickup.notes == "Dumpster full"
    body = client.get("/trash").get_data(as_text=True)
    assert "Recent" in body
    assert "Dumpster full" in body


def test_trash_shows_latest_pickup_for_lot(client, app):
    from datetime import datetime, timedelta
    from app.models import TrashPickup
    from app.services.vehicles import default_location
    with app.app_context():
        loc = default_location()
        old = TrashPickup(location_id=loc.id,
                          picked_up_at=datetime.utcnow() - timedelta(days=5),
                          notes="old pickup")
        fresh = TrashPickup(location_id=loc.id,
                            picked_up_at=datetime.utcnow(),
                            notes="fresh pickup")
        db.session.add_all([old, fresh])
        db.session.commit()
    body = client.get("/trash").get_data(as_text=True)
    assert "fresh pickup" in body
    assert "old pickup" not in body


def test_schedule_view_orders_by_prep_time(app):
    """Entries on the board are ordered by prep time (earliest first), falling
    back to import order for entries without a prep time."""
    from app.app import build_schedule_view
    from app.services import schedule as ss
    from app.services.vehicles import find_or_create_vehicle

    with app.app_context():
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)

        def add(unit, prep):
            v, _ = find_or_create_vehicle(unit, location_id=loc.id)
            ss.ensure_entry(sched, v, order_index=int(unit),
                            prep_time=prep)

        add("300", "09:45")
        add("400", "04:00")
        add("500", None)      # no prep time -> goes after timed ones
        add("100", "01:45")

        order = [r["vehicle"].unit_number for r in build_schedule_view(sched)]
        assert order == ["100", "400", "300", "500"]


def test_per_vehicle_type_checklist(app):
    """A vehicle type with its own checklist gets those tasks; a type without
    one falls back to the global default checklist."""
    from app.models import VehicleType
    from app.services import schedule as ss
    from app.services.vehicles import find_or_create_vehicle, \
        get_or_create_vehicle_type, default_location

    with app.app_context():
        custom = get_or_create_vehicle_type("TRANSITB")
        custom.checklist = "Inside: Sweep, Windows | Outside: Bay Checked"
        db.session.commit()

        plain = get_or_create_vehicle_type("VAN")  # no checklist

        loc = default_location()
        sched = ss.get_or_create_schedule(location=loc)

        v1, _ = find_or_create_vehicle("711", vehicle_type="TRANSITB",
                                       location_id=loc.id)
        v2, _ = find_or_create_vehicle("722", vehicle_type="VAN",
                                       location_id=loc.id)

        e1 = ss.ensure_entry(sched, v1, order_index=0)
        e2 = ss.ensure_entry(sched, v2, order_index=1)

        t1 = sorted(t.task_name for t in e1.tasks)
        t2 = sorted(t.task_name for t in e2.tasks)
        assert t1 == ["Bay Checked", "Sweep", "Windows"]
        assert t2 == ["Bathroom", "Bay Checked", "Dump", "Final Inspection",
                      "Mop", "Seats", "Sweep", "Windows"]


def test_stale_current_vehicle_cleared_on_dashboard_load(client, app):
    """A 'now working' assignment left over from a previous day (employee
    forgot to hit Done) is cleared automatically when the dashboard loads."""
    from datetime import timedelta
    from app.models import Employee
    from app.services import schedule as ss
    from app.services.vehicles import find_or_create_vehicle

    with app.app_context():
        loc = vehicles_loc(app)
        emp = Employee(name="Alice Smith")
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id
        v, _ = find_or_create_vehicle("731", location_id=loc.id)
        sched = ss.get_or_create_schedule(location=loc)
        entry = ss.ensure_entry(sched, v)
        entry_id = entry.id
        # Assign the employee today, then rewind the assignment to a prior day.
        client.post("/start-work", data={
            "employee_id": str(emp_id), "entry_id": str(entry_id)})
        emp = Employee.query.get(emp_id)
        assert emp.current_vehicle_id == v.id
        emp.current_vehicle_set_on = date.today() - timedelta(days=1)
        db.session.commit()
        vehicle_id = v.id

    html = client.get("/").data.decode()
    assert '<div class="now-worker-name">Alice Smith</div>' not in html

    with app.app_context():
        assert Employee.query.get(emp_id).current_vehicle_id is None
        assert Employee.query.get(emp_id).current_vehicle_set_on is None
    assert vehicle_id is not None


def test_todays_current_vehicle_kept_on_dashboard_load(client, app):
    """Assignments made today survive a dashboard reload."""
    from app.models import Employee
    from app.services import schedule as ss
    from app.services.vehicles import find_or_create_vehicle

    with app.app_context():
        loc = vehicles_loc(app)
        emp = Employee(name="Brian Jones")
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id
        v, _ = find_or_create_vehicle("732", location_id=loc.id)
        sched = ss.get_or_create_schedule(location=loc)
        entry = ss.ensure_entry(sched, v)
        entry_id = entry.id
        vehicle_id = v.id

    client.post("/start-work", data={
        "employee_id": str(emp_id), "entry_id": str(entry_id)})

    html = client.get("/").data.decode()
    assert '<div class="now-worker-name">Brian Jones</div>' in html

    with app.app_context():
        e = Employee.query.get(emp_id)
        assert e.current_vehicle_id == vehicle_id
        assert e.current_vehicle_set_on == date.today()


def test_current_vehicle_cleared_when_vehicle_completed(client, app):
    """Once the last task is checked and a vehicle is complete, employees
    are no longer shown as currently working on it."""
    from app.models import Employee

    with app.app_context():
        emp = Employee(name="Bob Jones")
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id

        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        v, _ = find_or_create_vehicle("730", location_id=vehicles_loc(app).id)
        sched = ss.get_or_create_schedule(location=vehicles_loc(app))
        entry = ss.ensure_entry(sched, v)
        tasks = [t.task_name for t in entry.tasks]
        ea = entry.id

    for tname in tasks:
        r = client.post(f"/task/{ea}/{tname}",
                        data={"checked": "true", "employee_id": str(emp_id)})
        assert r.status_code == 200
        with app.app_context():
            e = ScheduleEntry.query.get(ea)
            if e.status != "completed":
                assert Employee.query.get(emp_id).current_vehicle_id is not None

    with app.app_context():
        e = ScheduleEntry.query.get(ea)
        assert e.status == "completed"
        assert Employee.query.get(emp_id).current_vehicle_id is None


def test_skip_vehicle_does_not_count_as_complete(client, app):
    """Skipping a vehicle does NOT count it toward completion: the entry keeps
    its real progress, stays incomplete/remaining, and is reported as skipped.
    It can still be un-skipped."""
    with app.app_context():
        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        from app.app import build_schedule_view, schedule_counters
        v, _ = find_or_create_vehicle("740", location_id=vehicles_loc(app).id)
        sched = ss.get_or_create_schedule(location=vehicles_loc(app))
        entry = ss.ensure_entry(sched, v)
        ea = entry.id
        sched_id = sched.id
        assert entry.status == "pending"

    # Skip it
    r = client.post(f"/entry/{ea}/skip")
    assert r.status_code == 302
    with app.app_context():
        e = ScheduleEntry.query.get(ea)
        assert e.status == "skipped"
        # Skipping never marks the work as done.
        done, total, pct = sched_svc.entry_progress(e)
        assert total > 0
        assert done == 0
        assert pct == 0

        view = build_schedule_view(DailySchedule.query.get(sched_id))
        row = next(r for r in view if r["entry"].id == ea)
        assert row["is_skipped"] is True
        assert row["is_complete"] is False
        assert row["pct"] == 0

        # Day totals: still one vehicle, zero completed, one skipped and the
        # skipped vehicle is still counted as remaining work.
        counts = schedule_counters(view)
        assert counts["total"] == 1
        assert counts["completed"] == 0
        assert counts["skipped"] == 1
        assert counts["remaining"] == 1
        assert counts["incomplete"] == 1
        assert counts["overall"] == 0

    # Dashboard shows the skipped stat and says it does not count as completed
    html = client.get("/").data.decode()
    assert "Skipped" in html
    assert '"skipped"' in html
    assert "does not count toward completion" in html

    # Un-skip restores to pending (no tasks done)
    r = client.post(f"/entry/{ea}/unskip")
    assert r.status_code == 302
    with app.app_context():
        e = ScheduleEntry.query.get(ea)
        assert e.status == "pending"
        view = build_schedule_view(DailySchedule.query.get(sched_id))
        assert schedule_counters(view)["skipped"] == 0


def test_skip_keeps_partial_progress_incomplete(client, app):
    """A vehicle that was partially worked and then skipped keeps its real
    progress and is still not counted as completed."""
    with app.app_context():
        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        from app.app import build_schedule_view, schedule_counters
        v, _ = find_or_create_vehicle("741", location_id=vehicles_loc(app).id)
        sched = ss.get_or_create_schedule(location=vehicles_loc(app))
        entry = ss.ensure_entry(sched, v)
        ea = entry.id
        sched_id = sched.id
        ss.toggle_task(ea, "Sweep", True)
        done, total, _ = sched_svc.entry_progress(entry)
        assert done == 1 and total > 1

    assert client.post(f"/entry/{ea}/skip", data={"reason": "Maintenance"}).status_code == 302

    with app.app_context():
        e = ScheduleEntry.query.get(ea)
        assert e.status == "skipped"
        # The one completed task is still counted, but the entry is not complete.
        assert sched_svc.entry_progress(e)[:2] == (1, total)
        view = build_schedule_view(DailySchedule.query.get(sched_id))
        row = next(r for r in view if r["entry"].id == ea)
        assert row["is_skipped"] is True
        assert row["is_complete"] is False
        counts = schedule_counters(view)
        assert counts["completed"] == 0
        assert counts["skipped"] == 1
        assert counts["remaining"] == 1
        assert counts["overall"] < 100


def test_skip_reason_dropdown_includes_didnt_get_to_it(client, app):
    with app.app_context():
        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        v, _ = find_or_create_vehicle("765", location_id=vehicles_loc(app).id)
        ss.ensure_entry(ss.get_or_create_schedule(location=vehicles_loc(app)), v)

    html = client.get("/").data.decode()
    assert '<option value="Didn’t Get To It">Didn’t Get To It</option>' in html


def test_skip_does_not_record_cleaning_and_stores_reason(client, app):
    """Skipping a vehicle must NOT mark it as cleaned (still needs cleaning),
    and the skip reason is stored and rendered."""
    from app.models import ServiceRecord
    from datetime import date

    with app.app_context():
        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        v, _ = find_or_create_vehicle("750", location_id=vehicles_loc(app).id)
        assert v.last_washed is None
        sched = ss.get_or_create_schedule(location=vehicles_loc(app))
        entry = ss.ensure_entry(sched, v)
        ea = entry.id

    # Skip with a reason
    r = client.post(f"/entry/{ea}/skip", data={"reason": "not in service today"})
    assert r.status_code == 302

    with app.app_context():
        e = ScheduleEntry.query.get(ea)
        assert e.status == "skipped"
        assert e.skip_reason == "not in service today"
        # No service record created -> vehicle still needs cleaning
        assert ServiceRecord.query.filter_by(vehicle_id=e.vehicle_id).count() == 0
        v = e.vehicle
        assert v.last_washed is None
        assert v.last_detailed is None

    # End-day report and print report show the reason
    assert client.get("/end").data.decode().find("not in service today") != -1
    assert client.get(f"/print/{date.today().isoformat()}").data.decode().find(
        "not in service today") != -1


def test_skip_json_and_unskip_fragment(client, app):
    """The skip endpoint answers JSON for AJAX (so the page doesn't reload and
    lose scroll position) and hands back recalculated day totals (a skip does
    not bump completed), and un-skip redirects back to the same row."""
    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        from app.services import schedule as ss
        v, _ = find_or_create_vehicle("760", location_id=vehicles_loc(app).id)
        sched = ss.get_or_create_schedule(location=vehicles_loc(app))
        entry = ss.ensure_entry(sched, v)
        ea = entry.id

    r = client.post(f"/entry/{ea}/skip", data={"reason": "Maintenance"},
                    headers={"Accept": "application/json"})
    assert r.status_code == 200
    payload = r.get_json()
    assert payload["ok"] is True
    assert payload["unit"] == "760"
    assert payload["reason"] == "Maintenance"
    # The AJAX path updates the stat tiles, so they must not claim completion.
    counters = payload["counters"]
    assert counters["skipped"] == 1
    assert counters["completed"] == 0
    assert counters["remaining"] == 1

    with app.app_context():
        assert ScheduleEntry.query.get(ea).status == "skipped"

    r = client.post(f"/entry/{ea}/unskip")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"#row-{ea}")

    with app.app_context():
        assert ScheduleEntry.query.get(ea).status == "pending"


def test_finalized_day_summary_counts_skipped_as_incomplete(client, app):
    """Ending a day with a skipped vehicle records it as skipped and still
    incomplete, so the day never looks finished on the strength of a skip."""
    with app.app_context():
        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        loc = vehicles_loc(app)
        v, _ = find_or_create_vehicle("780", location_id=loc.id)
        sched = ss.get_or_create_schedule(location=loc)
        entry = ss.ensure_entry(sched, v)
        sched_svc.set_entry_skipped(entry, skipped=True, reason="Maintenance")

    assert client.post("/end", data={"confirm": "yes"}).status_code == 302

    with app.app_context():
        summary = json.loads(sched_svc.get_or_create_schedule(
            location=vehicles_loc(app)).summary)
        assert summary["total"] == 1
        assert summary["completed"] == 0
        assert summary["skipped"] == 1
        assert summary["incomplete"] == 1
        assert summary["overall"] == 0


# ---------------------------------------------------------------------------
# Transit buses
# ---------------------------------------------------------------------------

def _echo_report_pdf(rows):
    """Build an ECHO-format prep report PDF.

    rows: list of (prep_time, unit, location_code, vehicle_type, type, trips)
    """
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    headers = ["Prep Time", "Vehicle", "Vehicle Type", "Type", "Trips #"]
    col_widths = [60, 90, 80, 90, 45]
    row_h = 30
    x0, y0 = 40, 60

    all_rows = [headers] + [list(r) for r in rows]
    for ri, row in enumerate(all_rows):
        ry = y0 + ri * row_h
        cx = x0
        for ci, (cell, w) in enumerate(zip(row, col_widths)):
            shape = page.new_shape()
            shape.draw_rect(fitz.Rect(cx, ry, cx + w, ry + row_h))
            shape.finish(color=(0, 0, 0))
            shape.commit()
            if ci == 1 and "\n" in cell:
                lines = cell.split("\n", 1)
                page.insert_text((cx + 3, ry + 14), lines[0], fontsize=8)
                page.insert_text((cx + 3, ry + 24), lines[1], fontsize=7)
            else:
                page.insert_text((cx + 3, ry + 14), cell, fontsize=8)
            cx += w
    return doc.tobytes()


def test_is_transit_type():
    from app.services.vehicles import is_transit_type

    assert is_transit_type("TRANSITB")
    assert is_transit_type("transitb")
    assert is_transit_type("TRANSIT BUS")
    assert is_transit_type("transit_bus")
    assert not is_transit_type("SUVSUB")
    assert not is_transit_type("Van.")
    assert not is_transit_type("MINIBUS")
    assert not is_transit_type(None)
    assert not is_transit_type("")


def test_is_transit_vehicle(app):
    from app.services.vehicles import find_or_create_vehicle, is_transit_vehicle

    with app.app_context():
        bus, _ = find_or_create_vehicle("4301", vehicle_type="TRANSITB",
                                        location_id=vehicles_loc(app).id)
        van, _ = find_or_create_vehicle("9331", vehicle_type="Van.",
                                        location_id=vehicles_loc(app).id)
        assert is_transit_vehicle(bus) is True
        assert is_transit_vehicle(van) is False
        assert is_transit_vehicle(None) is False


def test_import_skips_transit_vehicles(client, app):
    """TRANSITB vehicles on a prep report are imported onto the board but
    skipped (they are washed by another crew)."""
    data = _echo_report_pdf([
        ("01:45", "100-\nJAXUNF", "TRANSITB", "Shuttle", "2"),
        ("02:00", "200-\nJAXSUV", "SUVSUB", "Hourly", "1"),
        ("03:15", "300-\nJAXUNF", "TRANSITB", "Shuttle", "1"),
    ])

    r = client.post("/import", data={"pdf": (io.BytesIO(data), "transit.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    # The preview calls out the transit buses up front.
    assert b"Transit Buses (skipped automatically)" in r.data

    with app.app_context():
        from app.models import PrepReportImport
        iid = PrepReportImport.query.first().id

    r = client.post(f"/import/{iid}/apply")
    assert r.status_code == 302

    with app.app_context():
        from app.app import build_schedule_view, schedule_counters
        sched = DailySchedule.query.filter_by(work_date=date.today()).first()
        statuses = {
            e.vehicle.unit_number: (e.status, e.skip_reason)
            for e in sched.entries
        }
        assert len(statuses) == 3
        assert statuses["100"] == ("skipped", "Transit — auto-skipped on import")
        assert statuses["300"] == ("skipped", "Transit — auto-skipped on import")
        assert statuses["200"] == ("pending", None)

        view = build_schedule_view(sched)
        rows = {r["vehicle"].unit_number: r for r in view}
        assert rows["100"]["is_auto_skipped"] is True
        assert rows["100"]["is_complete"] is False
        assert rows["300"]["is_complete"] is False
        assert rows["200"]["is_auto_skipped"] is False
        counts = schedule_counters(view)
        assert counts["total"] == 1
        assert counts["completed"] == 0
        assert counts["skipped"] == 2
        assert counts["remaining"] == 1
        assert counts["incomplete"] == 1
        assert counts["overall"] == 0


def test_auto_skipped_transit_is_excluded_from_day_totals(client, app):
    from app.app import build_schedule_view, finalize_day, schedule_counters
    from app.services import schedule as ss
    from app.services.vehicles import TRANSIT_SKIP_REASON, find_or_create_vehicle

    with app.app_context():
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        bus, _ = find_or_create_vehicle("4401", vehicle_type="TRANSITB",
                                        location_id=loc.id)
        bus_entry = ss.ensure_entry(sched, bus)
        ss.set_entry_skipped(bus_entry, skipped=True,
                             reason=TRANSIT_SKIP_REASON)
        van, _ = find_or_create_vehicle("4402", vehicle_type="Van",
                                        location_id=loc.id)
        van_entry = ss.ensure_entry(sched, van)
        for task in list(van_entry.tasks):
            ss.toggle_task(van_entry.id, task.task_name, True)

        counts = schedule_counters(build_schedule_view(sched))
        assert counts["total"] == 1
        assert counts["completed"] == 1
        assert counts["skipped"] == 1
        assert counts["remaining"] == 0
        assert counts["incomplete"] == 0
        assert counts["overall"] == 100

        finalize_day(sched)
        summary = json.loads(sched.summary)
        assert summary["total"] == 1
        assert summary["completed"] == 1
        assert summary["incomplete"] == 0
        assert summary["skipped"] == 1
        assert summary["overall"] == 100

    html = client.get("/end").data.decode()
    assert "4401" not in html
    assert "4402" in html


def test_import_keeps_completed_transit_vehicle_untouched(client, app):
    """Re-importing a report never overwrites work already done on a transit
    vehicle, and never replaces a manual skip reason."""
    from app.app import build_schedule_view, schedule_counters
    from app.services import schedule as ss
    from app.services.vehicles import find_or_create_vehicle
    from app.services.vehicles import TRANSIT_SKIP_REASON

    with app.app_context():
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        done_bus, _ = find_or_create_vehicle("410", vehicle_type="TRANSITB",
                                             location_id=loc.id)
        done_entry = ss.ensure_entry(sched, done_bus)
        for t in list(done_entry.tasks):
            ss.toggle_task(done_entry.id, t.task_name, True)

        other_bus, _ = find_or_create_vehicle("411", vehicle_type="TRANSITB",
                                              location_id=loc.id)
        other_entry = ss.ensure_entry(sched, other_bus)
        ss.set_entry_skipped(other_entry, skipped=True, reason="Maintenance")

    data = _echo_report_pdf([
        ("01:45", "410-\nJAXUNF", "TRANSITB", "Shuttle", "2"),
        ("02:00", "411-\nJAXUNF", "TRANSITB", "Shuttle", "1"),
    ])
    r = client.post("/import", data={"pdf": (io.BytesIO(data), "transit2.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    with app.app_context():
        from app.models import PrepReportImport
        iid = PrepReportImport.query.first().id
    assert client.post(f"/import/{iid}/apply").status_code == 302

    with app.app_context():
        sched = DailySchedule.query.filter_by(work_date=date.today()).first()
        by_unit = {e.vehicle.unit_number: e for e in sched.entries}
        assert by_unit["410"].status == "completed"
        assert by_unit["410"].skip_reason is None
        assert by_unit["411"].status == "skipped"
        assert by_unit["411"].skip_reason == "Maintenance"
        assert TRANSIT_SKIP_REASON not in {e.skip_reason for e in sched.entries}

        view = build_schedule_view(sched)
        manual_row = next(r for r in view if r["vehicle"].unit_number == "411")
        assert manual_row["is_auto_skipped"] is False
        assert manual_row["is_complete"] is False
        counts = schedule_counters(view)
        assert counts["completed"] == 1
        assert counts["remaining"] == 1


def test_transit_vehicles_in_own_dropdown_on_dashboard(client, app):
    """Transit buses are still on the dashboard, but inside their own dropdown
    at the bottom instead of the main work list."""
    from app.services import schedule as ss
    from app.services.vehicles import find_or_create_vehicle

    with app.app_context():
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        bus, _ = find_or_create_vehicle("4301", vehicle_type="TRANSITB",
                                        location_id=loc.id)
        ss.ensure_entry(sched, bus)
        suv, _ = find_or_create_vehicle("9205", vehicle_type="SUVSUB",
                                        location_id=loc.id)
        ss.ensure_entry(sched, suv)

    html = client.get("/").data.decode()
    assert "transit-board" in html
    main, _, dropdown = html.partition('<details class="card sect transit-board"')
    assert "Transit Buses (1)" in html

    # The transit bus is inside the dropdown, not the main work list.
    assert 'class="vnum">4301<' in dropdown
    assert 'class="vnum">4301<' not in main
    assert 'class="vnum">9205<' in main
    # ...and it is marked as a transit bus on the board.
    assert '<span class="badge info">Transit</span>' in dropdown

    # Filtering by unit keeps the transit bus findable in the same place.
    filtered = client.get("/?unit=4301").data.decode()
    assert 'class="vnum">4301<' in filtered
    assert 'class="vnum">9205<' not in filtered
    assert "transit-board" in filtered

    # The filtered count includes the transit dropdown.
    assert "Showing 1 of 2" in filtered


def test_report_type_transit_wins_over_stored_type(client, app):
    """A report calling a vehicle TRANSITB skips it and drops it in the transit
    dropdown even when the stored type says something else."""
    from app.services.vehicles import find_or_create_vehicle

    with app.app_context():
        v, _ = find_or_create_vehicle("9331", vehicle_type="Van.",
                                      location_id=vehicles_loc(app).id)

    data = _echo_report_pdf([("02:00", "9331-\nJAXVAN", "TRANSITB", "Shuttle", "1")])
    r = client.post("/import", data={"pdf": (io.BytesIO(data), "van.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    with app.app_context():
        from app.models import PrepReportImport
        iid = PrepReportImport.query.first().id
    assert client.post(f"/import/{iid}/apply").status_code == 302

    with app.app_context():
        entry = ScheduleEntry.query.filter_by(vehicle_id=v.id).first()
        assert entry.status == "skipped"
        assert entry.skip_reason == "Transit — auto-skipped on import"

    html = client.get("/").data.decode()
    _, _, dropdown = html.partition('<details class="card sect transit-board"')
    assert 'class="vnum">9331<' in dropdown


def test_unskip_transit_vehicle_lets_it_be_worked(client, app):
    """A transit bus can be un-skipped and worked like any other vehicle."""
    from app.app import build_schedule_view, schedule_counters
    from app.services import schedule as ss
    from app.services.vehicles import find_or_create_vehicle

    with app.app_context():
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        bus, _ = find_or_create_vehicle("4302", vehicle_type="TRANSITB",
                                        location_id=loc.id)
        entry = ss.ensure_entry(sched, bus)
        ss.set_entry_skipped(entry, skipped=True,
                             reason="Transit — auto-skipped on import")
        eid = entry.id
        counts = schedule_counters(build_schedule_view(sched))
        assert counts["total"] == 0
        assert counts["remaining"] == 0
        assert counts["skipped"] == 1

    html = client.get("/").data.decode()
    assert f'id="row-{eid}"' in html
    assert "Un-skip" in html

    assert client.post(f"/entry/{eid}/unskip").status_code == 302
    with app.app_context():
        assert ScheduleEntry.query.get(eid).status == "pending"
        sched = DailySchedule.query.filter_by(work_date=date.today()).first()
        counts = schedule_counters(build_schedule_view(sched))
        assert counts["total"] == 1
        assert counts["remaining"] == 1
        assert counts["completed"] == 0

    # It now offers the normal work actions.
    html = client.get("/").data.decode()
    _, _, dropdown = html.partition('<details class="card sect transit-board"')
    assert f'id="row-{eid}"' in dropdown
    assert "Skip" in dropdown

    # ...and it still counts as a transit vehicle (dropdown, Transit badge).
    assert '<span class="badge info">Transit</span>' in dropdown


# ---------------------------------------------------------------------------
# Dump tracking
# ---------------------------------------------------------------------------

def test_dump_tracking_on_all_checklist_vehicles(client, app):
    """Checking the Sweep (wash) task increments cleanings_since_dump and
    checking the Dump task resets it and records last_dumped."""
    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        from app.services import schedule as ss
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v, _ = find_or_create_vehicle("310", location_id=loc.id)
        entry = ss.ensure_entry(sched, v)
        entry_id = entry.id

    # First cleaning
    r = client.post(f"/task/{entry_id}/Sweep", data={"checked": "true"})
    assert r.status_code == 200
    with app.app_context():
        e = ScheduleEntry.query.get(entry_id)
        assert e.vehicle.cleanings_since_dump == 1

    # Second cleaning -> vehicle needs dump (twice cleaned, not dumped)
    r = client.post(f"/task/{entry_id}/Sweep", data={"checked": "true"})
    assert r.status_code == 200
    with app.app_context():
        e = ScheduleEntry.query.get(entry_id)
        v = e.vehicle
        assert v.cleanings_since_dump == 2
        assert v.needs_dump is True

    # Dump the vehicle -> resets counter and sets last_dumped
    r = client.post(f"/task/{entry_id}/Dump", data={"checked": "true"})
    assert r.status_code == 200
    with app.app_context():
        e = ScheduleEntry.query.get(entry_id)
        v = e.vehicle
        assert v.cleanings_since_dump == 0
        assert v.last_dumped is not None
        assert v.needs_dump is False


def test_dump_status_shown_on_dashboard_and_detail(client, manager_client, app):
    """Needs Dump indicator appears for a vehicle cleaned twice without dump."""
    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        from app.services import schedule as ss
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v, _ = find_or_create_vehicle("320", location_id=loc.id)
        entry = ss.ensure_entry(sched, v)
        entry_id = entry.id
        vid = v.id

    # Clean twice
    client.post(f"/task/{entry_id}/Sweep", data={"checked": "true"})
    client.post(f"/task/{entry_id}/Sweep", data={"checked": "true"})

    # Dashboard shows Needs Dump
    html = client.get("/").data.decode()
    assert "Needs Dump" in html

    # Vehicle detail shows Needs Dump and Last Dumped field
    detail = manager_client.get(f"/vehicles/{vid}").data.decode()
    assert "Needs Dump" in detail
    assert "Cleanings Since Dump" in detail


def test_unchecking_sweep_decrements_cleanings(client, app):
    """Un-checking the Sweep task decrements cleanings_since_dump."""
    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        from app.services import schedule as ss
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v, _ = find_or_create_vehicle("330", location_id=loc.id)
        entry = ss.ensure_entry(sched, v)
        entry_id = entry.id

    client.post(f"/task/{entry_id}/Sweep", data={"checked": "true"})
    client.post(f"/task/{entry_id}/Sweep", data={"checked": "true"})
    with app.app_context():
        assert ScheduleEntry.query.get(entry_id).vehicle.cleanings_since_dump == 2

    client.post(f"/task/{entry_id}/Sweep", data={"checked": "false"})
    with app.app_context():
        assert ScheduleEntry.query.get(entry_id).vehicle.cleanings_since_dump == 1


# ---------------------------------------------------------------------------
# Login / roles
# ---------------------------------------------------------------------------

def test_login_required_redirects_to_login(app):
    c = app.test_client()
    r = c.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
    assert c.get("/vehicles").status_code == 302


def test_login_accepts_case_insensitive_username_routes_by_role(app):
    e = app.test_client()
    r = e.post("/login", data={"username": "Employee", "password": "employee"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/splash")
    # The splash animation loads first, then the name picker.
    assert e.get("/splash").status_code == 200
    # The board stays locked until the employee picks their name.
    assert e.get("/").status_code == 302
    with app.app_context():
        emp = Employee.query.filter_by(active=True).first()
    assert e.post("/select", data={"employee_id": str(emp.id)}).status_code == 302
    assert e.get("/").status_code == 200

    m = app.test_client()
    r = m.post("/login", data={"username": "manager", "password": "manager"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/splash")

    d = app.test_client()
    r = d.post("/login", data={"username": "driver", "password": "driver"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/splash")


def test_login_rejects_unknown_user_and_wrong_password(app):
    c = app.test_client()
    r = c.post("/login", data={"username": "admin", "password": "admin"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/login")
    assert b"Invalid username or password" in c.get("/login").data

    c2 = app.test_client()
    r = c2.post("/login", data={"username": "employee", "password": "wrong"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/login")


def test_logout_clears_session(app):
    c = app.test_client()
    c.post("/login", data={"username": "employee", "password": "employee"})
    # Board is locked until the employee picks their name, then it unlocks.
    assert c.get("/").status_code == 302
    with app.app_context():
        emp = Employee.query.filter_by(active=True).first()
    c.post("/select", data={"employee_id": str(emp.id)})
    assert c.get("/").status_code == 200
    r = c.post("/logout")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
    assert c.get("/").status_code == 302


def test_driver_restricted_to_finished_screen(app):
    d = app.test_client()
    d.post("/login", data={"username": "driver", "password": "driver"})
    assert d.get("/driver").status_code == 200
    # Drivers may not browse the rest of the app.
    assert d.get("/").status_code == 302
    # ...but Settings (their own theme page) stays available.
    assert d.get("/settings").status_code == 200


def test_driver_screen_shows_only_finished_vehicles(app):
    with app.app_context():
        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v1, _ = find_or_create_vehicle("101", location_id=loc.id)
        v2, _ = find_or_create_vehicle("102", location_id=loc.id)
        e1 = ss.ensure_entry(sched, v1)
        ss.ensure_entry(sched, v2)
        for t in list(e1.tasks):
            ss.toggle_task(e1.id, t.task_name, True)

    d = app.test_client()
    d.post("/login", data={"username": "driver", "password": "driver"})
    html = d.get("/driver").data.decode()
    assert "101" in html
    assert "102" not in html
    assert "Completed" in html


def test_driver_screen_shows_next_two_days(app):
    with app.app_context():
        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        from datetime import timedelta
        loc = vehicles_loc(app)

        # Complete a vehicle on tomorrow's schedule
        tomorrow = date.today() + timedelta(days=1)
        sched_tom = ss.get_or_create_schedule(d=tomorrow, location=loc)
        v1, _ = find_or_create_vehicle("201", location_id=loc.id)
        e1 = ss.ensure_entry(sched_tom, v1)
        for t in list(e1.tasks):
            ss.toggle_task(e1.id, t.task_name, True)

        # Complete a vehicle on +2 days schedule
        day_after = date.today() + timedelta(days=2)
        sched_da = ss.get_or_create_schedule(d=day_after, location=loc)
        v2, _ = find_or_create_vehicle("202", location_id=loc.id)
        e2 = ss.ensure_entry(sched_da, v2)
        for t in list(e2.tasks):
            ss.toggle_task(e2.id, t.task_name, True)

        # Vehicle more than 2 days out should NOT appear
        three_days = date.today() + timedelta(days=3)
        sched_3 = ss.get_or_create_schedule(d=three_days, location=loc)
        v3, _ = find_or_create_vehicle("300", location_id=loc.id)
        e3 = ss.ensure_entry(sched_3, v3)
        for t in list(e3.tasks):
            ss.toggle_task(e3.id, t.task_name, True)

    d = app.test_client()
    d.post("/login", data={"username": "driver", "password": "driver"})
    html = d.get("/driver").data.decode()
    assert "201" in html
    assert "202" in html
    assert "300" not in html


def test_manager_cannot_toggle_tasks(client, app):
    with app.app_context():
        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v, _ = find_or_create_vehicle("103", location_id=loc.id)
        entry = ss.ensure_entry(sched, v)
        eid = entry.id

    m = app.test_client()
    m.post("/login", data={"username": "manager", "password": "manager"})
    r = m.post(f"/task/{eid}/Sweep", data={"checked": "true"})
    assert r.status_code == 403


def test_employee_cannot_access_manager_only_pages(client, app):
    """Employees may not visit Vehicles or Staff pages, but they CAN open
    Settings to change their own theme."""
    for path in ["/vehicles", "/vehicles/new", "/employees"]:
        r = client.get(path)
        assert r.status_code == 302
        assert "/" == r.headers["Location"]
    with app.app_context():
        from app.services.vehicles import find_or_create_vehicle
        v, _ = find_or_create_vehicle("104", location_id=vehicles_loc(app).id)
        assert client.get(f"/vehicles/{v.id}").status_code == 302
    # Settings is available to every role for their own theme.
    r = client.get("/settings")
    assert r.status_code == 200
    assert b"Appearance" in r.data


def test_manager_can_access_all_pages(manager_client):
    """The Manager account can visit every page including manager-only ones."""
    for path in ["/vehicles", "/vehicles/new", "/employees", "/settings"]:
        assert manager_client.get(path).status_code == 200


def test_employee_header_hides_manager_only_tabs(client):
    """Employees see Today, Import, End Day, History, Trash and their own
    Settings (theme) tab but not the Vehicles or Staff tabs."""
    html = client.get("/").data.decode()
    for href in ['href="/vehicles"', 'href="/employees"']:
        assert href not in html
    for href in ['href="/import"', 'href="/end"', 'href="/history"',
                 'href="/trash"', 'href="/settings"']:
        assert href in html


def test_manager_header_shows_all_tabs(manager_client):
    html = manager_client.get("/").data.decode()
    for href in ['href="/vehicles"', 'href="/employees"', 'href="/settings"',
                 'href="/import"', 'href="/end"', 'href="/history"',
                 'href="/trash"']:
        assert href in html


# ---------------------------------------------------------------------------
# Force-complete ("Done") button
# ---------------------------------------------------------------------------

def test_complete_entry_marks_completed_even_with_open_tasks(client, app):
    """A vehicle can be completed before all tasks are done; incomplete
    tasks stay unchecked so the manager can see what wasn't finished."""
    from app.models import Employee

    with app.app_context():
        emp = Employee(name="Pat Smith")
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id

        from app.services.vehicles import find_or_create_vehicle
        from app.services import schedule as ss
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v, _ = find_or_create_vehicle("880", location_id=loc.id)
        entry = ss.ensure_entry(sched, v)
        entry_id = entry.id
        # Complete only one task, then mark the vehicle done.
        client.post(f"/task/{entry_id}/Sweep",
                    data={"checked": "true", "employee_id": str(emp_id)})
        emp.current_vehicle_id = v.id
        db.session.commit()

    r = client.post(f"/entry/{entry_id}/complete")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    with app.app_context():
        e = ScheduleEntry.query.get(entry_id)
        assert e.status == "completed"
        done, total, pct = sched_svc.entry_progress(e)
        assert 0 < done < total
        incomplete = sorted(t.task_name for t in e.tasks if not t.completed)
        assert incomplete  # some tasks left undone
        assert employee_is_free(app, emp_id)


def employee_is_free(app, emp_id):
    from app.models import Employee
    return Employee.query.get(emp_id).current_vehicle_id is None


def test_manager_cannot_complete_entry(manager_client, app):
    """Managers are read-only; the Done endpoint rejects them."""
    from app.models import Employee

    with app.app_context():
        emp = Employee(name="Ron Manager")
        db.session.add(emp)
        db.session.commit()

        from app.services.vehicles import find_or_create_vehicle
        from app.services import schedule as ss
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v, _ = find_or_create_vehicle("881", location_id=loc.id)
        entry = ss.ensure_entry(sched, v)
        entry_id = entry.id
        entry.status = "in_progress"
        emp.current_vehicle_id = v.id
        db.session.commit()

    r = manager_client.post(f"/entry/{entry_id}/complete")
    assert r.status_code == 403
    with app.app_context():
        assert ScheduleEntry.query.get(entry_id).status == "in_progress"


def test_done_button_and_not_completed_shown_to_manager(client, app):
    """The Done button appears on started vehicles for employees, and the
    manager dashboard lists which tasks were not completed."""
    from app.models import Employee

    with app.app_context():
        emp = Employee(name="Cal Rider")
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id

        from app.services.vehicles import find_or_create_vehicle
        from app.services import schedule as ss
        loc = vehicles_loc(app)
        sched = ss.get_or_create_schedule(location=loc)
        v, _ = find_or_create_vehicle("882", location_id=loc.id)
        entry = ss.ensure_entry(sched, v)
        entry_id = entry.id

    # Employee view: Done button present once the vehicle is started.
    html = client.get("/").data.decode()
    assert html.find(">Done</button>") == -1
    r = client.post("/start-work", data={"employee_id": str(emp_id),
                                         "entry_id": str(entry_id)})
    assert r.get_json()["ok"] is True
    html = client.get("/").data.decode()
    assert 'class="btn success done-btn"' in html

    # Check off a single task, then press Done.
    client.post(f"/task/{entry_id}/Sweep",
                data={"checked": "true", "employee_id": str(emp_id)})
    client.post(f"/entry/{entry_id}/complete")

    # Manager sees exactly which tasks were left undone.
    m = app.test_client()
    m.post("/login", data={"username": "manager", "password": "manager"})
    html = m.get("/").data.decode()
    incomplete = sorted(
        t.task_name for t in sched_svc_entry_tasks(app, entry_id)
        if not t.completed)
    assert incomplete
    assert "Not completed" in html
    for t in incomplete:
        assert t in html


def _make_report_pdf(unit_lines):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Unit  Type  Route")
    y = 100
    for line in unit_lines:
        page.insert_text((72, y), line)
        y += 30
    return doc.tobytes()


def test_import_apply_tracks_who_imported(app, client):
    """The importer is recorded and shown: Manager or a specific employee."""
    from app.models import PrepReportImport, Employee
    data = _make_report_pdf(["100   Coach  R1"])

    # Manager imports and applies -> recorded as the Manager account.
    m = app.test_client()
    m.post("/login", data={"username": "manager", "password": "manager"})
    r = m.post("/import", data={
        "pdf": (io.BytesIO(data), "prep.pdf"),
        "sched_date": date.today().isoformat(),
        "imported_by": "manager",
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    with app.app_context():
        imp1 = PrepReportImport.query.order_by(PrepReportImport.id.desc()).first()
        iid = imp1.id
        assert imp1.employee_id is None
    m.post(f"/import/{iid}/apply", data={"imported_by": "manager"})
    with app.app_context():
        assert PrepReportImport.query.get(iid).employee_id is None
    html = m.get("/history").data.decode()
    assert ">Manager</td>" in html

    # A specific employee imports and applies -> recorded as that employee.
    with app.app_context():
        emp = Employee.query.filter_by(active=True).first()
        emp_id = emp.id
        emp_name = emp.name
    e = app.test_client()
    e.post("/login", data={"username": "employee", "password": "employee"})
    e.post("/select", data={"employee_id": str(emp_id)})
    r = e.post("/import", data={
        "pdf": (io.BytesIO(data), "prep2.pdf"),
        "sched_date": date.today().isoformat(),
        "imported_by": f"employee:{emp_id}",
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    with app.app_context():
        imp2 = PrepReportImport.query.order_by(PrepReportImport.id.desc()).first()
        iid2 = imp2.id
        assert imp2.employee_id == emp_id
    e.post(f"/import/{iid2}/apply", data={"imported_by": f"employee:{emp_id}"})
    with app.app_context():
        assert PrepReportImport.query.get(iid2).employee_id == emp_id
    html = e.get("/history").data.decode()
    assert emp_name in html
    assert f">{emp_name}</td>" in html


# ---------------------------------------------------------------------------
# Prep timers: Start -> Pause -> Resume -> Done
# ---------------------------------------------------------------------------

def prep_entry(app, unit, prep_time=None, vehicle_type="Coach"):
    """A vehicle on today's board, ready to be worked on."""
    from app.services import schedule as ss
    from app.services.vehicles import find_or_create_vehicle
    loc = vehicles_loc(app)
    v, _ = find_or_create_vehicle(unit, vehicle_type=vehicle_type,
                                 location_id=loc.id)
    sched = ss.get_or_create_schedule(location=loc)
    entry = ss.ensure_entry(sched, v, prep_time=prep_time)
    db.session.commit()
    return entry.id, v.id


def add_employee(app, name="Dana Timer"):
    emp = Employee(name=name)
    db.session.add(emp)
    db.session.commit()
    return emp.id


def test_prep_start_records_eastern_timestamp_and_employee(client, app):
    """Start begins the clock, records the employee, and stores an Eastern
    timestamp that carries its UTC offset (so it is unambiguous later)."""
    from app.models import PrepSession, PrepSessionEvent
    from app.services import timeutils

    with app.app_context():
        entry_id, _ = prep_entry(app, "900")
        emp_id = add_employee(app)

    r = client.post(f"/entry/{entry_id}/prep/start",
                    data={"employee_id": str(emp_id)})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["state"]["status"] == "running"
    assert body["state"]["employee"] == "Dana Timer"

    with app.app_context():
        sess = PrepSession.query.filter_by(entry_id=entry_id).first()
        assert sess.status == "running"
        assert sess.total_seconds == 0
        # Stored timezone-aware: the ISO string ends in an Eastern offset.
        assert sess.started_at == timeutils.store_ts(
            timeutils.load_ts(sess.started_at))
        offset = timeutils.load_ts(sess.started_at).strftime("%z")
        assert offset in ("-0400", "-0500")  # EDT or EST
        assert [e.event_type for e in sess.events] == ["start"]
        assert sess.events[0].employee_id == emp_id
        # Started also marks the vehicle in progress and the employee busy.
        entry = ScheduleEntry.query.get(entry_id)
        assert entry.status == "in_progress"
        assert Employee.query.get(emp_id).current_vehicle_id == entry.vehicle_id


def test_prep_workflow_start_pause_resume_done(client, app):
    """The full workflow: Start -> Pause -> Resume -> Done, with the total
    active prep time being the sum of the active segments only."""
    from app.models import PrepSession
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "901")
        emp_id = add_employee(app)
        t0 = timeutils.now_eastern().replace(microsecond=0)
        # Drive the service directly so the elapsed totals are exact.
        entry = ScheduleEntry.query.get(entry_id)
        prep_timer.start(entry, emp_id, at=t0)
        prep_timer.pause(entry, emp_id, at=t0 + timedelta(minutes=10))
        assert prep_timer.elapsed_seconds(
            prep_timer.session_for(entry), at=t0 + timedelta(minutes=25)) == 600
        prep_timer.resume(entry, emp_id, at=t0 + timedelta(minutes=25))
        prep_timer.finish(entry, emp_id, at=t0 + timedelta(minutes=40))
        sess = prep_timer.session_for(entry)
        assert sess.status == "finished"
        # 10 minutes before the pause, then 15 more after the resume; the
        # 15 paused minutes in between are never billed.
        assert sess.total_seconds == 25 * 60
        assert [e.event_type for e in sess.events] == [
            "start", "pause", "resume", "done"]
        assert sess.events[1].total_seconds == 600
        assert sess.events[3].total_seconds == 1500
        # Every event carries an Eastern 12-hour label for the report.
        labels = [e["label"] for e in prep_timer.state(entry, at=sess and None)["events"]]
        assert labels == ["Started", "Paused", "Resumed", "Done"]

    # The board buttons drive the same workflow over HTTP.
    r = client.post(f"/entry/{entry_id}/prep/pause",
                    data={"employee_id": str(emp_id)})
    assert r.status_code == 409
    assert "already finished" in r.get_json()["error"]


def test_prep_resume_keeps_previous_work_time(client, app):
    """Resume never loses the time already worked."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "902")
        emp_id = add_employee(app)
        entry = ScheduleEntry.query.get(entry_id)

    client.post(f"/entry/{entry_id}/prep/start",
                data={"employee_id": str(emp_id)})
    with app.app_context():
        entry = ScheduleEntry.query.get(entry_id)
        prep_timer.pause(entry, emp_id, at=timeutils.now_eastern())
    with app.app_context():
        sess = prep_timer.session_for(ScheduleEntry.query.get(entry_id))
        banked = int(sess.total_seconds or 0)
    r = client.post(f"/entry/{entry_id}/prep/resume",
                    data={"employee_id": str(emp_id)})
    assert r.get_json()["state"]["status"] == "running"
    with app.app_context():
        sess = prep_timer.session_for(ScheduleEntry.query.get(entry_id))
        assert sess.total_seconds == banked  # previous work kept
        assert sess.status == "running"
        elapsed = prep_timer.elapsed_seconds(sess)
        assert elapsed >= banked


def test_prep_done_stops_timer_marks_vehicle_and_reports_total(client, app):
    from app.models import PrepSession
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "903")
        emp_id = add_employee(app)
        entry = ScheduleEntry.query.get(entry_id)
        t0 = timeutils.now_eastern().replace(microsecond=0)
        prep_timer.start(entry, emp_id, at=t0)

    r = client.post(f"/entry/{entry_id}/prep/done",
                    data={"employee_id": str(emp_id)})
    assert r.status_code == 200
    body = r.get_json()
    assert body["state"]["status"] == "finished"
    assert body["state"]["finished"]           # 12-hour Eastern finish time
    assert body["state"]["total_label"]
    assert body["counters"]["completed"] == 1
    with app.app_context():
        entry = ScheduleEntry.query.get(entry_id)
        assert entry.status == "completed"
        sess = PrepSession.query.filter_by(entry_id=entry_id).first()
        assert sess.status == "finished"
        assert sess.finished_at is not None
        # No timer keeps running after Done.
        assert prep_timer.elapsed_seconds(sess) == sess.total_seconds
        assert Employee.query.get(emp_id).current_vehicle_id is None


def test_prep_invalid_actions_are_rejected(client, app):
    """Starting a running vehicle, pausing/resuming in the wrong state, and
    finishing a vehicle that was never started are all refused."""
    with app.app_context():
        running_id, _ = prep_entry(app, "904")
        fresh_id, _ = prep_entry(app, "905")
        emp_id = add_employee(app)

    # Never started: pause, resume and done are all invalid.
    for action in ("pause", "resume", "done"):
        r = client.post(f"/entry/{fresh_id}/prep/{action}",
                        data={"employee_id": str(emp_id)})
        assert r.status_code == 409
        assert "never started" in r.get_json()["error"]

    # Start, then start again: the second start is refused.
    assert client.post(f"/entry/{running_id}/prep/start",
                       data={"employee_id": str(emp_id)}).status_code == 200
    r = client.post(f"/entry/{running_id}/prep/start",
                    data={"employee_id": str(emp_id)})
    assert r.status_code == 409
    assert "already started" in r.get_json()["error"]
    assert r.get_json()["state"]["status"] == "running"

    # Resume while running is refused; pause twice is refused.
    r = client.post(f"/entry/{running_id}/prep/resume",
                    data={"employee_id": str(emp_id)})
    assert r.status_code == 409
    assert "already running" in r.get_json()["error"]
    assert client.post(f"/entry/{running_id}/prep/pause",
                       data={"employee_id": str(emp_id)}).status_code == 200
    r = client.post(f"/entry/{running_id}/prep/pause",
                    data={"employee_id": str(emp_id)})
    assert r.status_code == 409
    assert "already paused" in r.get_json()["error"]

    # Done twice: the second is refused.
    assert client.post(f"/entry/{running_id}/prep/done",
                       data={"employee_id": str(emp_id)}).status_code == 200
    r = client.post(f"/entry/{running_id}/prep/done",
                    data={"employee_id": str(emp_id)})
    assert r.status_code == 409
    assert "already finished" in r.get_json()["error"]


def test_prep_timers_are_independent_per_vehicle(client, app):
    """Several vehicles are worked on at the same time, each with its own
    clock, employee and total."""
    from app.services import prep_timer

    with app.app_context():
        first_id, _ = prep_entry(app, "906")
        second_id, _ = prep_entry(app, "907")
        emp_a = add_employee(app, "Ann Alpha")
        emp_b = add_employee(app, "Bob Beta")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        prep_timer.start(ScheduleEntry.query.get(first_id), emp_a, at=t0)
        prep_timer.start(ScheduleEntry.query.get(second_id), emp_b,
                         at=t0 + timedelta(minutes=2))
        prep_timer.pause(ScheduleEntry.query.get(first_id), emp_a,
                         at=t0 + timedelta(minutes=12))
        prep_timer.finish(ScheduleEntry.query.get(second_id), emp_b,
                          at=t0 + timedelta(minutes=22))
        a = prep_timer.session_for(ScheduleEntry.query.get(first_id))
        b = prep_timer.session_for(ScheduleEntry.query.get(second_id))
        assert a.total_seconds == 12 * 60
        assert b.total_seconds == 20 * 60
        assert a.employee.name == "Ann Alpha"
        assert b.employee.name == "Bob Beta"
        # Total for the day is the sum of both vehicles' active prep time.
        assert prep_timer.total_active_seconds(a.entry.schedule) == 32 * 60


# ---------------------------------------------------------------------------
# Two independent clock sets per vehicle: Inside and Outside
# ---------------------------------------------------------------------------

def test_each_clock_set_is_timed_separately(client, app):
    """A vehicle is timed once for the work inside it and once for the work
    outside it. The two sets keep their own clocks, totals and event logs, and
    neither borrows a second from the other."""
    from app.models import PrepSession
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "940")
        emp_id = add_employee(app)
        entry = ScheduleEntry.query.get(entry_id)
        t0 = timeutils.now_eastern().replace(microsecond=0)
        inside = prep_timer.start(entry, emp_id, at=t0,
                                  scope=prep_timer.INSIDE)
        # One person never runs two clocks at once, so the outside side is taken
        # up after pausing the inside one.
        with pytest.raises(prep_timer.PrepTimerError):
            prep_timer.start(entry, emp_id, at=t0 + timedelta(minutes=1),
                             scope=prep_timer.OUTSIDE)
        prep_timer.pause(entry, emp_id, at=t0 + timedelta(minutes=5),
                         session_id=inside.id)
        outside = prep_timer.start(entry, emp_id, at=t0 + timedelta(minutes=5),
                                   scope=prep_timer.OUTSIDE)
        # And the paused inside clock cannot be resumed while the outside one
        # runs, which would be two clocks at once.
        with pytest.raises(prep_timer.PrepTimerError):
            prep_timer.resume(entry, emp_id, at=t0 + timedelta(minutes=6),
                              session_id=inside.id)
        # Nor can a second outside clock be started over the running one.
        with pytest.raises(prep_timer.PrepTimerError):
            prep_timer.start(entry, emp_id, at=t0 + timedelta(minutes=6),
                             scope=prep_timer.OUTSIDE)

        prep_timer.finish(entry, emp_id, at=t0 + timedelta(minutes=10),
                          session_id=outside.id)
        # Now the inside side runs on its own again.
        prep_timer.resume(entry, emp_id, at=t0 + timedelta(minutes=20),
                          session_id=inside.id)
        prep_timer.finish(entry, emp_id, at=t0 + timedelta(minutes=30),
                          session_id=inside.id)

        # One session per employee per clock set, each logging only its own
        # events, with its own total.
        sessions = {s.scope: s for s in
                    PrepSession.query.filter_by(entry_id=entry_id).all()}
        assert sorted(sessions) == ["inside", "outside"]
        assert inside.id != outside.id
        assert [e.event_type for e in sessions["inside"].events] == \
            ["start", "pause", "resume", "done"]
        assert [e.event_type for e in sessions["outside"].events] == \
            ["start", "done"]
        state = prep_timer.state(entry)
        assert state["scopes"]["inside"]["elapsed"] == 15 * 60   # 5 + 10
        assert state["scopes"]["outside"]["elapsed"] == 5 * 60
        assert state["elapsed"] == 20 * 60
        assert state["worker_count"] == 2
        assert prep_timer.scope_totals(entry.schedule) == \
            {"inside": 15 * 60, "outside": 5 * 60}
        # The day's two totals together are the day's total.
        assert prep_timer.total_active_seconds(entry.schedule) == 20 * 60
        # The service itself never closes the board row.
        assert entry.status == "in_progress"


def test_prep_route_acts_on_the_clock_set_it_is_given(client, app):
    """A press names the clock set it belongs to: it moves that set's clock and
    leaves the other set alone, and the vehicle is only finished on the board
    once no clock of either set is left running."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "944")
        emp_id = add_employee(app)
        entry = ScheduleEntry.query.get(entry_id)
        t0 = timeutils.now_eastern().replace(microsecond=0)
        inside = prep_timer.start(entry, emp_id, at=t0)
        prep_timer.pause(entry, emp_id, at=t0 + timedelta(minutes=5),
                         session_id=inside.id)

    # An outside Start opens only that set and leaves the paused inside clock.
    r = client.post(f"/entry/{entry_id}/prep/start",
                    data={"employee_id": str(emp_id), "scope": "outside"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["scope"] == "outside"
    assert body["state"]["scopes"]["outside"]["status"] == "running"
    assert body["state"]["scopes"]["inside"]["status"] == "paused"
    assert body["state"]["scopes"]["outside"]["worker_count"] == 1
    assert body["state"]["scopes"]["inside"]["worker_count"] == 1

    # Closing the outside set leaves the vehicle open: the inside clock of this
    # vehicle is still on, and only the route can complete the row.
    r = client.post(f"/entry/{entry_id}/prep/done",
                    data={"employee_id": str(emp_id), "scope": "outside"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["scope"] == "outside"
    assert body["state"]["scopes"]["outside"]["status"] == "finished"
    assert body["state"]["scopes"]["inside"]["status"] == "paused"
    assert body["entry_completed"] is False
    assert body["still_working"] is True
    with app.app_context():
        assert ScheduleEntry.query.get(entry_id).status == "in_progress"

    # The last clock of either set to close completes the vehicle.
    r = client.post(f"/entry/{entry_id}/prep/done",
                    data={"employee_id": str(emp_id), "scope": "inside"})
    body = r.get_json()
    assert body["state"]["scopes"]["inside"]["status"] == "finished"
    assert body["entry_completed"] is True
    assert body["still_working"] is False
    with app.app_context():
        assert ScheduleEntry.query.get(entry_id).status == "completed"


def test_a_paused_clock_does_not_block_the_other_clock_set(client, app):
    """Paused time is not active prep time, and the other set does not wait
    for it: pausing the inside clock lets the same person time the outside."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "941")
        emp_id = add_employee(app)
        entry = ScheduleEntry.query.get(entry_id)
        t0 = timeutils.now_eastern().replace(microsecond=0)
        inside = prep_timer.start(entry, emp_id, at=t0)
        # A second running clock is refused while the first is running.
        with pytest.raises(prep_timer.PrepTimerError):
            prep_timer.start(entry, emp_id, at=t0, scope=prep_timer.OUTSIDE)
        prep_timer.pause(entry, emp_id, at=t0 + timedelta(minutes=5),
                         session_id=inside.id)
        outside = prep_timer.start(entry, emp_id, at=t0 + timedelta(minutes=5),
                                   scope=prep_timer.OUTSIDE)
        assert outside.scope == "outside"

        # A finished clock cannot be started over either.
        prep_timer.finish(entry, emp_id, at=t0 + timedelta(minutes=10),
                          session_id=outside.id)
        with pytest.raises(prep_timer.PrepTimerError):
            prep_timer.start(entry, emp_id, at=t0 + timedelta(minutes=10),
                             scope=prep_timer.OUTSIDE)
        state = prep_timer.state(entry)
        # The paused inside clock froze at 5 minutes; the outside one recorded
        # its own 5 minutes. Neither set borrows the other's time.
        assert state["scopes"]["inside"]["elapsed"] == 5 * 60
        assert state["scopes"]["outside"]["elapsed"] == 5 * 60
        assert state["elapsed"] == 10 * 60


def test_two_employees_time_different_sides_of_one_vehicle(client, app):
    """A crew can work a vehicle from both sides at once: Ann times the inside,
    Bob the outside, and each is only ever shown the buttons of their own set."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "942")
        ann = add_employee(app, "Ann Alpha")
        bob = add_employee(app, "Bob Beta")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        entry = ScheduleEntry.query.get(entry_id)
        prep_timer.start(entry, ann, at=t0, scope=prep_timer.INSIDE)
        prep_timer.start(entry, bob, at=t0, scope=prep_timer.OUTSIDE)
        db.session.commit()

    def set_html(scope, client_):
        html = client_.get("/").data.decode()
        start = html.index(f'id="prep-{entry_id}-{scope}"')
        return html[start:html.index("</section>", start)]

    c = app.test_client()
    c.post("/login", data={"username": "employee", "password": "employee"})
    c.post("/select", data={"employee_id": str(ann)})
    assert "Ann Alpha" in set_html("inside", c)
    assert "Bob Beta" not in set_html("inside", c)
    assert "Your inside clock for this vehicle is in the list above" \
        in set_html("inside", c)
    # Ann is on the inside, and the outside side is somebody else's, so she is
    # offered a clock of her own there rather than a Start.
    assert "+ Add Me" in set_html("outside", c)
    assert 'data-prep-scope="outside"' in set_html("outside", c)

    c = app.test_client()
    c.post("/login", data={"username": "employee", "password": "employee"})
    c.post("/select", data={"employee_id": str(bob)})
    assert "Bob Beta" in set_html("outside", c)
    assert "Ann Alpha" not in set_html("outside", c)
    assert "Your outside clock for this vehicle is in the list above" \
        in set_html("outside", c)
    assert "+ Add Me" in set_html("inside", c)

    # The vehicle total is the sum of both sides, each counted once.
    payload = client.get("/prep/active").get_json()
    row = payload["sessions"][str(entry_id)]
    assert row["elapsed"] < 60        # both clocks only just started
    assert row["scopes"]["inside"]["status"] == "running"
    assert row["scopes"]["outside"]["status"] == "running"
    assert row["scopes"]["inside"]["workers"][0]["employee"] == "Ann Alpha"
    assert row["scopes"]["outside"]["workers"][0]["employee"] == "Bob Beta"
    assert payload["workers"][str(ann)]["scope"] == "inside"
    assert payload["workers"][str(bob)]["scope"] == "outside"


def test_board_and_reports_split_the_day_by_clock_set(client, app):
    """The board, the end-of-day summary and the printable report all show the
    inside and outside prep time of the day apart."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "943")
        emp_id = add_employee(app)
        entry = ScheduleEntry.query.get(entry_id)
        t0 = timeutils.now_eastern().replace(microsecond=0)
        inside = prep_timer.start(entry, emp_id, at=t0)
        prep_timer.finish(entry, emp_id, at=t0 + timedelta(minutes=20),
                          session_id=inside.id)
        outside = prep_timer.start(entry, emp_id, at=t0 + timedelta(minutes=30),
                                   scope=prep_timer.OUTSIDE)
        prep_timer.finish(entry, emp_id, at=t0 + timedelta(minutes=45),
                          session_id=outside.id)
        db.session.commit()

    html = client.get("/").data.decode()
    assert "Inside Prep" in html
    assert "Outside Prep" in html
    assert "20m 00s" in html and "15m 00s" in html

    # The end-of-day summary and the printable report list every clock once,
    # labelled with the side it timed, and total each side of the day apart.
    assert "Inside Prep" in client.get("/end").data.decode()
    for page in (client.get("/end").data.decode(),
                 client.get(f"/print/{date.today().isoformat()}").data.decode()):
        assert "Clock Set" in page
        assert "20m 00s" in page and "15m 00s" in page
        assert "Inside" in page and "Outside" in page

    with app.app_context():
        vehicle = ScheduleEntry.query.get(entry_id).vehicle
        history = prep_timer.vehicle_history(vehicle)
        assert [h["scope_label"] for h in history] == ["Outside", "Inside"]
        totals = prep_timer.vehicle_scope_totals(vehicle)
        assert totals == {"inside": 20 * 60, "outside": 15 * 60}


def test_prep_timer_keeps_counting_after_a_reload(client, app):
    """A refresh re-reads the clock from the server, so time keeps counting
    (and the board's live timer is handed the data it needs to tick)."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "908")
        emp_id = add_employee(app)
        t0 = timeutils.now_eastern().replace(microsecond=0) - timedelta(minutes=5)
        prep_timer.start(ScheduleEntry.query.get(entry_id), emp_id, at=t0)

    html = client.get("/").data.decode()
    # The rendered row carries the running state plus the segment timestamp
    # the browser adds to its own clock.
    assert 'data-status="running"' in html
    assert "data-segment-epoch=" in html
    assert "Timer running" in html
    # Five minutes of work are already on the clock (t0 is truncated to the
    # second, so the render may land a tick either side of the boundary).
    assert any(label in html for label in ("04:58", "04:59", "05:00", "05:01"))

    state = client.get(f"/prep/active").get_json()
    assert state["ok"] is True
    row = state["sessions"][str(entry_id)]
    assert row["status"] == "running"
    assert 290 <= row["elapsed"] <= 400
    assert row["segment_epoch"] is not None
    assert row["base_seconds"] == 0


def test_prep_board_shows_buttons_for_the_current_state(client, app):
    """Start before work, Pause while running, Resume while paused, Done to
    finish, and a final total afterwards.

    Every vehicle carries two independent clock sets, so each of them starts
    with its own Start and the actions of one set never appear on the other."""
    with app.app_context():
        entry_id, _ = prep_entry(app, "909")
        emp_id = add_employee(app)

    def row_html():
        html = client.get("/").data.decode()
        start = html.index(f'id="prep-{entry_id}"')
        return html[start:html.index('class="progress"', start)]

    def set_html(scope):
        html = client.get("/").data.decode()
        start = html.index(f'id="prep-{entry_id}-{scope}"')
        return html[start:html.index("</section>", start)]

    # Both clock sets wait for a Start, each naming the set it times.
    assert 'data-prep-scope="inside"' in row_html()
    assert 'data-prep-scope="outside"' in row_html()
    assert "Start Inside" in set_html("inside")
    assert "Start Outside" in set_html("outside")
    assert 'data-prep-action="done"' not in row_html()

    client.post(f"/entry/{entry_id}/prep/start",
                data={"employee_id": str(emp_id)})
    html = set_html("inside")
    assert 'data-prep-action="pause"' in html
    assert 'data-prep-action="done"' in html
    assert 'data-prep-action="start"' not in html
    # The other set is untouched and still waiting for its own Start.
    assert 'data-prep-action="start"' in set_html("outside")
    assert 'data-prep-action="pause"' not in set_html("outside")

    client.post(f"/entry/{entry_id}/prep/pause",
                data={"employee_id": str(emp_id), "scope": "inside"})
    html = set_html("inside")
    assert 'data-prep-action="resume"' in html
    assert "Paused" in html

    client.post(f"/entry/{entry_id}/prep/resume",
                data={"employee_id": str(emp_id), "scope": "inside"})
    assert 'data-prep-action="pause"' in set_html("inside")

    client.post(f"/entry/{entry_id}/prep/done",
                data={"employee_id": str(emp_id), "scope": "inside"})
    html = row_html()
    assert "Completed" in html
    assert "Inside prep history (4)" in html    # start, pause, resume, done
    assert "Started" in html and "Resumed" in html
    # The finished set offers nothing; the untouched one still does.
    assert 'data-prep-action="start"' not in set_html("inside")
    assert 'data-prep-action="start"' in set_html("outside")


def test_prep_report_shows_history_and_total(client, app):
    """The prep report lists the Start/Pause/Resume/Done history and the total
    active prep time, and shows report times as Eastern 12-hour AM/PM."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "910", prep_time="04:30")
        emp_id = add_employee(app, "Kim Prep")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        entry = ScheduleEntry.query.get(entry_id)
        prep_timer.start(entry, emp_id, at=t0)
        prep_timer.pause(entry, emp_id, at=t0 + timedelta(minutes=5))
        prep_timer.resume(entry, emp_id, at=t0 + timedelta(minutes=20))
        prep_timer.finish(entry, emp_id, at=t0 + timedelta(minutes=35))
        db.session.commit()

    html = client.get(f"/print/{date.today().isoformat()}").data.decode()
    assert "Prep Time Log" in html
    assert "Prep Event Detail" in html
    assert "Kim Prep" in html
    assert "Paused" in html and "Resumed" in html and "Done" in html
    assert "20m 00s" in html                    # 5 + 15 minutes of active work
    assert "total active prep time" in html
    # The 24-hour report time is displayed as Eastern 12-hour AM/PM.
    assert "4:30 AM" in html
    assert "04:30" not in html

    # The end-of-day summary carries the same totals.
    end_html = client.get("/end").data.decode()
    assert "Prep Time Log" in end_html
    assert "20m 00s" in end_html


def test_prep_history_kept_on_vehicle_page(manager_client, app):
    """Every run is permanent history on the vehicle, not just today's board."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, vehicle_id = prep_entry(app, "911")
        emp_id = add_employee(app, "Lee Historian")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        prep_timer.start(ScheduleEntry.query.get(entry_id), emp_id, at=t0)
        prep_timer.finish(ScheduleEntry.query.get(entry_id), emp_id,
                          at=t0 + timedelta(minutes=7))

    html = manager_client.get(f"/vehicles/{vehicle_id}").data.decode()
    assert "Prep Time History" in html
    assert "Lee Historian" in html
    assert "7m 00s" in html


def test_completing_a_vehicle_stops_its_timer(client, app):
    """Finishing a vehicle any other way (full checklist, /complete) also stops
    the clock instead of leaving it running forever."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "912")
        emp_id = add_employee(app)
        client.post(f"/entry/{entry_id}/prep/start",
                    data={"employee_id": str(emp_id)})

    # Every checklist item checked -> the entry completes itself.
    with app.app_context():
        entry = ScheduleEntry.query.get(entry_id)
        for t in entry.tasks:
            t.completed = True
        db.session.commit()
    client.post(f"/task/{entry_id}/Sweep", data={"checked": "true",
                                                 "employee_id": str(emp_id)})
    with app.app_context():
        entry = ScheduleEntry.query.get(entry_id)
        assert entry.status == "completed"
    r = client.post(f"/entry/{entry_id}/complete")
    assert r.status_code == 200
    with app.app_context():
        sess = prep_timer.session_for(ScheduleEntry.query.get(entry_id))
        assert sess is not None
        assert sess.status == "finished"
        assert sess.finished_at is not None


def test_manager_cannot_use_prep_timers(manager_client, app):
    """Timers are read-only for the Manager account."""
    with app.app_context():
        entry_id, _ = prep_entry(app, "913")
        emp_id = add_employee(app)
    for action in ("start", "pause", "resume", "done"):
        r = manager_client.post(f"/entry/{entry_id}/prep/{action}",
                                data={"employee_id": str(emp_id)})
        assert r.status_code == 403
    r = manager_client.post("/start-work", data={
        "employee_id": str(emp_id), "entry_id": str(entry_id)})
    assert r.status_code == 403
    with app.app_context():
        from app.models import PrepSession
        assert PrepSession.query.count() == 0


def test_prep_time_labels_are_12_hour_eastern():
    """Report times are shown 12-hour AM/PM without touching what is stored."""
    from app.services import timeutils
    assert timeutils.prep_time_label("04:30") == "4:30 AM"
    assert timeutils.prep_time_label("13:45") == "1:45 PM"
    assert timeutils.prep_time_label("16:05") == "4:05 PM"
    assert timeutils.prep_time_label("4:05 PM") == "4:05 PM"
    assert timeutils.prep_time_label("0430") == "4:30 AM"
    assert timeutils.prep_time_label("as directed") == "as directed"
    assert timeutils.prep_time_label(None) == "—"
    # A full timestamp is converted into Eastern time for display.
    assert timeutils.prep_time_label("2026-01-15T20:05:00+00:00") == "3:05 PM"


def test_prep_timestamps_are_timezone_aware():
    """Everything the timer records is Eastern Time, and reads back the same
    instant no matter which offset was in force."""
    from app.services import timeutils
    stamp = timeutils.store_ts(datetime(2026, 1, 15, 12, 0))   # EST
    summer = timeutils.store_ts(datetime(2026, 7, 15, 12, 0))   # EDT
    assert stamp.endswith("-05:00")
    assert summer.endswith("-04:00")
    assert timeutils.fmt_time(stamp) == "12:00 PM"
    assert timeutils.fmt_time(summer) == "12:00 PM"
    assert timeutils.to_eastern("2026-07-15T16:05:00Z").hour == 12


# ---------------------------------------------------------------------------
# More than one employee on the same vehicle
# ---------------------------------------------------------------------------

def test_two_employees_work_the_same_vehicle_at_once(client, app):
    """A second employee can start the vehicle somebody else is already on."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "920")
        ann = add_employee(app, "Ann Alpha")
        bob = add_employee(app, "Bob Beta")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        entry = ScheduleEntry.query.get(entry_id)
        prep_timer.start(entry, ann, at=t0)
        prep_timer.start(entry, bob, at=t0 + timedelta(minutes=1))

        # Each employee has their own session, clock and total.
        sessions = prep_timer.sessions_for(entry)
        assert len(sessions) == 2
        assert {s.employee_id for s in sessions} == {ann, bob}
        assert all(s.status == "running" for s in sessions)
        assert [e.employee_id for e in sessions[0].events] == [ann]
        # The board state carries one clock per employee plus the vehicle total.
        state = prep_timer.state(entry, at=t0 + timedelta(minutes=6))
        assert state["worker_count"] == 2
        assert state["running_count"] == 2
        assert state["status"] == "running"
        assert [w["employee"] for w in state["workers"]] == [
            "Ann Alpha", "Bob Beta"]
        # 6 minutes for Ann (started first) and 5 for Bob (joined a minute in).
        assert [w["elapsed"] for w in state["workers"]] == [6 * 60, 5 * 60]
        assert state["elapsed"] == 11 * 60
        assert prep_timer.total_active_seconds(
            entry.schedule, at=t0 + timedelta(minutes=6)) == 11 * 60
        # Both employees are on the board as working on it.
        assert Employee.query.get(ann).current_vehicle_id == entry.vehicle_id
        assert Employee.query.get(bob).current_vehicle_id == entry.vehicle_id


def test_one_employee_cannot_start_the_same_vehicle_twice(client, app):
    """Joining a vehicle somebody else works is fine; starting it again yourself
    is not."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "921")
        ann = add_employee(app, "Ann Alpha")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        entry = ScheduleEntry.query.get(entry_id)
        prep_timer.start(entry, ann, at=t0)

    r = client.post(f"/entry/{entry_id}/prep/start",
                    data={"employee_id": str(ann)})
    assert r.status_code == 409
    assert "already started for you" in r.get_json()["error"]
    # Once finished, the same employee cannot re-open the vehicle either.
    with app.app_context():
        entry = ScheduleEntry.query.get(entry_id)
        prep_timer.finish(entry, ann, at=t0 + timedelta(minutes=30))
    r = client.post(f"/entry/{entry_id}/prep/start",
                    data={"employee_id": str(ann)})
    assert r.status_code == 409
    assert "already been finished" in r.get_json()["error"]


def test_each_employee_pauses_and_finishes_their_own_clock(client, app):
    """Pausing / resuming / finishing one person's clock leaves everyone else's
    running, and the vehicle is only complete once the last one is done."""
    from app.models import PrepSession
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "922")
        ann = add_employee(app, "Ann Alpha")
        bob = add_employee(app, "Bob Beta")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        entry = ScheduleEntry.query.get(entry_id)
        ann_session = prep_timer.start(entry, ann, at=t0).id
        bob_session = prep_timer.start(entry, bob, at=t0).id

    # Ann pauses her own clock (the button in her row carries her session).
    r = client.post(f"/entry/{entry_id}/prep/pause",
                    data={"employee_id": str(ann),
                          "session_id": str(ann_session)})
    assert r.status_code == 200
    state = r.get_json()["state"]
    assert state["running_count"] == 1
    assert {w["status"] for w in state["workers"]} == {"paused", "running"}
    with app.app_context():
        assert PrepSession.query.get(ann_session).status == "paused"
        assert PrepSession.query.get(bob_session).status == "running"

    # Ann is done: her clock freezes and she leaves the floor, while Bob keeps
    # working and the vehicle is not complete yet.
    with app.app_context():
        entry = ScheduleEntry.query.get(entry_id)
        prep_timer.finish(entry, ann, at=t0 + timedelta(minutes=10),
                          session_id=ann_session)
        assert Employee.query.get(ann).current_vehicle_id is None
        assert Employee.query.get(bob).current_vehicle_id == entry.vehicle_id
        assert entry.status == "in_progress"
    r = client.post(f"/entry/{entry_id}/prep/done",
                    data={"employee_id": str(bob),
                          "session_id": str(bob_session)})
    assert r.status_code == 200
    body = r.get_json()
    assert body["entry_completed"] is True
    assert body["counters"]["completed"] == 1
    with app.app_context():
        entry = ScheduleEntry.query.get(entry_id)
        assert entry.status == "completed"
        assert [s.id for s in prep_timer.sessions_for(entry)] == [
            ann_session, bob_session]
        state = prep_timer.state(entry)
        assert [w["status"] for w in state["workers"]] == [
            "finished", "finished"]
        # Each employee's clock is separate, and the vehicle total is the sum.
        assert state["elapsed"] == sum(w["elapsed"] for w in state["workers"])
        assert Employee.query.get(bob).current_vehicle_id is None


def test_finishing_a_crew_stops_every_clock_on_the_vehicle(client, app):
    """Completing a vehicle some other way stops all of its clocks, so nothing
    is left running in the background."""
    from app.models import PrepSession
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "923")
        ann = add_employee(app, "Ann Alpha")
        bob = add_employee(app, "Bob Beta")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        entry = ScheduleEntry.query.get(entry_id)
        prep_timer.start(entry, ann, at=t0)
        prep_timer.start(entry, bob, at=t0)

    client.post(f"/entry/{entry_id}/complete")
    with app.app_context():
        sessions = PrepSession.query.filter_by(entry_id=entry_id).all()
        assert len(sessions) == 2
        assert all(s.status == "finished" for s in sessions)
        assert all(s.finished_at is not None for s in sessions)


def test_only_the_acting_employees_clock_is_stopped(client, app):
    """A press acts on one clock: the acting employee's own, or the one the
    button belongs to. Somebody with no clock on the vehicle cannot stop
    anybody else's, and a timer from another vehicle is refused."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, vehicle_id = prep_entry(app, "924")
        ann = add_employee(app, "Ann Alpha")
        bob = add_employee(app, "Bob Beta")
        cid = add_employee(app, "Cid Clark")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        entry = ScheduleEntry.query.get(entry_id)
        prep_timer.start(entry, ann, at=t0)
        prep_timer.start(entry, bob, at=t0)

    # Bob presses Pause with no session id: it is Bob's own clock that stops.
    r = client.post(f"/entry/{entry_id}/prep/pause",
                    data={"employee_id": str(bob)})
    assert r.status_code == 200
    paused = [w for w in r.get_json()["state"]["workers"]
              if w["status"] == "paused"]
    assert [w["employee"] for w in paused] == ["Bob Beta"]

    # Cid has no clock on the vehicle, so there is nothing to act on.
    r = client.post(f"/entry/{entry_id}/prep/pause",
                    data={"employee_id": str(cid)})
    assert r.status_code == 409
    assert "no prep timer on vehicle" in r.get_json()["error"]
    with app.app_context():
        entry = ScheduleEntry.query.get(entry_id)
        assert sorted(s.status for s in prep_timer.sessions_for(entry)) == [
            "paused", "running"]

    # A timer that belongs to another vehicle is refused as well.
    other_id, _ = prep_entry(app, "925")
    with app.app_context():
        other = ScheduleEntry.query.get(other_id)
        other_session = prep_timer.start(other, cid, at=t0).id
    r = client.post(f"/entry/{entry_id}/prep/pause",
                    data={"employee_id": str(cid),
                          "session_id": str(other_session)})
    assert r.status_code == 409
    assert "not on vehicle" in r.get_json()["error"]


def test_board_shows_every_employee_on_a_vehicle(client, app):
    """The board lists one clock per employee and offers the + Add Me button so
    anybody can start their own clock on a vehicle already being worked."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "926")
        ann = add_employee(app, "Ann Alpha")
        bob = add_employee(app, "Bob Beta")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        entry = ScheduleEntry.query.get(entry_id)
        ann_session = prep_timer.start(entry, ann, at=t0).id
        prep_timer.start(entry, bob, at=t0)

    html = client.get("/").data.decode()
    block = html[html.index(f'id="prep-{entry_id}"'):
                 html.index('class="progress"', html.index(f'id="prep-{entry_id}"'))]
    assert "Ann Alpha" in block and "Bob Beta" in block
    assert "2 employees" in block
    assert 'data-prep-join="%d"' % entry_id in block
    # A clock per employee, each with its own session and status, plus the
    # vehicle total and the two clock-set totals the board heads each side with.
    assert block.count('data-prep-timer') == 5   # 3 totals + 2 clocks
    assert 'data-session="%d"' % ann_session in block
    assert "Inside prep history (2)" in block   # one start event each
    # Both clocks are inside clocks, so the inside set holds the crew while the
    # outside set is still empty.
    outside = block[block.index(f'id="prep-{entry_id}-outside"'):]
    assert "Ann Alpha" not in outside and "Bob Beta" not in outside
    assert "No outside clock yet" in outside

    # The "Now Working" strip carries a card per employee.
    assert 'data-prep-employee="%d"' % ann in html
    assert 'data-prep-employee="%d"' % bob in html

    # /prep/active keys the vehicles by entry and the clocks by employee.
    payload = client.get("/prep/active").get_json()
    assert payload["sessions"][str(entry_id)]["worker_count"] == 2
    assert {payload["workers"][str(ann)]["employee"],
            payload["workers"][str(bob)]["employee"]} == {"Ann Alpha", "Bob Beta"}


def test_crew_reports_list_every_employee(client, app):
    """The printable report and the end-of-day summary log one line per
    employee and total both clocks."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "927", prep_time="04:30")
        ann = add_employee(app, "Ann Alpha")
        bob = add_employee(app, "Bob Beta")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        entry = ScheduleEntry.query.get(entry_id)
        prep_timer.start(entry, ann, at=t0)
        prep_timer.pause(entry, ann, at=t0 + timedelta(minutes=10),
                         session_id=prep_timer.sessions_for(entry)[0].id)
        prep_timer.resume(entry, ann, at=t0 + timedelta(minutes=20),
                          session_id=prep_timer.sessions_for(entry)[0].id)
        prep_timer.finish(entry, ann, at=t0 + timedelta(minutes=25),
                          session_id=prep_timer.sessions_for(entry)[0].id)
        prep_timer.start(entry, bob, at=t0 + timedelta(minutes=30))
        db.session.commit()

    for html in (client.get(f"/print/{date.today().isoformat()}").data.decode(),
                 client.get("/end").data.decode()):
        assert "Prep Time Log" in html
        assert "Ann Alpha" in html and "Bob Beta" in html
        # Ann: 10 minutes + 5 after the resume. Bob: still running.
        assert "15m 00s" in html
        assert "Paused" in html and "Resumed" in html
    # The vehicle's history keeps one run per employee.
    with app.app_context():
        from app.models import Vehicle as V
        vehicle = V.query.get(entry_id) and ScheduleEntry.query.get(
            entry_id).vehicle
        history = prep_timer.vehicle_history(vehicle)
        assert len(history) == 2
        assert {h["employee"] for h in history} == {"Ann Alpha", "Bob Beta"}


# ---------------------------------------------------------------------------
# Upgrading a database written before a vehicle had two clock sets
# ---------------------------------------------------------------------------

def _downgrade_prep_sessions_to_one_per_vehicle(db_path):
    """Rewrite prep_sessions the way the old model made it: one timer per
    vehicle per day, i.e. UNIQUE on entry_id only and no clock set at all.
    Everything else, including the recorded events, is left exactly as the old
    database had it."""
    import sqlite3
    con = sqlite3.connect(db_path)
    current = con.execute("SELECT sql FROM sqlite_master WHERE type='table'"
                          " AND name='prep_sessions'").fetchone()[0]
    # Drop the clock-set column and the constraint naming it, and fall back to
    # the older one-timer-per-vehicle rule.
    legacy = "\n".join(line for line in current.splitlines()
                       if "scope VARCHAR" not in line)
    legacy = legacy.replace(
        "CONSTRAINT uq_prep_session_employee_scope "
        "UNIQUE (entry_id, employee_id, scope)",
        "UNIQUE (entry_id)")
    assert "UNIQUE (entry_id)" in legacy
    assert "scope" not in legacy
    con.execute("PRAGMA foreign_keys=OFF")
    # Keeps prep_session_events pointing at "prep_sessions" while we swap it.
    con.execute("PRAGMA legacy_alter_table=ON")
    con.execute("ALTER TABLE prep_sessions RENAME TO prep_sessions_legacy")
    con.execute(legacy)
    # The old table had no clock-set column, so name the columns it kept.
    con.execute("INSERT INTO prep_sessions (id, entry_id, vehicle_id,"
                " employee_id, status, started_at, last_event_at,"
                " finished_at, total_seconds, created_at, updated_at)"
                " SELECT id, entry_id, vehicle_id, employee_id, status,"
                " started_at, last_event_at, finished_at, total_seconds,"
                " created_at, updated_at FROM prep_sessions_legacy")
    con.execute("DROP TABLE prep_sessions_legacy")
    con.execute("PRAGMA legacy_alter_table=OFF")
    con.commit()
    con.close()


def test_existing_database_is_upgraded_to_allow_a_crew_per_vehicle(app, tmp_path):
    """A database written before the change keeps every recorded second and
    gains the ability to run a crew, and later two clock sets per vehicle."""
    from app.models import PrepSession, PrepSessionEvent
    from app.services import prep_timer

    db_path = str(tmp_path / "test.db")
    with app.app_context():
        entry_id, _ = prep_entry(app, "930")
        ann = add_employee(app, "Ann Alpha")
        bob = add_employee(app, "Bob Beta")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        session = prep_timer.start(ScheduleEntry.query.get(entry_id), ann,
                                   at=t0)
        prep_timer.finish(ScheduleEntry.query.get(entry_id), ann,
                          at=t0 + timedelta(minutes=20), session_id=session.id)
        old_session_id = PrepSession.query.one().id
    _downgrade_prep_sessions_to_one_per_vehicle(db_path)

    # Booting the app against the old file is the upgrade.
    upgraded = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "SECRET_KEY": "test",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
    })
    with upgraded.app_context():
        sessions = PrepSession.query.all()
        assert [s.id for s in sessions] == [old_session_id]
        assert sessions[0].employee_id == ann
        assert sessions[0].total_seconds == 20 * 60
        assert [e.id for e in sessions[0].events] == \
            [e.id for e in PrepSessionEvent.query.all()]

        # The rebuilt table is the model's own definition, foreign keys
        # included, so a session still points at its entry, vehicle and
        # employee and every event still finds its clock.
        import sqlite3
        con = sqlite3.connect(db_path)
        assert {r[2] for r in con.execute(
            "PRAGMA foreign_key_list(prep_sessions)")} == {
            "schedule_entries", "vehicles", "employees"}
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        con.close()

        entry = ScheduleEntry.query.get(entry_id)
        # A clock recorded before the split has no clock set, so it counts
        # towards the inside *and* the outside total of its day and is still
        # counted once in the vehicle total.
        state = prep_timer.state(entry)
        assert state["scopes"]["inside"]["elapsed"] == 20 * 60
        assert state["scopes"]["outside"]["elapsed"] == 20 * 60
        assert state["elapsed"] == 20 * 60
        assert state["scopes"]["inside"]["workers"][0]["scope"] == "both"

        # The constraint is gone: a second employee can join the same vehicle.
        prep_timer.start(entry, bob, at=t0 + timedelta(minutes=30))
        assert len(PrepSession.query.all()) == 2
        # One employee never runs two clocks at once, so Bob pauses his inside
        # clock before taking up the outside one; the two sets then keep
        # separate clocks, totals and event logs.
        with pytest.raises(prep_timer.PrepTimerError):
            prep_timer.start(entry, bob, at=t0 + timedelta(minutes=30),
                             scope=prep_timer.OUTSIDE)
        bob_inside = [s for s in PrepSession.query.all()
                      if s.employee_id == bob and s.scope == "inside"][0]
        prep_timer.pause(entry, bob, at=t0 + timedelta(minutes=35),
                         session_id=bob_inside.id)
        bob_outside = prep_timer.start(entry, bob, at=t0 + timedelta(minutes=40),
                                       scope=prep_timer.OUTSIDE)
        prep_timer.finish(entry, bob, at=t0 + timedelta(minutes=50),
                          session_id=bob_outside.id)
        assert len(PrepSession.query.all()) == 3

        state = prep_timer.state(entry)
        assert state["worker_count"] == 3
        # The legacy clock counts in both set totals, but only once in the
        # vehicle total: 20m legacy + 5m Bob inside + 10m Bob outside.
        assert state["elapsed"] == 35 * 60
        assert state["scopes"]["inside"]["elapsed"] == 25 * 60
        assert state["scopes"]["outside"]["elapsed"] == 30 * 60
        # Ann's legacy clock already stands for both sides, and Bob's two
        # clocks are independent of each other.
        assert state["scopes"]["inside"]["worker_count"] == 2   # Ann and Bob
        assert state["scopes"]["outside"]["worker_count"] == 2  # Ann and Bob

        # Ann cannot run a second clock in either set either.
        for wanted in (prep_timer.INSIDE, prep_timer.OUTSIDE):
            with pytest.raises(prep_timer.PrepTimerError):
                prep_timer.start(entry, ann, at=t0 + timedelta(minutes=30),
                                 scope=wanted)
        assert len(PrepSession.query.all()) == 3


def _prep_sessions_ddl(db_path):
    import sqlite3
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT sql FROM sqlite_master WHERE type='table'"
                           " AND name='prep_sessions'").fetchone()[0]
    finally:
        con.close()


def test_upgraded_prep_sessions_keeps_its_foreign_keys(app, tmp_path):
    """Rebuilding the table must not quietly drop the references the model
    declares — the crew upgrade is a constraint swap, not a schema downgrade."""
    db_path = str(tmp_path / "test.db")
    _downgrade_prep_sessions_to_one_per_vehicle(db_path)
    create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "SECRET_KEY": "test",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
    })

    import sqlite3
    con = sqlite3.connect(db_path)
    try:
        targets = {r[2] for r in
                   con.execute("PRAGMA foreign_key_list(prep_sessions)")}
        assert targets == {"schedule_entries", "vehicles", "employees"}
    finally:
        con.close()


def test_booting_again_leaves_an_already_upgraded_database_alone(app, tmp_path):
    """The upgrade runs once. A database that already allows a crew must be
    left untouched on every later start, not re-copied."""
    from app.models import PrepSession
    from app.services import prep_timer
    import sqlite3

    db_path = str(tmp_path / "test.db")
    with app.app_context():
        entry_id, _ = prep_entry(app, "932")
        ann = add_employee(app, "Ann Alpha")
        prep_timer.start(ScheduleEntry.query.get(entry_id), ann,
                         at=timeutils.now_eastern().replace(microsecond=0))
    _downgrade_prep_sessions_to_one_per_vehicle(db_path)

    config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "SECRET_KEY": "test",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
    }
    create_app(config)  # first boot: the upgrade runs

    # A column the upgrade never knew about: a rebuild would drop it, because
    # it copies a fixed list of columns.
    con = sqlite3.connect(db_path)
    con.execute("ALTER TABLE prep_sessions ADD COLUMN note TEXT")
    con.execute("UPDATE prep_sessions SET note='keep me'")
    con.commit()
    con.close()

    create_app(config)  # second boot: nothing left to do

    con = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(prep_sessions)")}
        assert "note" in cols
        assert con.execute("SELECT note FROM prep_sessions").fetchall() == \
            [("keep me",)]
        ddl = _prep_sessions_ddl(db_path)
        assert "UNIQUE (entry_id, employee_id)" in ddl
        assert ddl.count("FOREIGN KEY") == 3
    finally:
        con.close()
    with app.app_context():
        # Still one session per (entry, employee), still readable by the model.
        assert [s.employee_id for s in PrepSession.query.all()] == [ann]


def test_join_button_is_hidden_for_the_employee_already_timing(client, app):
    """A person who already has a clock in one clock set is offered their own
    buttons there, not a button that would be refused, and is still offered a
    clock of their own in the other set."""
    from app.services import prep_timer

    with app.app_context():
        entry_id, _ = prep_entry(app, "931")
        ann = add_employee(app, "Ann Alpha")
        bob = add_employee(app, "Bob Beta")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        entry = ScheduleEntry.query.get(entry_id)
        prep_timer.start(entry, ann, at=t0)
        db.session.commit()

    def set_html(entry_id, scope):
        html = c.get("/").data.decode()
        start = html.index(f'id="prep-{entry_id}-{scope}"')
        return html[start:start + 4000]

    # Signed in as Ann: her own row of buttons, no "+ Add Me", in the set she
    # is timing — while the untouched set still offers her a clock of her own.
    c = app.test_client()
    c.post("/login", data={"username": "employee", "password": "employee"})
    c.post("/select", data={"employee_id": str(ann)})
    assert "Ann Alpha" in set_html(entry_id, "inside")
    assert 'data-prep-join' not in set_html(entry_id, "inside")
    assert "Your inside clock for this vehicle is in the list above" \
        in set_html(entry_id, "inside")
    # The other set knows nothing of her inside clock: it is still waiting for
    # its own first Start, which is hers to press.
    assert "Start Outside" in set_html(entry_id, "outside")
    assert "Ann Alpha" not in set_html(entry_id, "outside")

    # Signed in as Bob, the same vehicle offers him a clock of his own.
    c = app.test_client()
    c.post("/login", data={"username": "employee", "password": "employee"})
    c.post("/select", data={"employee_id": str(bob)})
    assert 'data-prep-join="%d"' % entry_id in set_html(entry_id, "inside")


def test_now_working_card_follows_the_employee_not_the_vehicle(client, app):
    """Each "Now Working" card shows that person's own clock, and a card
    disappears as soon as they press Done even if colleagues carry on."""
    with app.app_context():
        first_id, _ = prep_entry(app, "932")
        second_id, _ = prep_entry(app, "933")
        ann = add_employee(app, "Ann Alpha")
        bob = add_employee(app, "Bob Beta")
        t0 = timeutils.now_eastern().replace(microsecond=0)
        first = ScheduleEntry.query.get(first_id)
        second = ScheduleEntry.query.get(second_id)
        from app.services import prep_timer
        prep_timer.start(first, ann, at=t0)
        prep_timer.start(second, bob, at=t0 + timedelta(minutes=2))
        ann_session = prep_timer.sessions_for(first)[0].id

    payload = client.get("/prep/active").get_json()
    assert payload["workers"][str(ann)]["session_id"] == ann_session
    assert payload["workers"][str(ann)]["entry_id"] == first_id
    assert payload["workers"][str(bob)]["entry_id"] == second_id
    html = client.get("/").data.decode()
    assert 'data-prep-employee="%d"' % ann in html

    # Ann presses Done: her card goes, Bob's keeps ticking.
    client.post(f"/entry/{first_id}/prep/done", data={"employee_id": str(ann)})
    payload = client.get("/prep/active").get_json()
    assert str(ann) not in payload["workers"]
    assert payload["workers"][str(bob)]["status"] == "running"
    html = client.get("/").data.decode()
    assert 'data-prep-employee="%d"' % ann not in html
    assert 'data-prep-employee="%d"' % bob in html
