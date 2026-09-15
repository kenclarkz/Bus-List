"""Incident report helpers: validation, photo storage, note helpers."""
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