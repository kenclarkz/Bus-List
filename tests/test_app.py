"""Tests for the Detailing Operations Dashboard."""
import io
import json
from datetime import date, datetime

import pytest

from app import create_app
from app.models import db, Vehicle, ScheduleEntry, TaskCompletion, Replacement, \
    DailySchedule
from app.services.vehicles import find_or_create_vehicle
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
    })
    with app.app_context():
        yield app


@pytest.fixture()
def client(app):
    # Default: signed in as the Employee account so existing route tests run
    # against the interactive board.
    c = app.test_client()
    c.post("/login", data={"username": "employee", "password": "employee"})
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
    headers = ["Prep Time", "Vehicle", "Vehicle Type", "Type", "Trips #"]
    col_widths = [60, 90, 80, 90, 45]
    row_h = 30
    x0, y0 = 40, 60

    data_rows = [
        ["01:45", "9205-\nJAXSUV", "SUVSUB", "Departure", "4"],
        ["02:00", "9203-\nJAXSUV", "SUVSUB", "As Directed", "1"],
        ["04:00", "4301-\nJAXUNF", "TRANSITB", "Shuttle", "2"],
        ["04:15", "7101-\nJAXMINIC", "MINIC34", "Transfer", "2"],
        ["09:45", "9331-\nJAXVAN", "Van", "Hourly", "1"],
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
        assert v_last_washed(app, "300") is not None


def vehicles_loc(app):
    from app.services import vehicles
    return vehicles.default_location()


def v_last_washed(app, unit):
    v = Vehicle.query.filter_by(unit_number=unit).first()
    return v.last_washed


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


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_settings_lifecycle(client, app):
    r = client.post("/settings", data={
        "recent_days": "3",
        "due_soon_days": "10",
        "location": "Main Depot",
        "checklist": "Sweep,Mop,Windows",
    })
    assert r.status_code == 302
    with app.app_context():
        from app.services import settings as s
        assert s.get_setting("recent_days") == "3"
        assert s.get_checklist() == ["Sweep", "Mop", "Windows"]


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

def test_vehicle_crud(client, app):
    r = client.post("/vehicles/new", data={
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
    r = client.get(f"/vehicles/{vid}")
    assert r.status_code == 200
    assert b"999" in r.data
    r = client.get("/vehicles")
    assert b"999" in r.data


def test_vehicle_list_shows_inactive_vehicles(client, app):
    with app.app_context():
        v, _ = find_or_create_vehicle("888", vehicle_type="Van", route="R8")
        v.active = False
        db.session.commit()
    r = client.get("/vehicles")
    assert r.status_code == 200
    assert b"888" in r.data
    assert b"Inactive" in r.data


def test_employees_and_history_pages(client):
    assert client.get("/employees").status_code == 200
    assert client.get("/history").status_code == 200
    assert client.get("/settings").status_code == 200


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
        custom.checklist = "Sweep,Windows,Bay Checked"
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


def test_skip_vehicle_counts_as_complete(client, app):
    """Skipping a vehicle counts it toward completion and it can be un-skipped."""
    with app.app_context():
        from app.services import schedule as ss
        from app.services.vehicles import find_or_create_vehicle
        v, _ = find_or_create_vehicle("740", location_id=vehicles_loc(app).id)
        sched = ss.get_or_create_schedule(location=vehicles_loc(app))
        entry = ss.ensure_entry(sched, v)
        ea = entry.id
        assert entry.status == "pending"

    # Skip it
    r = client.post(f"/entry/{ea}/skip")
    assert r.status_code == 302
    with app.app_context():
        e = ScheduleEntry.query.get(ea)
        assert e.status == "skipped"
        done, total, pct = sched_svc.entry_progress(e)
        assert done == total
        assert pct == 100

    # Dashboard counts it toward completed and shows skipped stat
    import re
    html = client.get("/").data.decode()
    assert "Skipped" in html
    assert '"skipped"' in html

    # Un-skip restores to pending (no tasks done)
    r = client.post(f"/entry/{ea}/unskip")
    assert r.status_code == 302
    with app.app_context():
        e = ScheduleEntry.query.get(ea)
        assert e.status == "pending"


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


def test_dump_status_shown_on_dashboard_and_detail(client, app):
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
    detail = client.get(f"/vehicles/{vid}").data.decode()
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
    assert r.headers["Location"].endswith("/")
    assert e.get("/").status_code == 200

    m = app.test_client()
    r = m.post("/login", data={"username": "manager", "password": "manager"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/")

    d = app.test_client()
    r = d.post("/login", data={"username": "driver", "password": "driver"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/driver")


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
    assert d.get("/settings").status_code == 302


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
