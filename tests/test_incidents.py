"""Tests for the incident report feature."""
import io
import os

import pytest

from app import create_app
from app.models import db, Vehicle, Employee, IncidentReport, IncidentNote, \
    IncidentPhoto
from app.services.incidents import ISSUE_TYPES, SEVERITIES, STATUSES
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


def test_driver_cannot_manage_incidents(app):
    """Drivers may submit incidents but cannot manage (review/edit) them."""
    d = app.test_client()
    d.post("/login", data={"username": "driver", "password": "driver"})
    assert d.get("/incidents").status_code == 200
    assert d.get("/incidents/new").status_code == 200
    d.post("/incidents/new", data={
        "unit_number": "8493",
        "issue_type": "Exterior",
        "description": "Minor scratch",
    })
    with app.app_context():
        inc = IncidentReport.query.first()
        assert inc is not None  # a vehicle-less crew member can still report
    iid = inc.id
    # Editing / resolving stays manager-only.
    assert d.get(f"/incidents/{iid}").status_code == 200
    assert d.post(f"/incidents/{iid}/edit", data={}).status_code == 302
    assert d.post(f"/incidents/{iid}/resolve", data={}).status_code == 302


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
# ECHO Accident/Incident Report (filled-in PDF download)
# ---------------------------------------------------------------------------

def test_submit_saves_echo_report_fields(client, app):
    vid = _make_vehicle(app)
    r = client.post("/incidents/new", data={
        "vehicle_id": str(vid),
        "issue_type": "Damage",
        "severity": "High",
        "description": "Rear-ended at low speed",
        "driver_name": "John Smith",
        "police_notified": "yes",
        "police_report_number": "E-44123",
        "road_name": "FL A1A",
        "intersection_with": "3rd Street",
        "county_parish": "Duval",
        "city_town": "Jacksonville Beach",
        "roadway_conditions": "Wet",
        "accident_type": "Collision",
        "injuries_other_party": "no",
        "employee_injured": "no",
        "employee_citation": "no",
        "investigating_supervisor": "R. Daudt",
        "employee_supervisor": "B. Bunten",
        "other_driver_is_owner": "yes",
        "other_driver_name": "Jane Doe",
        "other_driver_address": "123 Main St",
        "other_driver_city": "Jacksonville",
        "other_driver_state": "FL",
        "other_driver_zip": "32224",
        "other_driver_phone": "904-555-1234",
        "other_driver_license": "D12345678",
        "other_driver_license_state": "FL",
        "other_make": "Toyota",
        "other_model": "Camry",
        "other_year": "2018",
        "other_color": "Silver",
        "other_plate": "XYZ 123",
        "other_passengers": "2",
        "other_injuries": "no",
        "other_driver_ticketed": "no",
        "insurance_company": "GEICO",
        "insurance_policy": "P-998877",
        "property_damage": "yes",
        "owner_object_struck": "Guardrail",
        "hazmat_spill": "no",
        "witnesses": "Two bystanders helped.",
    })
    assert r.status_code == 302
    with app.app_context():
        inc = IncidentReport.query.first()
        assert inc.driver_name == "John Smith"
        assert inc.police_notified is True
        assert inc.police_report_number == "E-44123"
        assert inc.road_name == "FL A1A"
        assert inc.intersection_with == "3rd Street"
        assert inc.accident_type == "Collision"
        assert inc.injuries_other_party is False
        assert inc.other_driver_is_owner is True
        assert inc.insurance_company == "GEICO"
        assert inc.property_damage is True
        assert inc.hazmat_spill is False
        assert inc.witnesses == "Two bystanders helped."


def test_download_filled_incident_pdf(client, app):
    vid = _make_vehicle(app, unit="9101")
    with app.app_context():
        v = Vehicle.query.get(vid)
        v.vehicle_type = None
        db.session.add(IncidentReport(
            vehicle_id=vid, issue_type="Damage", severity="High",
            description="Front bumper cracked.",
            driver_name="John Smith", accident_type="Collision",
            police_notified=True, police_report_number="E-44123",
            road_name="FL A1A", city_town="Jacksonville Beach"))
        db.session.commit()
        iid = IncidentReport.query.first().id

    r = client.get(f"/incidents/{iid}/pdf")
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert r.data.startswith(b"%PDF")
    # The generated copy carries the recorded values onto the template.
    text = ""
    import pymupdf
    doc = pymupdf.open("pdf", r.data)
    for page in doc:
        text += page.get_text()
    assert "John Smith" in text
    assert "FL A1A" in text
    assert "E-44123" in text
    assert "Front bumper cracked." in text
    assert "Jacksonville Beach" in text


def test_driver_can_submit_incident(app):
    d = app.test_client()
    d.post("/login", data={"username": "driver", "password": "driver"})
    assert d.get("/incidents/new").status_code == 200
    r = d.post("/incidents/new", data={
        "unit_number": "8493",
        "issue_type": "Damage",
        "severity": "Medium",
        "description": "Tail light broken",
        "driver_name": "Danny Driver",
    })
    assert r.status_code == 302
    with app.app_context():
        inc = IncidentReport.query.first()
        assert inc is not None
        assert inc.vehicle.unit_number == "8493"
        assert inc.driver_name == "Danny Driver"


def test_manager_edit_saves_echo_report_fields(manager_client, app):
    iid = _make_incident(app)
    r = manager_client.post(f"/incidents/{iid}/edit", data={
        "issue_type": "Mechanical",
        "severity": "Medium",
        "status": "Open",
        "description": "Updated description",
        "driver_name": "Ed Supervisor",
        "police_notified": "no",
        "road_name": "Beach Blvd",
        "accident_type": "Incident",
        "other_driver_name": "Pat Passenger",
        "insurance_company": "Progressive",
        "witnesses": "Front desk clerk",
    })
    assert r.status_code == 302
    with app.app_context():
        inc = IncidentReport.query.get(iid)
        assert inc.driver_name == "Ed Supervisor"
        assert inc.police_notified is False
        assert inc.road_name == "Beach Blvd"
        assert inc.accident_type == "Incident"
        assert inc.other_driver_name == "Pat Passenger"
        assert inc.insurance_company == "Progressive"
        assert inc.witnesses == "Front desk clerk"