"""Incident report helpers: validation, photo storage, note helpers."""
import json
import os
import uuid

from werkzeug.utils import secure_filename

from app.models import db, IncidentNote, IncidentPhoto, IncidentReport, \
    Employee, Vehicle

ISSUE_TYPES = ["Mechanical", "Interior", "Exterior", "Damage", "Safety", "Other"]
SEVERITIES = ["Low", "Medium", "High", "Critical"]
STATUSES = ["Open", "In Progress", "Resolved"]

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"}

SEVERITY_CLASSES = {
    "Low": "success",
    "Medium": "warn",
    "High": "danger",
    "Critical": "danger",
}
STATUS_CLASSES = {
    "Open": "warn",
    "In Progress": "info",
    "Resolved": "success",
}

# Fields collected on the ECHO East Coast ACCIDENT / INCIDENT REPORT form.
# Groups are rendered in the same order the printed template presents them.
# entry: (field_name, label, input_type)
# input_type: "text" | "textarea" | "yesno" | "select:opt1|opt2|..."
# "yesno" answers are stored as "Yes"/"No" and circle the matching word on the
# PDF template.
ECHO_FIELDS = [
    ("Driver", [
        ("driver_name", "Driver Full Name (Printed)", "text"),
    ]),
    ("Accident / Incident", [
        ("road_name", "Name of road on which it occurred", "text"),
        ("intersection", "At intersection with", "text"),
        ("county", "County / Parish", "text"),
        ("city", "City / Town", "text"),
        ("roadway_conditions", "Roadway & weather conditions", "text"),
        ("police_notified", "Police notified?", "yesno"),
        ("police_report_number", "Police report number (if yes)", "text"),
        ("accident_type", "Type of accident", "select:Collision|Passengers involved|Incident (no other vehicle or passengers)"),
        ("injuries_other_party", "Injuries (other party's)?", "yesno"),
        ("employee_injured", "Employee injured?", "yesno"),
        ("citation_received", "Did employee receive a citation?", "yesno"),
        ("violation_reason", "Violation reason", "text"),
        ("investigating_supervisor", "Investigating supervisor", "text"),
        ("employee_supervisor", "Employee supervisor", "text"),
    ]),
    ("Other Vehicle — Driver", [
        ("other_vehicle_driver_owned", "Was other driver the owner?", "yesno"),
        ("other_driver_name", "Driver name", "text"),
        ("other_driver_address", "Driver address", "text"),
        ("other_driver_city", "City", "text"),
        ("other_driver_state", "State", "text"),
        ("other_driver_zip", "Zip", "text"),
        ("other_driver_phone", "Phone #", "text"),
        ("other_driver_license", "Driver license #", "text"),
        ("other_driver_license_state", "License state", "text"),
    ]),
    ("Owner (if different from driver)", [
        ("owner_name", "Owner name", "text"),
        ("owner_address", "Address", "text"),
        ("owner_city", "City", "text"),
        ("owner_state", "State", "text"),
        ("owner_zip", "Zip", "text"),
    ]),
    ("Other Vehicle — Vehicle Info", [
        ("vehicle_make", "Make", "text"),
        ("vehicle_model", "Model", "text"),
        ("vehicle_year", "Year", "text"),
        ("vehicle_color", "Color", "text"),
        ("license_plate", "License plate", "text"),
        ("number_of_passengers", "Number of passengers", "text"),
        ("other_vehicle_injuries", "Injuries?", "yesno"),
        ("other_driver_ticketed", "Was other driver ticketed?", "yesno"),
    ]),
    ("Insurance", [
        ("insurance_co", "Insurance company", "text"),
        ("insurance_policy", "Policy #", "text"),
        ("insurance_address", "Address", "text"),
        ("insurance_city", "City", "text"),
        ("insurance_state", "State", "text"),
        ("insurance_zip", "Zip", "text"),
        ("insurance_phone", "Phone #", "text"),
        ("witnesses", "Witnesses / injured parties", "textarea"),
    ]),
    ("Property & Hazardous Materials", [
        ("property_damage", "Damage to property other than vehicles?", "yesno"),
        ("property_owner", "Name & address of owner of object struck", "text"),
        ("hazardous_spill", "Hazardous material spilled (other than fuel)?", "yesno"),
    ]),
]

