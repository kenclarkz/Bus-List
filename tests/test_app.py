"""Tests for the Detailing Operations Dashboard."""
import io
import json
from datetime import date, datetime

import pytest

from app import create_app
from app.models import db, Vehicle, ScheduleEntry, TaskCompletion, Replacement, \
    DailySchedule
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
    return app.test_client()


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


def test_employees_and_history_pages(client):
    assert client.get("/employees").status_code == 200
    assert client.get("/history").status_code == 200
    assert client.get("/settings").status_code == 200
