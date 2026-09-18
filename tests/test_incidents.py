"""Tests for the incident report feature."""
import io
import os

import pytest

from app import create_app
from app.models import db, Vehicle, Employee, IncidentReport, IncidentNote, \
    IncidentPhoto
from app.services.incidents import ISSUE_TYPES, SEVERITIES, STATUSES, \
    echo_field_groups
from app.services.vehicles import find_or_create_vehicle, default_location


@pytest.fixture()
def app(tmp_path):
    db_path = tmp_path / "incidents.db"
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


def _make_vehicle(app, unit="9999"):
    with app.app_context():
        v, _ = find_or_create_vehicle(unit, location_id=default_location().id)
        return v.id


def _make_incident(app, unit="9999", issue_type="Mechanical",
                   severity="Medium", description="Test issue"):
    with app.app_context():
        v, is_new = find_or_create_vehicle(unit,
                                           location_id=default_location().id)
        inc = IncidentReport(vehicle_id=v.id, issue_type=issue_type,
                             severity=severity, description=description)
        db.session.add(inc)
        db.session.commit()
        return inc.id


# ---------------------------------------------------------------------------
# Submitting incidents
# ---------------------------------------------------------------------------

def test_submit_incident(client, app):
    vid = _make_vehicle(app)
    r = client.post("/incidents/new", data={
        "vehicle_id": str(vid),
        "issue_type": "Mechanical",
        "severity": "High",
        "description": "Engine overheating",
        "location": "Engine bay",
    })
    assert r.status_code == 302
    with app.app_context():
        inc = IncidentReport.query.first()
        assert inc is not None
        assert inc.vehicle_id == vid
        assert inc.issue_type == "Mechanical"
        assert inc.severity == "High"
        assert inc.description == "Engine overheating"
        assert inc.location == "Engine bay"
        assert inc.status == "Open"
        # Employees always report as themselves.
        emp = Employee.query.filter_by(active=True).first()
        assert inc.reported_by == emp.id
        iid = inc.id
    body = client.get(f"/incidents/{iid}").data.decode()
    assert "Engine overheating" in body
    assert f"Incident #{iid}" in body


def test_submit_requires_description_and_valid_type(client, app):
    vid = _make_vehicle(app)
    r = client.post("/incidents/new", data={
        "vehicle_id": str(vid),
        "issue_type": "Mechanical",
        "description": "",
    })
    assert r.status_code == 302
    r = client.post("/incidents/new", data={
        "vehicle_id": str(vid),
        "issue_type": "NotARealType",
        "description": "x",
    })
    assert r.status_code == 302
    with app.app_context():
        assert IncidentReport.query.count() == 0


def test_submit_requires_vehicle(client, app):
    r = client.post("/incidents/new", data={
        "vehicle_id": "",
        "unit_number": "",
        "issue_type": "Damage",
        "description": "x",
    })
    assert r.status_code == 302
    with app.app_context():
        assert IncidentReport.query.count() == 0


def test_submit_with_new_unit_number(client, app):
    """An issue can be raised against a vehicle that doesn't exist yet."""
    r = client.post("/incidents/new", data={
        "unit_number": "7711",
        "issue_type": "Safety",
        "severity": "Critical",
        "description": "Broken seatbelt",
    })
    assert r.status_code == 302
    with app.app_context():
        inc = IncidentReport.query.first()
        assert inc is not None
        assert inc.vehicle.unit_number == "7711"
        assert inc.issue_type == "Safety"


def test_manager_can_submit_incident(manager_client, app):
    vid = _make_vehicle(app)
    with app.app_context():
        emp = Employee(name="Sara Manager")
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id
    r = manager_client.post("/incidents/new", data={
        "vehicle_id": str(vid),
        "issue_type": "Interior",
        "severity": "Low",
        "description": "Torn seat",
        "reported_by": str(emp_id),
    })
    assert r.status_code == 302
    with app.app_context():
        inc = IncidentReport.query.first()
        assert inc.issue_type == "Interior"
        assert inc.reported_by == emp_id