ECHO_SELECT_TYPES = {
    "accident_type": ["Collision", "Passengers involved",
                      "Incident (no other vehicle or passengers)"],
}


def echo_field_groups(incident=None):
    """Return ECHO template field groups with current values populated.

    Each group is (group_label, [(name, label, input_type, value)]). Works for
    both the create form (incident is None) and the edit form (incident set).
    """
    data = load_echo_fields(incident) if incident is not None else {}
    groups = []
    for label, fields in ECHO_FIELDS:
        rows = []
        for name, flabel, ftype in fields:
            if ftype.startswith("select:"):
                choice, rest = ftype.split(":", 1)
                options = rest.split("|")
                rows.append((name, flabel, choice, options, data.get(name, "")))
            else:
                rows.append((name, flabel, ftype, None, data.get(name, "")))
        groups.append((label, rows))
    return groups


def load_echo_fields(incident):
    """Parse an incident's echo_fields JSON column into a plain dict."""
    if not incident or not getattr(incident, "echo_fields", None):
        return {}
    try:
        return json.loads(incident.echo_fields)
    except (ValueError, TypeError):
        return {}


def collect_echo_fields(form):
    """Pull the ECHO field values from a submitted form into a dict.

    Accepts a Werkzeug MultiDict (request.form) with the fields namespaced as
    'echo_fields[<name>]'. Unknown keys are ignored and blank values dropped.
    """
    data = {}
    allowed = set()
    for _, fields in ECHO_FIELDS:
        for name, _, _ in fields:
            allowed.add(name)
    raw = form.get("echo_fields") if hasattr(form, "get") else None
    if isinstance(raw, dict):
        for key, val in raw.items():
            if key in allowed and str(val).strip():
                data[key] = str(val).strip()
    elif hasattr(form, "getlist"):
        for key in allowed:
            val = form.get(f"echo_fields[{key}]", "")
            if str(val).strip():
                data[key] = str(val).strip()
    return data


def set_echo_fields(incident, data):
    """Serialize a dict of ECHO fields onto the incident (JSON column)."""
    incident.echo_fields = json.dumps(data) if data else None


def allowed_photo(filename):
    """Whether an uploaded filename is an allowed image type."""
    return os.path.splitext(filename or "")[1].lower() in PHOTO_EXTENSIONS


def photo_dir(config=None):
    """Absolute path of the incidents photo folder (created on demand)."""
    from flask import current_app
    base = (config or current_app.config).get("UPLOAD_FOLDER") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "uploads")
    path = os.path.join(base, "incidents")
    os.makedirs(path, exist_ok=True)
    return path


def save_incident_photo(file_storage):
    """Save an uploaded image into the incidents folder.

    Returns the absolute path, or None when the file is not an allowed image
    type. The storage is the flask request FileStorage object.
    """
    if file_storage is None or not allowed_photo(file_storage.filename):
        return None
    safe = secure_filename(file_storage.filename) or "photo.jpg"
    unique = f"{uuid.uuid4().hex[:12]}_{safe}"
    path = os.path.join(photo_dir(), unique)
    file_storage.save(path)
    return path


def add_photo(incident, file_storage, uploaded_by=None):
    """Persist an uploaded photo onto an incident. Returns the photo or None."""
    path = save_incident_photo(file_storage)
    if not path:
        return None
    photo = IncidentPhoto(incident_id=incident.id, file_path=path,
                          uploaded_by=uploaded_by)
    db.session.add(photo)
    return photo


def add_note(incident, text, employee_id=None):
    """Add a note to an incident. Returns the note or None if empty."""
    text = (text or "").strip()
    if not text:
        return None
    note = IncidentNote(incident_id=incident.id, text=text,
                        employee_id=employee_id)
    db.session.add(note)
    return note