# ---------------------------------------------------------------------------
# Listing / filtering
# ---------------------------------------------------------------------------

def test_incidents_list_and_filters(client, app):
    _make_incident(app, unit="9101", issue_type="Mechanical", severity="High")
    _make_incident(app, unit="9202", issue_type="Interior", severity="Low")

    html = client.get("/incidents").data.decode()
    assert "9101" in html
    assert "9202" in html

    r = client.get("/incidents?type=Mechanical")
    assert b"9101" in r.data
    assert b"9202" not in r.data

    r = client.get("/incidents?unit=9202")
    assert b"9202" in r.data
    assert b"9101" not in r.data

    r = client.get("/incidents?severity=Low")
    assert b"9202" in r.data
    assert b"9101" not in r.data

    r = client.get("/incidents?status=Open")
    assert b"9101" in r.data


def test_incidents_filter_by_employee(client, app):
    with app.app_context():
        emp1 = Employee(name="Alice Worker")
        emp2 = Employee(name="Bob Worker")
        db.session.add_all([emp1, emp2])
        db.session.commit()
        v1, _ = find_or_create_vehicle("9101", location_id=default_location().id)
        v2, _ = find_or_create_vehicle("9202", location_id=default_location().id)
        db.session.add(IncidentReport(vehicle_id=v1.id, issue_type="Damage",
                                      description="a", reported_by=emp1.id))
        db.session.add(IncidentReport(vehicle_id=v2.id, issue_type="Damage",
                                      description="b", reported_by=emp2.id))
        db.session.commit()
        emp1_id = emp1.id
        emp2_id = emp2.id

    # Alice's filter shows only the vehicle she reported.
    body = client.get(f"/incidents?employee={emp1_id}").data.decode()
    assert "<strong>9101</strong>" in body
    assert "<strong>9202</strong>" not in body

    # Bob's filter shows only his vehicle's row in the table.
    body = client.get(f"/incidents?employee={emp2_id}").data.decode()
    assert "<strong>9202</strong>" in body
    assert "<strong>9101</strong>" not in body


def test_manager_can_view_incidents(manager_client, app):
    assert manager_client.get("/incidents").status_code == 200
    assert manager_client.get("/incidents/new").status_code == 200


def test_driver_cannot_access_incidents(app):
    d = app.test_client()
    d.post("/login", data={"username": "driver", "password": "driver"})
    assert d.get("/incidents").status_code == 302


# ---------------------------------------------------------------------------
# Manager review / edit / assign / resolve
# ---------------------------------------------------------------------------

def test_manager_edits_assigns_and_resolves(manager_client, app, client):
    iid = _make_incident(app)
    with app.app_context():
        emp = Employee(name="Fix It Fred")
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id

    r = manager_client.post(f"/incidents/{iid}/edit", data={
        "issue_type": "Damage",
        "severity": "Critical",
        "status": "In Progress",
        "assigned_to": str(emp_id),
        "location": "Bumper",
        "description": "Dented front bumper",
        "occurred_at": "2026-09-15T09:00",
    })
    assert r.status_code == 302

    r = manager_client.post(f"/incidents/{iid}/note", data={"text": "Tech informed"})
    assert r.status_code == 302

    r = manager_client.post(f"/incidents/{iid}/resolve", data={
        "resolution_notes": "Bumper replaced"})
    assert r.status_code == 302

    with app.app_context():
        inc = IncidentReport.query.get(iid)
        assert inc.issue_type == "Damage"
        assert inc.severity == "Critical"
        assert inc.status == "Resolved"
        assert inc.assigned_to == emp_id
        assert inc.resolution_notes == "Bumper replaced"
        assert inc.resolved_at is not None
        notes = IncidentNote.query.filter_by(incident_id=iid).all()
        assert len(notes) == 1
        assert notes[0].text == "Tech informed"

    body = manager_client.get(f"/incidents/{iid}").data.decode()
    assert "Bumper replaced" in body
    assert "Dented front bumper" in body
    assert "Tech informed" in body


def test_employee_cannot_manage_incidents(client, app):
    iid = _make_incident(app)
    r = client.post(f"/incidents/{iid}/edit", data={"status": "Resolved"})
    assert r.status_code == 302
    r = client.post(f"/incidents/{iid}/note", data={"text": "nope"})
    assert r.status_code == 302
    r = client.post(f"/incidents/{iid}/resolve", data={"resolution_notes": "x"})
    assert r.status_code == 302
    with app.app_context():
        inc = IncidentReport.query.get(iid)
        assert inc.status == "Open"
        assert IncidentNote.query.filter_by(incident_id=iid).count() == 0


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------

def test_incident_photos_uploaded_and_served(manager_client, app):
    iid = _make_incident(app)
    r = manager_client.post(f"/incidents/{iid}/photos", data={
        "photos": (io.BytesIO(b"fakeimagebytes"), "bumper.jpg"),
    }, content_type="multipart/form-data")
    assert r.status_code == 302

    with app.app_context():
        photo = IncidentPhoto.query.first()
        assert photo is not None
        assert photo.incident_id == iid
        assert photo.file_path and os.path.isfile(photo.file_path)
        served = photo.file_path
        pid = photo.id

    # Photo viewing is allowed for staff (not just managers).
    c = app.test_client()
    c.post("/login", data={"username": "employee", "password": "employee"})
    with app.app_context():
        emp = Employee.query.filter_by(active=True).first()
    c.post("/select", data={"employee_id": str(emp.id)})
    r = c.get(f"/incidents/photo/{pid}")
    assert r.status_code == 200
    assert r.data == b"fakeimagebytes"

    # Employees may not remove photos.
    c.post(f"/incidents/photo/{pid}/delete")
    with app.app_context():
        assert IncidentPhoto.query.get(pid) is not None
        assert os.path.isfile(served)


def test_manager_can_remove_photo(manager_client, app):
    iid = _make_incident(app)
    manager_client.post(f"/incidents/{iid}/photos", data={
        "photos": (io.BytesIO(b"data"), "photo.png"),
    }, content_type="multipart/form-data")
    with app.app_context():
        photo = IncidentPhoto.query.first()
        pid = photo.id
        path = photo.file_path
    r = manager_client.post(f"/incidents/photo/{pid}/delete")
    assert r.status_code == 302
    with app.app_context():
        assert IncidentPhoto.query.get(pid) is None
    assert not os.path.isfile(path)


def test_incident_rejects_non_image_photos(client, app):
    iid = _make_incident(app)
    r = client.post(f"/incidents/{iid}/photos", data={
        "photos": (io.BytesIO(b"<script>"), "evil.txt"),
    }, content_type="multipart/form-data")
    assert r.status_code == 302
    with app.app_context():
        assert IncidentPhoto.query.count() == 0


def test_photo_uploaded_on_submission(client, app):
    vid = _make_vehicle(app)
    r = client.post("/incidents/new", data={
        "vehicle_id": str(vid),
        "issue_type": "Exterior",
        "description": "Scratched paint",
        "photos": (io.BytesIO(b"imgdata"), "scratch.jpg"),
    }, content_type="multipart/form-data")
    assert r.status_code == 302
    with app.app_context():
        photo = IncidentPhoto.query.first()
        assert photo is not None
        assert os.path.isfile(photo.file_path)
        inc = IncidentReport.query.first()
        assert inc.photos, "incident should have its photo"


# ---------------------------------------------------------------------------
# Vehicle history link
# ---------------------------------------------------------------------------

def test_vehicle_detail_lists_incidents(manager_client, app):
    vid = _make_vehicle(app, unit="5307")
    with app.app_context():
        db.session.add(IncidentReport(vehicle_id=vid, issue_type="Damage",
                                      severity="High",
                                      description="Windshield crack"))
        db.session.commit()

    html = manager_client.get(f"/vehicles/{vid}").data.decode()
    assert "Incident Reports" in html
    assert "Damage" in html
    assert 'href="/incidents/new?vehicle=' in html


# ---------------------------------------------------------------------------
# ECHO report PDF generation & download
# ---------------------------------------------------------------------------

def test_submit_with_echo_fields_creates_pdf(client, app):
    vid = _make_vehicle(app, unit="9101")
    r = client.post("/incidents/new", data={
        "vehicle_id": str(vid),
        "issue_type": "Mechanical",
        "description": "Engine overheating on I-95",
        "echo_fields[driver_name]": "John Smith",
        "echo_fields[police_notified]": "Yes",
        "echo_fields[police_report_number]": "2026-0112",
        "echo_fields[accident_type]": "Collision",
        "echo_fields[roadway_conditions]": "Dry",
        "echo_fields[witnesses]": "Two witnesses nearby.",
        "echo_fields[hazardous_spill]": "No",
    })
    assert r.status_code == 302
    with app.app_context():
        inc = IncidentReport.query.first()
        assert inc.echo_fields is not None
        import json
        echo = json.loads(inc.echo_fields)
        assert echo["driver_name"] == "John Smith"
        assert echo["police_notified"] == "Yes"
        assert echo["accident_type"] == "Collision"
        # Yes list fields should not leak into unknown keys.
        assert "unit_number" not in echo
        # A filled PDF copy is saved for managers to download.
        assert inc.pdf_path and os.path.isfile(inc.pdf_path)
        iid = inc.id
    body = client.get(f"/incidents/{iid}").data.decode()
    assert "Download Filled PDF" in body


def test_incident_pdf_download(client, app):
    iid = _make_incident(app, unit="9203")
    r = client.get(f"/incidents/{iid}/pdf")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"
    assert r.headers["Content-Type"] == "application/pdf"
    assert "ECHO_Incident_Report_9203" in r.headers.get(
        "Content-Disposition", "")


def test_incident_pdf_download_available_to_manager(manager_client, app):
    iid = _make_incident(app, unit="8414")
    r = manager_client.get(f"/incidents/{iid}/pdf")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"


def test_manager_edit_updates_echo_fields_and_pdf(manager_client, app):
    iid = _make_incident(app, unit="8406")
    r = manager_client.post(f"/incidents/{iid}/edit", data={
        "issue_type": "Damage",
        "severity": "Critical",
        "status": "In Progress",
        "description": "Dented front bumper",
        "echo_fields[driver_name]": "Jane Manager",
        "echo_fields[city]": "Jacksonville",
    })
    assert r.status_code == 302
    with app.app_context():
        import json
        inc = IncidentReport.query.get(iid)
        echo = json.loads(inc.echo_fields)
        assert echo["driver_name"] == "Jane Manager"
        assert echo["city"] == "Jacksonville"
        assert inc.pdf_path and os.path.isfile(inc.pdf_path)
        pdf_path = inc.pdf_path
        inc.pdf_path = None
        db.session.commit()
    # Download regenerates when the saved copy went missing.
    r = manager_client.get(f"/incidents/{iid}/pdf")
    assert r.status_code == 200
    with app.app_context():
        inc = IncidentReport.query.get(iid)
        assert inc.pdf_path and os.path.isfile(inc.pdf_path)


def test_echo_field_groups_covers_required_sections(app):
    with app.app_context():
        groups = echo_field_groups()
        labels = [g[0] for g in groups]
        assert "Driver" in labels
        assert "Accident / Incident" in labels
        assert "Other Vehicle — Driver" in labels
        names = {name for _, rows in groups for name, *_ in rows}
        # Core template fields are present so the PDF can be filled.
        for key in ["driver_name", "police_notified", "police_report_number",
                    "road_name", "accident_type", "injuries_other_party",
                    "insurance_co", "witnesses", "hazardous_spill"]:
            assert key in names


def test_driver_cannot_download_pdf(app):
    iid = _make_incident(app, unit="9416")
    d = app.test_client()
    d.post("/login", data={"username": "driver", "password": "driver"})
    assert d.get(f"/incidents/{iid}/pdf").status_code == 302