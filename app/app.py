"""Application factory and route registration."""
import atexit
import os
import re
import json
from datetime import date, datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, \
    jsonify, session, send_file, abort
from werkzeug.utils import secure_filename

from .models import db, Vehicle, Employee, ScheduleEntry, Replacement, Note, \
    DailySchedule, PrepReportImport, TrashPickup, Location, \
    IncidentReport, IncidentNote, IncidentPhoto
from .services import settings, vehicles, schedule as sched_svc
from .services import incidents as incidents_svc
from .services.incidents import ISSUE_TYPES, SEVERITIES, STATUSES, \
    allowed_photo, save_incident_photo, SEVERITY_CLASSES, STATUS_CLASSES

# The only three accounts. Passwords are the lowercase role name. No accounts
# can be created through the app.
ROLE_ACCOUNTS = {
    "employee": {"display": "Employee", "password": "employee"},
    "driver": {"display": "Driver", "password": "driver"},
    "manager": {"display": "Manager", "password": "manager"},
}

# Pages only the Manager account may visit.
MANAGER_ONLY_ENDPOINTS = {
    "vehicle_list",
    "vehicle_new",
    "vehicle_detail",
    "vehicle_edit",
    "vehicle_toggle_active",
    "employees_page",
    "employee_toggle_active",
    # Incident management (review / edit / assign / note / photos / resolve).
    "incident_edit",
    "incident_note",
    "incident_photo_upload",
    "incident_resolve",
    "incident_photo_delete",
}


def role_home(role):
    """Default landing page for a signed-in role."""
    if role == "driver":
        return url_for("driver_dashboard")
    return url_for("dashboard")


def _save_uploaded_pdf(data, filename):
    """Save uploaded PDF bytes to disk and return the path."""
    import uuid
    from flask import current_app
    upload_dir = current_app.config.get("UPLOAD_FOLDER") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    safe_name = secure_filename(filename) or "report.pdf"
    unique_name = f"{uuid.uuid4().hex[:12]}_{safe_name}"
    path = os.path.join(upload_dir, unique_name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _parse_imported_by(raw):
    """Parse an 'imported_by' form value into an employee id (or None).

    'manager'            -> (None) the Manager account imported it.
    'employee:<id>'      -> (employee id) a specific employee imported it.
    """
    raw = (raw or "").strip()
    if raw.startswith("employee:"):
        try:
            return int(raw.split(":", 1)[1])
        except (TypeError, ValueError):
            return None
    return None


# ECHO Accident/Incident Report fields captured alongside an incident.
BOOL_REPORT_FIELDS = (
    "police_notified", "injuries_other_party", "employee_injured",
    "employee_citation", "other_driver_is_owner", "other_injuries",
    "other_driver_ticketed", "property_damage", "hazmat_spill",
)

TEXT_REPORT_FIELDS = (
    "driver_name", "police_report_number", "road_name", "intersection_with",
    "county_parish", "city_town", "roadway_conditions", "accident_type",
    "violation_reason", "investigating_supervisor", "employee_supervisor",
    "other_driver_name", "other_driver_address", "other_driver_city",
    "other_driver_state", "other_driver_zip", "other_driver_phone",
    "other_driver_license", "other_driver_license_state", "owner_name",
    "owner_address", "owner_city", "owner_state", "owner_zip", "other_make",
    "other_model", "other_year", "other_color", "other_plate",
    "other_passengers", "insurance_company", "insurance_policy",
    "insurance_address", "insurance_city", "insurance_state", "insurance_zip",
    "insurance_phone", "witnesses", "owner_object_struck",
)


def _parse_yes_no(value):
    """Convert a yes/no form value into True/False/None."""
    value = (value or "").strip().lower()
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


def _apply_report_fields(incident, form):
    """Copy the ECHO Accident/Incident Report form inputs onto a record."""
    for field in TEXT_REPORT_FIELDS:
        setattr(incident, field, (form.get(field) or "").strip() or None)
    for field in BOOL_REPORT_FIELDS:
        setattr(incident, field, _parse_yes_no(form.get(field)))


def create_app(test_config=None):
    app = Flask(__name__)

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    db_uri = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(data_dir, "detail.db"))
    if test_config and test_config.get("SQLALCHEMY_DATABASE_URI"):
        db_uri = test_config["SQLALCHEMY_DATABASE_URI"]
        if "sqlite" in db_uri and db_uri != "sqlite:///:memory:":
            ddir = os.path.dirname(db_uri.replace("sqlite:///", ""))
            if ddir:
                os.makedirs(ddir, exist_ok=True)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        SQLALCHEMY_DATABASE_URI=db_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_FOLDER=os.path.join(base_dir, "uploads"),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    )

    db.init_app(app)
    with app.app_context():
        db.create_all()
        _migrate()
        seed_defaults()

    register_routes(app)
    if not (test_config or app.config.get("TESTING")):
        _start_auto_end_day_scheduler(app)
    return app


def _migrate():
    """Lightweight column migrations for sqlite (no migration framework)."""
    import sqlite3
    from flask import current_app

    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if not uri.startswith("sqlite:///"):
        return
    path = uri.replace("sqlite:///", "", 1)
    if path == ":memory:":
        return
    try:
        con = sqlite3.connect(path)
        cols = {r[1] for r in con.execute("PRAGMA table_info(schedule_entries)")}
        if "prep_time" not in cols:
            con.execute("ALTER TABLE schedule_entries ADD COLUMN prep_time VARCHAR(40)")
            con.commit()
        tcols = {r[1] for r in con.execute("PRAGMA table_info(vehicle_types)")}
        if "checklist" not in tcols:
            con.execute("ALTER TABLE vehicle_types ADD COLUMN checklist TEXT")
            con.commit()
        ecols = {r[1] for r in con.execute("PRAGMA table_info(employees)")}
        if "current_vehicle_id" not in ecols:
            con.execute("ALTER TABLE employees ADD COLUMN current_vehicle_id INTEGER")
            con.commit()
        if "current_vehicle_set_on" not in ecols:
            con.execute("ALTER TABLE employees ADD COLUMN current_vehicle_set_on DATE")
            con.commit()
        # Backfill existing assigned vehicles so they start counting as "today" and
        # get cleared automatically when the next day rolls around.
        con.execute(
            "UPDATE employees SET current_vehicle_set_on = date('now') "
            "WHERE current_vehicle_id IS NOT NULL AND current_vehicle_set_on IS NULL")
        con.commit()
        scols = {r[1] for r in con.execute("PRAGMA table_info(schedule_entries)")}
        if "skip_reason" not in scols:
            con.execute("ALTER TABLE schedule_entries ADD COLUMN skip_reason VARCHAR(255)")
            con.commit()
        scols = {r[1] for r in con.execute("PRAGMA table_info(schedule_entries)")}
        if "pickup_time" not in scols:
            con.execute("ALTER TABLE schedule_entries ADD COLUMN pickup_time VARCHAR(40)")
            con.commit()
        if "driver_code" not in scols:
            con.execute("ALTER TABLE schedule_entries ADD COLUMN driver_code VARCHAR(120)")
            con.commit()
        vcols = {r[1] for r in con.execute("PRAGMA table_info(vehicles)")}
        if "last_dumped" not in vcols:
            con.execute("ALTER TABLE vehicles ADD COLUMN last_dumped DATETIME")
            con.commit()
        if "cleanings_since_dump" not in vcols:
            con.execute("ALTER TABLE vehicles ADD COLUMN cleanings_since_dump INTEGER DEFAULT 0")
            con.commit()
        pcols = {r[1] for r in con.execute("PRAGMA table_info(prep_report_imports)")}
        if "file_path" not in pcols:
            con.execute("ALTER TABLE prep_report_imports ADD COLUMN file_path VARCHAR(512)")
            con.commit()
        icols = {r[1] for r in con.execute("PRAGMA table_info(incident_reports)")}
        INCIDENT_REPORT_COLUMNS = [
            ("driver_name", "VARCHAR(200)"),
            ("police_notified", "BOOLEAN"),
            ("police_report_number", "VARCHAR(80)"),
            ("road_name", "VARCHAR(200)"),
            ("intersection_with", "VARCHAR(200)"),
            ("county_parish", "VARCHAR(120)"),
            ("city_town", "VARCHAR(120)"),
            ("roadway_conditions", "VARCHAR(120)"),
            ("accident_type", "VARCHAR(80)"),
            ("injuries_other_party", "BOOLEAN"),
            ("employee_injured", "BOOLEAN"),
            ("employee_citation", "BOOLEAN"),
            ("violation_reason", "VARCHAR(255)"),
            ("investigating_supervisor", "VARCHAR(200)"),
            ("employee_supervisor", "VARCHAR(200)"),
            ("other_driver_is_owner", "BOOLEAN"),
            ("other_driver_name", "VARCHAR(200)"),
            ("other_driver_address", "VARCHAR(200)"),
            ("other_driver_city", "VARCHAR(120)"),
            ("other_driver_state", "VARCHAR(40)"),
            ("other_driver_zip", "VARCHAR(40)"),
            ("other_driver_phone", "VARCHAR(80)"),
            ("other_driver_license", "VARCHAR(80)"),
            ("other_driver_license_state", "VARCHAR(40)"),
            ("owner_name", "VARCHAR(200)"),
            ("owner_address", "VARCHAR(200)"),
            ("owner_city", "VARCHAR(120)"),
            ("owner_state", "VARCHAR(40)"),
            ("owner_zip", "VARCHAR(40)"),
            ("other_make", "VARCHAR(100)"),
            ("other_model", "VARCHAR(100)"),
            ("other_year", "VARCHAR(20)"),
            ("other_color", "VARCHAR(60)"),
            ("other_plate", "VARCHAR(60)"),
            ("other_passengers", "VARCHAR(40)"),
            ("other_injuries", "BOOLEAN"),
            ("other_driver_ticketed", "BOOLEAN"),
            ("insurance_company", "VARCHAR(200)"),
            ("insurance_policy", "VARCHAR(120)"),
            ("insurance_address", "VARCHAR(200)"),
            ("insurance_city", "VARCHAR(120)"),
            ("insurance_state", "VARCHAR(40)"),
            ("insurance_zip", "VARCHAR(40)"),
            ("insurance_phone", "VARCHAR(80)"),
            ("witnesses", "TEXT"),
            ("property_damage", "BOOLEAN"),
            ("owner_object_struck", "VARCHAR(255)"),
            ("hazmat_spill", "BOOLEAN"),
        ]
        for col, ctype in INCIDENT_REPORT_COLUMNS:
            if col not in icols:
                con.execute(f"ALTER TABLE incident_reports ADD COLUMN {col} {ctype}")
                con.commit()
        con.close()
    except Exception:
        pass


def seed_defaults():
    from .models import Location, Employee, VehicleType
    loc = Location.query.filter_by(name="Main Depot").first()
    if not loc:
        loc = Location(name="Main Depot")
        db.session.add(loc)
        db.session.commit()
    if Employee.query.count() == 0:
        db.session.add(Employee(name="User", location_id=loc.id))
        db.session.commit()
    if Vehicle.query.count() == 0:
        _seed_vehicles(loc)
    else:
        _restore_seed_vehicles(loc)


def _restore_seed_vehicles(loc):
    """Re-activate seeded fleet vehicles that were deactivated by an import.

    Importing a prep report used to set active=False on every vehicle missing
    from the report. Bring those base-fleet units back so the board isn't
    stuck at zero. Vehicles the operator truly retired remain togglable offline;
    the Maintenance unit stays inactive by design.
    """
    changes = False
    for entity, unit, vtype, status, desc, make, model, cap in VEHICLE_SEED_DATA:
        if status == "Maintenance":
            continue
        vehicle = Vehicle.query.filter(
            db.func.lower(Vehicle.unit_number) == str(unit).lower()
        ).filter_by(location_id=loc.id).first()
        if vehicle and not vehicle.active:
            vehicle.active = True
            changes = True
    if changes:
        db.session.commit()


VEHICLE_SEED_DATA = [
    ("UNF", "4301", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4302", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4303", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4304", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4305", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4306", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4307", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4308", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "5100", "ADAMINIVAN", "Active", "MINIBUS ADA LIFT", "Ford", "F450", 14),
    ("UNF", "5307", "ADAMINIBUS", "Active", "MINIBUS ADA LIFT", "Starcraft", "Allstar XL 32", 30),
    ("UNF", "5308", "ADAMINIBUS", "Active", "MINIBUS ADA LIFT", "Starcraft", "Allstar XL 32", 30),
    ("ECHO JAX", "7101", "MINIC34", "Active", "MINI COACH", "TEMSA", "TS-30", 34),
    ("ECHO JAX", "8401", "MOTORC", "Active", "MOTORCOACH", "Vanhool", "CX45", 56),
    ("ECHO JAX", "8406", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8414", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8415", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8416", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8437", "ADAMOTORC", "Active", "MOTORCOACH ADA LIFT", "Vanhool", "CX45", 56),
    ("ECHO JAX", "8438", "ADAMOTORC", "Active", "Motorcoach ADA Lift", "VanHool", "CX45", 56),
    ("ECHO JAX", "8439", "ADAMOTORC", "Active", "Motorcoach ADA Lift", "Vanhool", "CX45", 56),
    ("ECHO JAX", "8492", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8493", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8494", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8495", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "9101", "SEDAN", "Active", "SEDAN", "Volvo", "S90", 3),
    ("ECHO JAX", "9102", "SEDAN", "Active", "SEDAN", "Volvo", "S90", 3),
    ("ECHO JAX", "9142", "SEDAN", "Active", "SEDAN", "Genesis", "G90", 3),
    ("ECHO JAX", "9145", "SEDAN", "Active", "SEDAN", "Genesis", "G80", 3),
    ("ECHO JAX", "9146", "SEDAN", "Out Of Service", "SEDAN", "Cadillac", "XTS", 3),
    ("ECHO JAX", "9201", "SUVSUB", "Active", "SUV - REID", "CHEVROLET", "SUBURBAN", 6),
    ("ECHO JAX", "9202", "SUVSUB", "Active", "SUV - BUNTEN", "CHEVROLET", "SUBURBAN", 6),
    ("ECHO JAX", "9203", "SUVSUB", "Active", "SUV - RICKETTS", "CHEVROLET", "SUBURBAN", 7),
    ("ECHO JAX", "9204", "SUVSUB", "Active", "SUV - WILLIAMS", "CHEVROLET", "SUBURBAN", 7),
    ("ECHO JAX", "9205", "SUVSUB", "Active", "SUV", "CHEVROLET", "SUBURBAN", 7),
    ("ECHO JAX", "9232", "SUVYUKON", "Active", "SUV", "GMC XL", "YUKON", 7),
    ("ECHO JAX", "9233", "SUVYUKON", "Active", "SUV", "GMC", "Yukon Denali", 5),
    ("ECHO JAX", "9234", "SUVSUB", "Active", "SUV", "Ford", "Expedition", 7),
    ("ECHO JAX", "9301", "Van.", "Active", "", "Ford", "Transit", 14),
    ("MSG", "9313", "ADAMINIVAN", "Active", "", "Ford", "Transit", 12),
    ("ECHO JAX", "9321", "VANSPRINTEREXEC", "Active", "", "Mercedes Benz", "Grech Executive", 13),
    ("ECHO JAX", "9331", "Van.", "Active", "WHITE", "Ford", "Transit", 14),
    ("ECHO JAX", "9332", "VANSPRINTEREXEC", "Active", "MERCEDES SPRINTER", "Mercedes", "Sprinter", 14),
    ("ECHO JAX", "9333", "Van.", "Active", "", "Ford", "Transit", 13),
    ("ECHO JAX", "9334", "TRUCK", "Maintenance", "LUGGAGE VEHICLE ONLY - NO PASSENGERS", "Ford", "Venterra", 0),
    ("ECHO JAX", "9335", "Van.", "Active", "Executive Van", "Ford", "Transit", 13),
    ("ECHO JAX", "9336", "Van.", "Active", "Executive Van", "Ford", "Transit", 13),
    ("MSG", "9341", "Van.", "Active", "Marriott Shuttle", "Ford", "E-350", 14),
    ("MSG", "9342", "Van.", "Active", "Marriott Shuttle", "Ford", "E-350", 14),
    ("ECHO JAX", "9416", "MINIBUS", "Active", "MINI BUS / REAR LUGGAGE", "Ford", "Grech GM33", 28),
    ("ECHO JAX", "9417", "MINIBUS", "Active", "MINI BUS / REAR LUGGAGE", "Ford", "Grech GM 33", 28),
    ("ECHO JAX", "9421", "MINIBUS", "Active", "MINI BUS / REAR LUGGAGE", "GMC", "DIAMOND VIP", 24),
    ("ECHO JAX", "9422", "MINIBUS", "Active", "MINI BUS / REAR LUGGAGE", "Ford", "Grech GM28", 22),
    ("ECHO JAX", "9423", "MINIBUS", "Active", "MINI BUS / REAR LUGGAGE", "Ford", "Grech GM28", 22),
    ("ECHO JAX", "9440", "MINIC40", "Active", "GRECH GM40", "Grech", "GM-40", 40),
]


def _seed_vehicles(loc):
    from .services.vehicles import get_or_create_vehicle_type
    for entity, unit, vtype, status, desc, make, model, cap in VEHICLE_SEED_DATA:
        vt = get_or_create_vehicle_type(vtype)
        v = Vehicle(
            unit_number=unit,
            vehicle_type_id=vt.id if vt else None,
            status=status,
            description=desc or None,
            make=make,
            model=model,
            capacity=cap,
            active=(status != "Maintenance"),
            cleaning_frequency=vt.cleaning_frequency_days if vt else 7,
            location_id=loc.id,
        )
        db.session.add(v)
    db.session.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def current_date(form=None):
    dt = (form or request.args).get("date")
    if dt:
        try:
            return datetime.strptime(dt, "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.today()


def fmt_days_since(days):
    """Human-friendly 'time since' label (e.g. 'today', '3 days ago')."""
    if days is None:
        return "never"
    if days < 1:
        hours = int(days * 24)
        return "today" if hours <= 12 else f"{hours} hours ago"
    d = round(days)
    return "1 day ago" if d == 1 else f"{d} days ago"


def status_indicator(last_washed):
    if not last_washed:
        return ("Never Washed", "status-unknown")
    now = datetime.utcnow()
    recent_days = int(settings.get_setting("recent_days", 2) or 2)
    due_days = int(settings.get_setting("due_soon_days", 7) or 7)
    diff = (now - last_washed).days + (now - last_washed).seconds / 86400.0
    if diff <= recent_days:
        return ("Recently Washed", "status-recent")
    if diff <= due_days:
        return ("Due Soon", "status-due")
    return ("Overdue", "status-overdue")


def _prep_time_sort_key(entry):
    """Sort schedule entries by prep time (earliest first). Entries without a
    prep time are moved after all timed ones, ordered by their import position."""
    import re
    m = re.match(r"\s*(\d{1,2}):(\d{2})", entry.prep_time or "")
    if not m:
        return (1, entry.order_index)
    return (0, int(m.group(1)) * 60 + int(m.group(2)))


def build_schedule_view(sched):
    entries_by_id = {e.id: e for e in sched.entries}
    # original entry id -> replacement entry (the vehicle that took its place)
    replaced_by = {}
    for e in sched.entries:
        if e.is_replacement and e.replacement_of_entry_id in entries_by_id:
            replaced_by[e.replacement_of_entry_id] = e

    rows = []
    for entry in sorted(sched.entries, key=_prep_time_sort_key):
        done, total, pct = sched_svc.entry_progress(entry)
        original = entries_by_id.get(entry.replacement_of_entry_id) \
            if entry.is_replacement else None
        replacer = replaced_by.get(entry.id)
        # A replaced vehicle's prep is fulfilled by its replacement, so it
        # counts toward completion (progress treated as fully done) just like
        # a skipped vehicle does.
        if replacer is not None:
            done, total, pct = total, total, 100
        complete = entry.status in ("completed", "skipped") or replacer is not None
        rows.append({
            "entry": entry,
            "vehicle": entry.vehicle,
            "done": done,
            "total": total,
            "pct": pct,
            "is_complete": complete,
            "indicator": status_indicator(entry.vehicle.last_washed),
            # The vehicle this row replaced (for replacement entries).
            "replacement_of": original.vehicle if original else None,
            # The vehicle that replaced this row (for replaced originals).
            "replaced_by": replacer.vehicle if replacer else None,
        })
    return rows


def finalize_day(sched, at=None):
    """Finalize a day's schedule: mark it finalized, record when, and store the
    summary. Returns True if this call finalized it, False if it was already
    finalized (no-op). Shared by the End My Day route and the automatic
    11:50 PM end-of-day job so both produce identical summaries.
    """
    if sched.finalized:
        return False
    rows = build_schedule_view(sched)
    total = len(rows)
    completed = sum(1 for r in rows if r["is_complete"])
    incomplete = total - completed
    overall = round((sum(r["done"] for r in rows) /
                    (sum(r["total"] for r in rows) or 1)) * 100) if rows else 0
    sched.finalized = True
    sched.finalized_at = at or datetime.utcnow()
    sched.summary = json.dumps(dict(
        total=total, completed=completed, incomplete=incomplete,
        overall=overall))
    db.session.commit()
    return True


def _auto_end_day_job(app):
    """Finalize every schedule the employees left open for today. This is the
    scheduled task body: at 11:50 PM each day, any day that wasn't ended by an
    employee is ended automatically. Idempotent, so an employee who already
    clicked End My Day is never touched."""
    count = 0
    with app.app_context():
        unfinalized = DailySchedule.query.filter_by(
            work_date=date.today(), finalized=False).all()
        for sched in unfinalized:
            if finalize_day(sched):
                count += 1
        if count:
            app.logger.info("Auto end-of-day: finalized %s day(s)", count)
    return count


def _auto_end_time():
    """Parse AUTO_END_DAY_TIME (HH:MM, default 23:50) into (hour, minute).
    Invalid values fall back to 23:50."""
    raw = os.environ.get("AUTO_END_DAY_TIME", "23:50").strip()
    try:
        hour, minute = (int(x) for x in raw.split(":", 1))
    except (TypeError, ValueError):
        hour, minute = 23, 50
    return min(max(hour, 0), 23), min(max(minute, 0), 59)


def _start_auto_end_day_scheduler(app):
    """Start the daily 11:50 PM auto end-of-day job (in-process Background
    Scheduler). Skipped while testing so test apps don't spawn threads.

    Time comes from AUTO_END_DAY_TIME (HH:MM, default 23:50) so operators can
    adjust the cutoff without code changes. Multiple admins/workers firing the
    job at the same instant are harmless because finalize_day() is idempotent.
    """
    app.logger.info("Starting auto end-of-day scheduler")
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    hour, minute = _auto_end_time()

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        _auto_end_day_job,
        args=[app],
        trigger=CronTrigger(hour=hour, minute=minute),
        id="auto_end_day",
        replace_existing=True,
    )
    scheduler.start()

    def _shutdown_scheduler():
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass

    atexit.register(_shutdown_scheduler)
    return scheduler


def _nav_links(role):
    """(endpoint, label, icon) tuples for the current role. Rendered by both
    the classic top-bar layout and the new sidepanel layout."""
    links = []
    if role == "driver":
        links.append(("driver_dashboard", "Finished", "✅"))
        links.append(("settings_page", "Settings", "⚙️"))
        return links
    links.append(("dashboard", "Today", "📋"))
    if role == "manager":
        links.append(("vehicle_list", "Vehicles", "🚌"))
    links.append(("import_report", "Import", "📥"))
    links.append(("end_day", "End Day", "🏁"))
    links.append(("history_days", "History", "🕓"))
    links.append(("incidents_list", "Incidents", "⚠️"))
    links.append(("trash_page", "Trash", "🗑️"))
    if role == "manager":
        links.append(("employees_page", "Staff", "👥"))
    links.append(("settings_page", "Settings", "⚙️"))
    return links


def employees_list():
    return Employee.query.filter_by(active=True).all()


def replacement_count_for_date(d):
    start = datetime.combine(d, datetime.min.time())
    end = datetime.combine(d, datetime.max.time())
    return Replacement.query.filter(Replacement.replaced_at >= start,
                                    Replacement.replaced_at <= end).count()


def notes_for_date(d):
    return Note.query.filter_by(work_date=d).all()


def build_import_summary(preview, method):
    return (f"{preview['count']} vehicles parsed. "
            f"New: {len(preview['new'])}, "
            f"Updated: {len(preview['updated'])}, "
            f"Removed: {len(preview['removed'])}, "
            f"Replacements: {len(preview['replacements'])}, "
            f"Uncertain: {len(preview['uncertain'])} [{method}]")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register_routes(app):

    @app.template_filter("fromjson")
    def fromjson_filter(value):
        if not value:
            return {}
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}

    @app.template_filter("task_category")
    def task_category_filter(task_name):
        """Return 'inside' or 'outside' based on the configured categories."""
        if task_name in settings.get_checklist_inside():
            return "inside"
        if task_name in settings.get_checklist_outside():
            return "outside"
        # Unknown tasks default to inside.
        return "inside"

    @app.template_filter("incident_severity_class")
    def incident_severity_class_filter(value):
        return SEVERITY_CLASSES.get(value, "muted")

    @app.template_filter("incident_status_class")
    def incident_status_class_filter(value):
        return STATUS_CLASSES.get(value, "muted")

    @app.template_filter("strip_res")
    def strip_res_filter(value):
        """Strip reservation ('Res # ...') notes so they don't clutter the
        dashboard board. Other operational notes are preserved."""
        if not value:
            return value
        return re.sub(r"(?i)\s*\bRes\s*#\s*[\w.*-]+", "", str(value)).strip()

    @app.context_processor
    def inject_globals():
        user = session.get("user")
        emp = None
        emp_id = None
        if user == "employee" and session.get("employee_id"):
            emp_id = session["employee_id"]
            emp = Employee.query.get(emp_id)
        # Resolve the signed-in user's own stored theme ("on"|"off"|"system"|
        # "futuristic"|"halloween"|"bloomberg"|"synthwave"|"cosmos"|
        # "cyberpunk") to the value used in the data-theme attribute.
        # CSS defines dark styles for "dark" and the special themes, so "on"
        # must map to "dark"; "system" is resolved live by the browser.
        raw_dark_mode = settings.get_user_theme(user, emp_id)
        resolved_dark_mode = "dark" if raw_dark_mode == "on" else raw_dark_mode
        return {
            "today": date.today,
            "checklist": settings.get_checklist(),
            "checklist_inside": settings.get_checklist_inside(),
            "checklist_outside": settings.get_checklist_outside(),
            "app_name": "Detailing Operations Dashboard",
            "current_role": user if user in ROLE_ACCOUNTS else "employee",
            "current_user": ROLE_ACCOUNTS.get(user, {}).get(
                "display") if user else None,
            "current_employee": emp,
            "dark_mode": resolved_dark_mode,
            "layout": settings.get_user_layout(user, emp_id),
            "nav_links": _nav_links(user if user in ROLE_ACCOUNTS else "employee"),
        }

    @app.before_request
    def require_login():
        """Every page except login/logout requires a signed-in account."""
        if request.endpoint in ("static", "login", "logout"):
            return None
        user = session.get("user")
        if not user or user not in ROLE_ACCOUNTS:
            session.clear()
            return redirect(url_for("login"))
        # Drivers only see the finished-vehicles screen (plus their own Settings
        # page for their theme choice and the incident report submission flow).
        if user == "driver" and request.endpoint not in (
                "driver_dashboard", "settings_page", "incidents_list",
                "incident_new", "incident_detail", "incident_pdf",
                "incident_photo"):
            return redirect(url_for("driver_dashboard"))
        # Vehicles, Staff and operational Settings are manager-only.
        if user != "manager" and request.endpoint in MANAGER_ONLY_ENDPOINTS:
            return redirect(url_for("dashboard"))
        # Employees must pick their name from the dropdown before using the
        # board. Login, the splash animation and the picker itself are exempt.
        if user == "employee" and not session.get("employee_id") and \
                request.endpoint not in ("splash", "select_employee"):
            return redirect(url_for("select_employee"))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("user") in ROLE_ACCOUNTS:
            return redirect(url_for("splash"))
        if request.method == "POST":
            username = (request.form.get("username") or "").strip().lower()
            password = request.form.get("password") or ""
            account = ROLE_ACCOUNTS.get(username)
            if account and password == account["password"]:
                session.clear()
                session["user"] = username
                session["username"] = account["display"]
                flash(f"Welcome, {account['display']}", "success")
                return redirect(url_for("splash"))
            flash("Invalid username or password", "error")
            return redirect(url_for("login"))
        return render_template("login.html")

    @app.route("/splash")
    def splash():
        """Fullscreen intro animation played after login before the dashboard."""
        return render_template("splash.html")

    @app.route("/select", methods=["GET", "POST"])
    def select_employee():
        """After the splash, employees pick their name before the board unlocks."""
        role = session.get("user")
        if role == "driver":
            return redirect(url_for("driver_dashboard"))
        if role != "employee":
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            try:
                emp = Employee.query.get(int(request.form.get("employee_id")))
            except (TypeError, ValueError):
                emp = None
            if emp and emp.active:
                session["employee_id"] = emp.id
                session["employee_name"] = emp.name
                flash(f"Signed in as {emp.name}", "success")
                return redirect(url_for("dashboard"))
            flash("Please choose your name to continue", "error")
            return redirect(url_for("select_employee"))
        return render_template("select.html", employees=employees_list())

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("You have been logged out", "success")
        return redirect(url_for("login"))

    @app.route("/set-current-vehicle", methods=["POST"])
    def set_current_vehicle():
        emp_id = request.form.get("employee_id")
        vehicle_id = request.form.get("vehicle_id") or None
        if emp_id:
            emp = Employee.query.get(int(emp_id))
            if emp:
                emp.current_vehicle_id = int(vehicle_id) if vehicle_id else None
                emp.current_vehicle_set_on = date.today() if vehicle_id else None
                db.session.commit()
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/start-work", methods=["POST"])
    def start_work():
        if session.get("role", "employee") != "employee":
            return jsonify(ok=False, error="Manager view is read-only"), 403
        emp_id = request.json.get("employee_id") if request.is_json else request.form.get("employee_id")
        entry_id = request.json.get("entry_id") if request.is_json else request.form.get("entry_id")
        if not emp_id or not entry_id:
            return jsonify(ok=False, error="Missing employee_id or entry_id"), 400
        emp = Employee.query.get(int(emp_id))
        entry = ScheduleEntry.query.get(int(entry_id))
        if not emp or not entry:
            return jsonify(ok=False, error="Invalid employee or entry"), 404
        emp.current_vehicle_id = entry.vehicle_id
        emp.current_vehicle_set_on = date.today()
        if entry.status == "pending":
            entry.status = "in_progress"
        db.session.commit()
        return jsonify(ok=True, employee=emp.name, initials=emp.initials,
                       vehicle=entry.vehicle.unit_number)

    @app.route("/")
    def dashboard():
        from datetime import timedelta
        # Accept ?date=YYYY-MM-DD, default to today
        date_str = request.args.get("date", "").strip()
        try:
            view_date = date.fromisoformat(date_str) if date_str else date.today()
        except ValueError:
            view_date = date.today()

        loc = vehicles.default_location()
        sched = sched_svc.get_or_create_schedule(d=view_date, location=loc)
        has_import = PrepReportImport.query.filter_by(
            applied=True, schedule_date=sched.work_date).first() is not None
        # Show the board if a prep report was imported OR vehicles were added
        # manually, so operators can always see (and work) the day's list.
        rows = build_schedule_view(sched) if (has_import or sched.entries) else []

        total = len(rows)
        completed = sum(1 for r in rows if r["is_complete"])
        in_progress = sum(1 for r in rows if r["entry"].status == "in_progress"
                          and not r["replaced_by"])
        skipped = sum(1 for r in rows if r["entry"].status == "skipped")
        remaining = total - completed - in_progress
        overall = round((sum(r["done"] for r in rows) /
                        (sum(r["total"] for r in rows) or 1)) * 100) if rows else 0
        overdue = sum(1 for r in rows if r["indicator"][0] == "Overdue")
        replacements = replacement_count_for_date(sched.work_date)

        # Build date navigation links (today, tomorrow, +2 days)
        nav_dates = []
        for offset in range(3):
            d = date.today() + timedelta(days=offset)
            nav_dates.append({
                "date": d,
                "label": ["Today", "Tomorrow", "+2 Days"][offset],
                "iso": d.isoformat(),
                "active": view_date == d,
            })

        # Check which dates have imports applied
        imported_dates = set()
        for imp in PrepReportImport.query.filter_by(applied=True).all():
            if imp.schedule_date:
                imported_dates.add(imp.schedule_date.isoformat())

        filters = {
            "unit": request.args.get("unit", "").strip(),
            "type": request.args.get("type", "").strip(),
            "route": request.args.get("route", "").strip(),
            "status": request.args.get("status", "").strip(),
        }
        q_unit = filters["unit"].lower()
        q_type = filters["type"].lower()
        q_route = filters["route"].lower()
        q_status = filters["status"]

        frows = []
        for r in rows:
            v = r["vehicle"]
            if q_unit and q_unit not in v.unit_number.lower():
                continue
            if q_type and not (v.vehicle_type and
                               q_type in v.vehicle_type.name.lower()):
                continue
            if q_route and q_route not in (v.route or "").lower():
                continue
            if q_status and q_status != r["entry"].status:
                continue
            frows.append(r)

        types = sorted({v.vehicle_type.name for v in Vehicle.query
                        if v.vehicle_type and v.vehicle_type.name})

        # Build employee current vehicle map for active employees today.
        # Clear any assignments left over from a previous day (employees who
        # forgot to hit Done) so the "Now Working" board doesn't go stale.
        sched_svc.clear_stale_current_vehicles()
        active_employees = []
        for emp in Employee.query.filter_by(active=True).order_by(Employee.name).all():
            cv = emp.current_vehicle
            active_employees.append({
                "id": emp.id, "name": emp.name, "initials": emp.initials,
                "current_vehicle": cv.unit_number if cv else None,
            })

        return render_template(
            "dashboard.html",
            rows=frows, all_rows=rows, sched=sched,
            total=total, completed=completed, in_progress=in_progress,
            skipped=skipped, remaining=remaining, overall=overall, overdue=overdue,
            replacements=replacements, types=types, filters=filters,
            employees=employees_list(),
            nav_dates=nav_dates, view_date=view_date,
            imported_dates=imported_dates,
            active_employees=active_employees,
        )

    @app.route("/driver")
    def driver_dashboard():
        """Driver screen: read-only list of finished vehicles for today and next two days."""
        loc = vehicles.default_location()
        days = []
        for offset in range(3):
            d = date.today() + timedelta(days=offset)
            sched = sched_svc.get_or_create_schedule(d=d, location=loc)
            rows = []
            for entry in sorted(sched.entries, key=_prep_time_sort_key):
                if entry.status != "completed":
                    continue
                done, total, pct = sched_svc.entry_progress(entry)
                workers = sorted({t.employee.initials for t in entry.tasks
                                  if t.completed and t.employee})
                finished_at = max((t.completed_at for t in entry.tasks
                                   if t.completed_at), default=None)
                rows.append({
                    "entry": entry,
                    "vehicle": entry.vehicle,
                    "done": done,
                    "total": total,
                    "pct": pct,
                    "workers": workers,
                    "finished_at": finished_at,
                })
            days.append({"date": d, "rows": rows})
        return render_template("driver.html", days=days)

    @app.route("/vehicles")
    def vehicle_list():
        loc = vehicles.default_location()
        vq = Vehicle.query.filter_by(location_id=loc.id).all()
        return render_template("vehicles.html", vehicles=vq)

    @app.route("/vehicles/new", methods=["GET", "POST"])
    def vehicle_new():
        if request.method == "POST":
            unit = request.form.get("unit_number")
            if not unit:
                flash("Unit number is required", "error")
                return redirect(url_for("vehicle_new"))
            vehicle, _ = vehicles.find_or_create_vehicle(
                unit,
                vehicle_type=request.form.get("vehicle_type") or None,
                route=request.form.get("route") or None,
                notes=request.form.get("notes") or None,
                location_id=vehicles.default_location().id,
            )
            vehicle.status = request.form.get("status") or "Active"
            vehicle.make = request.form.get("make") or None
            vehicle.model = request.form.get("model") or None
            vehicle.description = request.form.get("description") or None
            try:
                vehicle.capacity = int(request.form.get("capacity", 0))
            except (ValueError, TypeError):
                pass
            db.session.commit()
            flash(f"Vehicle {vehicle.unit_number} created", "success")
            return redirect(url_for("vehicle_detail", vehicle_id=vehicle.id))
        return render_template("vehicle_form.html")

    @app.route("/vehicles/<int:vehicle_id>")
    def vehicle_detail(vehicle_id):
        vehicle = Vehicle.query.get_or_404(vehicle_id)
        return render_template("vehicle_detail.html", vehicle=vehicle,
                               indicator=status_indicator(vehicle.last_washed))

    @app.route("/vehicles/<int:vehicle_id>/edit", methods=["GET", "POST"])
    def vehicle_edit(vehicle_id):
        vehicle = Vehicle.query.get_or_404(vehicle_id)
        if request.method == "POST":
            vehicle.unit_number = request.form.get("unit_number", vehicle.unit_number)
            vehicle.route = request.form.get("route") or None
            vehicle.status = request.form.get("status") or "Active"
            vehicle.notes = request.form.get("notes") or None
            vehicle.make = request.form.get("make") or None
            vehicle.model = request.form.get("model") or None
            vehicle.description = request.form.get("description") or None
            vehicle.active = request.form.get("active") == "on"
            vt_name = request.form.get("vehicle_type")
            if vt_name:
                vehicle.vehicle_type = vehicles.get_or_create_vehicle_type(vt_name)
            try:
                vehicle.cleaning_frequency = int(
                    request.form.get("cleaning_frequency", vehicle.cleaning_frequency))
                vehicle.capacity = int(request.form.get("capacity", 0))
            except (ValueError, TypeError):
                pass
            db.session.commit()
            flash("Vehicle updated", "success")
            return redirect(url_for("vehicle_detail", vehicle_id=vehicle.id))
        return render_template("vehicle_form.html", vehicle=vehicle)

    @app.route("/vehicles/<int:vehicle_id>/toggle-active", methods=["POST"])
    def vehicle_toggle_active(vehicle_id):
        vehicle = Vehicle.query.get_or_404(vehicle_id)
        vehicle.active = not vehicle.active
        db.session.commit()
        return redirect(url_for("vehicle_detail", vehicle_id=vehicle.id))

    @app.route("/import", methods=["GET", "POST"])
    def import_report():
        if request.method == "POST":
            file = request.files.get("pdf")
            if not file or not file.filename:
                flash("Please choose a prep report PDF", "error")
                return redirect(url_for("import_report"))
            data = file.read()
            # Which day to import for (default today)
            sched_date = request.form.get("sched_date", "").strip()
            try:
                sched_dt = date.fromisoformat(sched_date) if sched_date else date.today()
            except ValueError:
                sched_dt = date.today()
            from app.services.pdf_parser import parse_prep_report
            from app.services import schedule as ss
            parsed, method, warnings = parse_prep_report(data, file.filename)
            preview = ss.build_preview(parsed, location=vehicles.default_location())
            summary = build_import_summary(preview, method)
            file_path = _save_uploaded_pdf(data, file.filename)
            imp = vehicles.record_import(
                file.filename, applied=False, summary=summary,
                preview=preview, method=method, file_path=file_path,
                employee_id=_parse_imported_by(request.form.get("imported_by")))
            return render_template(
                "import_preview.html",
                preview=preview, warnings=warnings, method=method,
                import_id=imp.id,
                sched_date=sched_dt.isoformat(),
                employees=employees_list(),
                imported_by_employee_id=imp.employee_id)
        return render_template("import.html", import_dates=_import_date_options(),
                               employees=employees_list())

    @app.route("/import/<int:import_id>/view")
    def import_view(import_id):
        imp = PrepReportImport.query.get_or_404(import_id)
        if not imp.file_path or not os.path.isfile(imp.file_path):
            flash("Original PDF file is no longer available", "error")
            return redirect(url_for("history_days"))
        return render_template(
            "import_view.html", imp=imp, pdf_url=url_for("import_pdf", import_id=import_id))

    @app.route("/import/<int:import_id>/pdf")
    def import_pdf(import_id):
        imp = PrepReportImport.query.get_or_404(import_id)
        if not imp.file_path or not os.path.isfile(imp.file_path):
            flash("Original PDF file is no longer available", "error")
            return redirect(url_for("history_days"))
        return send_file(imp.file_path, mimetype="application/pdf")

    @app.route("/import/<int:import_id>/apply", methods=["POST"])
    def import_apply(import_id):
        imp = PrepReportImport.query.get_or_404(import_id)
        preview = json.loads(imp.preview_json)
        sched_date_str = request.form.get("sched_date", "").strip()
        try:
            sched_dt = date.fromisoformat(sched_date_str) if sched_date_str else date.today()
        except ValueError:
            sched_dt = date.today()
        sched = sched_svc.apply_import(
            preview,
            location=vehicles.default_location(),
            employee_id=request.form.get("employee_id") or None,
            source="import",
            schedule_date=sched_dt)
        imp.applied = True
        imp.applied_at = datetime.utcnow()
        imp.schedule_date = sched.work_date
        imported_by_raw = request.form.get("imported_by")
        if imported_by_raw is not None:
            imp.employee_id = _parse_imported_by(imported_by_raw)
        db.session.commit()
        flash(f"Prep report applied for {sched_dt.strftime('%b %d')}. Work list updated.", "success")
        return redirect(url_for("dashboard", date=sched_dt.isoformat()))

    @app.route("/task/<int:entry_id>/<path:task_name>", methods=["POST"])
    def task_toggle(entry_id, task_name):
        if session.get("user") != "employee":
            return jsonify(ok=False, error="Manager view is read-only"), 403
        checked = request.form.get("checked") == "true"
        emp = request.form.get("employee_id") or None
        task = sched_svc.toggle_task(entry_id, task_name, checked, emp)
        done = total = pct = None
        if task:
            done, total, pct = sched_svc.entry_progress(task.entry)
        return jsonify(ok=True, done=done, total=total, pct=pct)

    @app.route("/entry/<int:entry_id>/skip", methods=["POST"])
    def entry_skip(entry_id):
        wants_json = request.headers.get("Accept", "") == "application/json"
        if session.get("user") != "employee":
            if wants_json:
                return jsonify(ok=False, error="Manager view is read-only"), 403
            flash("Manager view is read-only", "error")
            return redirect(url_for("dashboard"))
        entry = ScheduleEntry.query.get_or_404(entry_id)
        reason = request.form.get("reason", "").strip()
        if not reason:
            reason = entry.skip_reason or ""
        status = sched_svc.set_entry_skipped(entry, skipped=True, reason=reason)
        if wants_json:
            return jsonify(ok=True, unit=entry.vehicle.unit_number, reason=reason)
        flash(f"Vehicle {entry.vehicle.unit_number} marked as skipped", "success")
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/entry/<int:entry_id>/unskip", methods=["POST"])
    def entry_unskip(entry_id):
        if session.get("user") != "employee":
            flash("Manager view is read-only", "error")
            return redirect(url_for("dashboard"))
        entry = ScheduleEntry.query.get_or_404(entry_id)
        sched_svc.set_entry_skipped(entry, skipped=False)
        flash(f"Vehicle {entry.vehicle.unit_number} un-skipped", "success")
        # Drop back to the same row after the full reload instead of the top.
        ref = (request.referrer or url_for("dashboard")).split("#", 1)[0]
        return redirect(f"{ref}#row-{entry_id}")

    @app.route("/entry/<int:entry_id>/complete", methods=["POST"])
    def entry_complete(entry_id):
        if session.get("user") != "employee":
            return jsonify(ok=False, error="Manager view is read-only"), 403
        entry = ScheduleEntry.query.get_or_404(entry_id)
        sched_svc.complete_entry(entry)
        done, total, pct = sched_svc.entry_progress(entry)
        incomplete = [t.task_name for t in entry.tasks if not t.completed]
        return jsonify(ok=True, done=done, total=total, pct=pct,
                       incomplete=incomplete)

    @app.route("/schedule/<int:entry_id>/replace", methods=["POST"])
    def entry_replace(entry_id):
        entry = ScheduleEntry.query.get_or_404(entry_id)
        repl_unit = request.form.get("replacement_unit", "").strip()
        reason = request.form.get("reason", "")
        if not repl_unit:
            flash("Replacement vehicle unit is required", "error")
            return redirect(url_for("dashboard"))
        vehicle, _ = vehicles.find_or_create_vehicle(
            repl_unit, location_id=vehicles.default_location().id)
        employee_id = request.form.get("employee_id") or None
        sched_svc.move_entry_to_replacement(
            entry.schedule, entry, vehicle, reason, employee_id)
        flash(f"Vehicle {entry.vehicle.unit_number} replaced by "
              f"{vehicle.unit_number}", "success")
        return redirect(url_for("dashboard"))

    @app.route("/schedule/add", methods=["POST"])
    def schedule_add():
        sched_date = request.form.get("date", "").strip()
        try:
            sched_dt = date.fromisoformat(sched_date) if sched_date else date.today()
        except ValueError:
            sched_dt = date.today()
        unit = request.form.get("unit_number", "").strip()
        if not unit:
            flash("Unit number is required", "error")
            return redirect(url_for("dashboard", date=sched_dt.isoformat()))
        loc = vehicles.default_location()
        vehicle, _ = vehicles.find_or_create_vehicle(
            unit,
            vehicle_type=request.form.get("vehicle_type") or None,
            route=request.form.get("route") or None,
            location_id=loc.id,
        )
        # If the vehicle already exists, surface any changes the operator entered.
        if request.form.get("route"):
            vehicle.route = request.form["route"]
        vehicle.status = request.form.get("status") or vehicle.status or "Active"
        vehicle.active = True
        prep_time = request.form.get("prep_time") or None
        pickup_time = request.form.get("pickup_time") or None
        driver_code = request.form.get("driver_code") or None
        sched = sched_svc.get_or_create_schedule(d=sched_dt, location=loc)
        order = (max((e.order_index for e in sched.entries), default=-1) + 1)
        entry = sched_svc.ensure_entry(
            sched, vehicle, order_index=order, prep_time=prep_time,
            pickup_time=pickup_time, driver_code=driver_code)
        db.session.commit()
        flash(f"Vehicle {vehicle.unit_number} added to {sched_dt.strftime('%b %d')}'s board",
              "success")
        return redirect(url_for("dashboard", date=sched_dt.isoformat()))

    @app.route("/notes/<path:date>", methods=["POST"])
    def add_note(date):
        text = request.form.get("text", "").strip()
        employee_id = request.form.get("employee_id") or None
        if text:
            d = datetime.strptime(date, "%Y-%m-%d").date()
            sess = DailySchedule.query.filter_by(
                work_date=d,
                location_id=vehicles.default_location().id).first()
            note = Note(work_date=d, text=text, employee_id=employee_id,
                        schedule_id=sess.id if sess else None)
            db.session.add(note)
            db.session.commit()
            flash("Note added", "success")
        return redirect(request.referrer or url_for("end_day", date=date))

    @app.route("/end", methods=["GET", "POST"])
    def end_day():
        d = current_date()
        loc = vehicles.default_location()
        sched = sched_svc.get_or_create_schedule(d, loc)
        rows = build_schedule_view(sched)
        notes = notes_for_date(d)
        replacements = replacement_count_for_date(d)

        total = len(rows)
        completed = sum(1 for r in rows if r["is_complete"])
        skipped = sum(1 for r in rows if r["entry"].status == "skipped")
        incomplete = total - completed
        overall = round((sum(r["done"] for r in rows) /
                        (sum(r["total"] for r in rows) or 1)) * 100) if rows else 0
        incomplete_rows = [r for r in rows if not r["is_complete"]]
        completed_rows = []
        for r in rows:
            if not r["is_complete"]:
                continue
            emp_tasks = {}
            for t in r["entry"].tasks:
                if t.completed and t.employee:
                    emp_tasks.setdefault(t.employee.name, []).append(t.task_name)
            completed_rows.append({**r, "employees": emp_tasks})

        if request.method == "POST" and request.form.get("confirm") == "yes":
            finalize_day(sched)
            flash("Day finalized and saved to history", "success")
            return redirect(url_for("history_days"))

        # Date nav for end day (today, tomorrow, +2 days)
        from datetime import timedelta
        nav_dates = []
        for offset in range(3):
            nd = date.today() + timedelta(days=offset)
            nav_dates.append({
                "date": nd, "iso": nd.isoformat(),
                "label": ["Today", "Tomorrow", "+2 Days"][offset],
                "active": d == nd,
            })

        # Per-employee stats
        emp_done = {}
        total_tasks = 0
        for r in rows:
            for t in r["entry"].tasks:
                total_tasks += 1
                if t.completed and t.employee:
                    emp_done[t.employee.name] = emp_done.get(t.employee.name, 0) + 1
        employee_stats = sorted(
            [{"name": n,
              "initials": Employee.query.filter_by(name=n).first().initials,
              "done": d, "total": total_tasks,
              "pct": round(d / total_tasks * 100) if total_tasks else 0}
             for n, d in emp_done.items()],
            key=lambda x: x["name"])

        return render_template(
            "end_day.html", rows=rows, sched=sched, notes=notes,
            replacements=replacements, d=d,
            total=total, completed=completed, incomplete=incomplete,
            skipped=skipped,
            overall=overall, incomplete_rows=incomplete_rows,
            completed_rows=completed_rows,
            finalized=sched.finalized, employees=employees_list(),
            nav_dates=nav_dates, employee_stats=employee_stats)

    @app.route("/print/<path:date>")
    def print_report(date):
        d = datetime.strptime(date, "%Y-%m-%d").date()
        loc = vehicles.default_location()
        sched = sched_svc.get_or_create_schedule(d, loc)
        rows = build_schedule_view(sched)
        notes = notes_for_date(d)
        replacements = replacement_count_for_date(d)
        total = len(rows)
        completed = sum(1 for r in rows if r["is_complete"])
        skipped = sum(1 for r in rows if r["entry"].status == "skipped")
        overall = round((sum(r["done"] for r in rows) /
                        (sum(r["total"] for r in rows) or 1)) * 100) if rows else 0
        # Per-employee stats
        emp_done = {}
        total_tasks = 0
        for r in rows:
            for t in r["entry"].tasks:
                total_tasks += 1
                if t.completed and t.employee:
                    emp_done[t.employee.name] = emp_done.get(t.employee.name, 0) + 1
        employee_stats = sorted(
            [{"name": n,
              "initials": Employee.query.filter_by(name=n).first().initials,
              "done": d, "total": total_tasks,
              "pct": round(d / total_tasks * 100) if total_tasks else 0}
             for n, d in emp_done.items()],
            key=lambda x: x["name"])
        return render_template(
            "print_report.html", rows=rows, notes=notes, d=d, sched=sched,
            replacements=replacements, total=total, completed=completed,
            skipped=skipped,
            overall=overall, employee_stats=employee_stats)

    @app.route("/history")
    def history_days():
        days = DailySchedule.query.order_by(
            DailySchedule.work_date.desc()).limit(60).all()
        imports = PrepReportImport.query.order_by(
            PrepReportImport.imported_at.desc()).limit(30).all()
        replacements = Replacement.query.order_by(
            Replacement.replaced_at.desc()).limit(50).all()
        return render_template(
            "history.html", days=days, imports=imports, replacements=replacements)

    @app.route("/import/<int:import_id>/delete", methods=["POST"])
    def import_delete(import_id):
        imp = PrepReportImport.query.get_or_404(import_id)
        count = vehicles.remove_import(imp)
        flash(f"Prep report import deleted along with {count} vehicle(s)", "success")
        return redirect(url_for("history_days"))

    @app.route("/schedule/<int:schedule_id>/delete", methods=["POST"])
    def schedule_delete(schedule_id):
        sched = DailySchedule.query.get_or_404(schedule_id)
        if sched.work_date == date.today():
            flash("Today's board cannot be deleted", "error")
            return redirect(url_for("history_days"))
        count = sched_svc.delete_schedule(sched)
        flash(f"Deleted previous day {sched.work_date.strftime('%b %d %Y')} "
              f"with {count} vehicle(s)", "success")
        return redirect(url_for("history_days"))

    @app.route("/history/vehicle/<int:vehicle_id>")
    def vehicle_history(vehicle_id):
        vehicle = Vehicle.query.get_or_404(vehicle_id)
        return render_template("vehicle_detail.html", vehicle=vehicle,
                               indicator=status_indicator(vehicle.last_washed))

    @app.route("/employees", methods=["GET", "POST"])
    def employees_page():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if name:
                db.session.add(Employee(
                    name=name, location_id=vehicles.default_location().id))
                db.session.commit()
                flash("Employee added", "success")
            return redirect(url_for("employees_page"))
        employees = Employee.query.all()
        return render_template("employees.html", employees=employees)

    @app.route("/employees/<int:employee_id>/toggle-active", methods=["POST"])
    def employee_toggle_active(employee_id):
        employee = Employee.query.get_or_404(employee_id)
        employee.active = not employee.active
        if not employee.active:
            # Free the removed employee from any vehicle they were working on.
            employee.current_vehicle_id = None
        db.session.commit()
        return redirect(url_for("employees_page"))

    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        from .models import VehicleType
        from .services.schedule import refresh_type_entries
        user = session.get("user")
        emp_id = session.get("employee_id")
        if request.method == "POST":
            # Theme is the one setting every account can change, and it is
            # stored per-user so one person's choice never changes someone
            # else's appearance.
            dark_mode = request.form.get("dark_mode")
            if dark_mode in settings.THEME_CHOICES:
                settings.set_user_theme(user, emp_id, dark_mode)
            # Layout is another per-user appearance choice: classic (top bar,
            # the default site design) or sidepanel (the new design).
            layout = request.form.get("layout")
            if layout in settings.LAYOUT_CHOICES:
                settings.set_user_layout(user, emp_id, layout)
            # The remaining operational settings are manager-only.
            if user == "manager":
                for key in ["recent_days", "due_soon_days", "location"]:
                    val = request.form.get(key)
                    if val is not None:
                        settings.set_setting(key, val)
                # Categorized checklist: Inside and Outside task lists.
                inside = request.form.get("checklist_inside")
                if inside is not None:
                    settings.set_setting("checklist_inside", inside)
                outside = request.form.get("checklist_outside")
                if outside is not None:
                    settings.set_setting("checklist_outside", outside)
                # Per-vehicle-type checklists (Inside + Outside). A type uses
                # the global default unless its own fields are submitted.
                for vt in VehicleType.query.all():
                    in_val = request.form.get(f"type_checklist_inside_{vt.id}")
                    out_val = request.form.get(f"type_checklist_outside_{vt.id}")
                    if in_val is None and out_val is None:
                        continue
                    in_tasks = [x.strip() for x in (in_val or "").split(",")
                                if x.strip()]
                    out_tasks = [x.strip() for x in (out_val or "").split(",")
                                 if x.strip()]
                    combined = settings.format_type_checklist_for_storage(
                        in_tasks, out_tasks)
                    new_val = combined or None
                    if vt.checklist != new_val:
                        vt.checklist = new_val
                        db.session.commit()
                        refresh_type_entries(vt)
            db.session.commit()
            flash("Settings saved", "success")
            return redirect(url_for("settings_page"))
        vtypes = VehicleType.query.order_by(VehicleType.name).all()
        for vt in vtypes:
            cat = settings.get_type_categorized_checklist(vt)
            vt._inside = ", ".join(cat["inside"])
            vt._outside = ", ".join(cat["outside"])
        return render_template("settings.html", settings={
            "recent_days": settings.get_setting("recent_days", 2),
            "due_soon_days": settings.get_setting("due_soon_days", 7),
            "location": settings.get_setting("location") or "Main Depot",
            "checklist_inside": ", ".join(settings.get_checklist_inside()),
            "checklist_outside": ", ".join(settings.get_checklist_outside()),
            "dark_mode": settings.get_user_theme(user, emp_id),
            "layout": settings.get_user_layout(user, emp_id),
        }, vehicle_types=vtypes)

    @app.route("/trash", methods=["GET", "POST"])
    def trash_page():
        """Trash tab: shows the last time trash was picked up from each lot
        and lets staff record a new pickup."""
        if request.method == "POST":
            picked = request.form.get("picked_at", "").strip()
            when = datetime.utcnow()
            if picked:
                try:
                    when = datetime.fromisoformat(picked)
                except ValueError:
                    pass
            pickup = TrashPickup(
                location_id=request.form.get("location_id")
                or vehicles.default_location().id,
                picked_up_at=when,
                notes=request.form.get("notes", "").strip() or None,
                employee_id=request.form.get("employee_id") or None,
            )
            db.session.add(pickup)
            db.session.commit()
            flash("Trash pickup recorded", "success")
            return redirect(url_for("trash_page"))

        default_loc = vehicles.default_location()
        locations = Location.query.order_by(Location.name).all()
        if not locations:
            locations = [default_loc]
        rows = []
        for loc in locations:
            last = TrashPickup.query.filter_by(
                location_id=loc.id).order_by(
                    TrashPickup.picked_up_at.desc()).first()
            never = last is None
            days_since = None
            freshness = ("Never", "muted")
            if last:
                diff = datetime.utcnow() - last.picked_up_at
                days_since = diff.days + diff.seconds / 86400.0
                if days_since <= 1:
                    freshness = ("Recent", "success")
                elif days_since <= 7:
                    freshness = ("Due", "warn")
                else:
                    freshness = ("Overdue", "danger")
            rows.append({
                "location": loc,
                "last_pickup": last,
                "never": never,
                "days_since": fmt_days_since(days_since),
                "freshness": freshness,
            })
        return render_template("trash.html", rows=rows,
                               employees=employees_list(),
                               locations=locations,
                               default_location=default_loc,
                               now_iso=datetime.utcnow().strftime("%Y-%m-%dT%H:%M"))

    # ------------------------------------------------------------------
    # Incident reports
    # ------------------------------------------------------------------

    def _incident_actor_id():
        """Resolve who is recording an incident action: the signed-in employee
        id, or None for the Manager account."""
        if session.get("user") == "employee" and session.get("employee_id"):
            try:
                return int(session["employee_id"])
            except (TypeError, ValueError):
                return None
        return None

    @app.route("/incidents")
    def incidents_list():
        filters = {
            "unit": request.args.get("unit", "").strip(),
            "type": request.args.get("type", "").strip(),
            "severity": request.args.get("severity", "").strip(),
            "employee": request.args.get("employee", "").strip(),
            "status": request.args.get("status", "").strip(),
        }
        q = IncidentReport.query.join(
            Vehicle, IncidentReport.vehicle_id == Vehicle.id)
        if filters["unit"]:
            q = q.filter(
                Vehicle.unit_number.ilike(f"%{filters['unit']}%"))
        if filters["type"]:
            q = q.filter(IncidentReport.issue_type == filters["type"])
        if filters["severity"]:
            q = q.filter(IncidentReport.severity == filters["severity"])
        if filters["status"]:
            q = q.filter(IncidentReport.status == filters["status"])
        if filters["employee"]:
            try:
                emp_id = int(filters["employee"])
            except ValueError:
                emp_id = None
            if emp_id:
                q = q.filter(db.or_(IncidentReport.reported_by == emp_id,
                                    IncidentReport.assigned_to == emp_id))
        incidents = q.order_by(IncidentReport.created_at.desc()).all()

        summary = {
            "open": IncidentReport.query.filter_by(status="Open").count(),
            "in_progress": IncidentReport.query.filter_by(
                status="In Progress").count(),
            "resolved": IncidentReport.query.filter_by(status="Resolved").count(),
            "total": IncidentReport.query.count(),
        }
        return render_template(
            "incidents.html", incidents=incidents, filters=filters,
            issue_types=ISSUE_TYPES, severities=SEVERITIES, statuses=STATUSES,
            employees=Employee.query.filter_by(active=True)
            .order_by(Employee.name).all(),
            summary=summary)

    @app.route("/incidents/new", methods=["GET", "POST"])
    def incident_new():
        if request.method == "POST":
            vehicle = None
            vehicle_id = request.form.get("vehicle_id", "").strip()
            if vehicle_id:
                vehicle = Vehicle.query.get(int(vehicle_id))
            if not vehicle:
                unit = request.form.get("unit_number", "").strip()
                if unit:
                    vehicle, _ = vehicles.find_or_create_vehicle(
                        unit, location_id=vehicles.default_location().id)
            if not vehicle:
                flash("Please choose a vehicle", "error")
                return redirect(url_for("incident_new"))

            issue_type = request.form.get("issue_type", "").strip()
            if issue_type not in ISSUE_TYPES:
                flash("Please choose a valid issue type", "error")
                return redirect(url_for("incident_new"))
            description = request.form.get("description", "").strip()
            if not description:
                flash("Description is required", "error")
                return redirect(url_for("incident_new"))
            severity = request.form.get("severity", "Medium").strip()
            if severity not in SEVERITIES:
                severity = "Medium"
            occurred_at = datetime.utcnow()
            occurred_raw = request.form.get("occurred_at", "").strip()
            if occurred_raw:
                try:
                    occurred_at = datetime.fromisoformat(occurred_raw)
                except ValueError:
                    pass
            # Reported by: managers pick from the list, employees are always
            # the currently selected employee.
            reported_by = _incident_actor_id()
            if reported_by is None and session.get("user") == "manager":
                raw = request.form.get("reported_by", "").strip()
                try:
                    reported_by = int(raw) if raw else None
                except ValueError:
                    reported_by = None

            incident = IncidentReport(
                vehicle_id=vehicle.id,
                issue_type=issue_type,
                severity=severity,
                description=description,
                location=request.form.get("location", "").strip() or None,
                occurred_at=occurred_at,
                reported_by=reported_by,
            )
            _apply_report_fields(incident, request.form)
            db.session.add(incident)
            db.session.flush()

            for f in request.files.getlist("photos"):
                if f and f.filename:
                    photo = incidents_svc.add_photo(
                        incident, f, uploaded_by=reported_by)
                    if photo is None:
                        flash(f"Skipped photo '{f.filename}': "
                              "unsupported file type", "warn")
            db.session.commit()
            flash("Incident report submitted", "success")
            return redirect(url_for("incident_detail", incident_id=incident.id))

        preselect = request.args.get("vehicle", "").strip()
        try:
            preselect_id = int(preselect)
        except ValueError:
            preselect_id = None
        return render_template(
            "incident_new.html",
            vehicles=Vehicle.query.order_by(Vehicle.unit_number).all(),
            employees=Employee.query.filter_by(active=True)
            .order_by(Employee.name).all(),
            issue_types=ISSUE_TYPES, severities=SEVERITIES,
            preselect_id=preselect_id,
            now_iso=datetime.now().strftime("%Y-%m-%dT%H:%M"))

    @app.route("/incidents/<int:incident_id>")
    def incident_detail(incident_id):
        incident = IncidentReport.query.get_or_404(incident_id)
        return render_template(
            "incident_detail.html", incident=incident,
            issue_types=ISSUE_TYPES, severities=SEVERITIES, statuses=STATUSES,
            employees=Employee.query.filter_by(active=True)
            .order_by(Employee.name).all())

    @app.route("/incidents/<int:incident_id>/edit", methods=["POST"])
    def incident_edit(incident_id):
        incident = IncidentReport.query.get_or_404(incident_id)
        prev_status = incident.status
        issue_type = request.form.get("issue_type", "").strip()
        if issue_type in ISSUE_TYPES:
            incident.issue_type = issue_type
        severity = request.form.get("severity", "").strip()
        if severity in SEVERITIES:
            incident.severity = severity
        status = request.form.get("status", "").strip()
        if status in STATUSES and status != prev_status:
            incident.status = status
            if status == "Resolved":
                incident.resolved_at = incident.resolved_at or datetime.utcnow()
            else:
                incident.resolved_at = None
        location = request.form.get("location", "").strip()
        incident.location = location or None
        description = request.form.get("description", "").strip()
        if description:
            incident.description = description
        occurred_raw = request.form.get("occurred_at", "").strip()
        if occurred_raw:
            try:
                incident.occurred_at = datetime.fromisoformat(occurred_raw)
            except ValueError:
                pass
        assigned_raw = request.form.get("assigned_to", "").strip()
        if assigned_raw:
            try:
                incident.assigned_to = int(assigned_raw)
            except ValueError:
                pass
        elif "assigned_to" in request.form:
            incident.assigned_to = None
        _apply_report_fields(incident, request.form)
        db.session.commit()
        flash("Incident updated", "success")
        return redirect(url_for("incident_detail", incident_id=incident.id))

    @app.route("/incidents/<int:incident_id>/note", methods=["POST"])
    def incident_note(incident_id):
        incident = IncidentReport.query.get_or_404(incident_id)
        note = incidents_svc.add_note(
            incident, request.form.get("text", ""),
            employee_id=_incident_actor_id())
        db.session.commit()
        flash("Note added", "success") if note else flash(
            "Note cannot be empty", "error")
        return redirect(url_for("incident_detail", incident_id=incident.id))

    @app.route("/incidents/<int:incident_id>/photos", methods=["POST"])
    def incident_photo_upload(incident_id):
        incident = IncidentReport.query.get_or_404(incident_id)
        actor = _incident_actor_id()
        added = 0
        skipped = 0
        for f in request.files.getlist("photos"):
            if not f or not f.filename:
                continue
            photo = incidents_svc.add_photo(incident, f, uploaded_by=actor)
            if photo is None:
                skipped += 1
            else:
                added += 1
        db.session.commit()
        if added:
            flash(f"{added} photo(s) added", "success")
        else:
            flash("No new photos added", "warn")
        if skipped:
            flash(f"{skipped} photo(s) skipped: unsupported file type", "warn")
        return redirect(url_for("incident_detail", incident_id=incident.id))

    @app.route("/incidents/<int:incident_id>/resolve", methods=["POST"])
    def incident_resolve(incident_id):
        incident = IncidentReport.query.get_or_404(incident_id)
        incident.status = "Resolved"
        incident.resolution_notes = request.form.get(
            "resolution_notes", "").strip() or None
        incident.resolved_at = datetime.utcnow()
        db.session.commit()
        flash("Incident marked as resolved", "success")
        return redirect(url_for("incident_detail", incident_id=incident.id))

    @app.route("/incidents/photo/<int:photo_id>")
    def incident_photo(photo_id):
        photo = IncidentPhoto.query.get_or_404(photo_id)
        if not os.path.isfile(photo.file_path):
            abort(404)
        return send_file(photo.file_path)

    @app.route("/incidents/photo/<int:photo_id>/delete", methods=["POST"])
    def incident_photo_delete(photo_id):
        photo = IncidentPhoto.query.get_or_404(photo_id)
        incident_id = photo.incident_id
        if photo.file_path and os.path.isfile(photo.file_path):
            try:
                os.remove(photo.file_path)
            except OSError:
                pass
        db.session.delete(photo)
        db.session.commit()
        flash("Photo removed", "success")
        return redirect(url_for("incident_detail", incident_id=incident_id))

    @app.route("/incidents/<int:incident_id>/pdf")
    def incident_pdf(incident_id):
        """Download the incident filled onto the ECHO Accident/Incident Report."""
        import io
        from .services.incident_report_pdf import render_incident_pdf
        incident = IncidentReport.query.get_or_404(incident_id)
        data = render_incident_pdf(incident)
        unit = incident.vehicle.unit_number or "vehicle"
        filename = f"incident_{incident.id}_{unit}_report.pdf"
        return send_file(io.BytesIO(data), mimetype="application/pdf",
                         as_attachment=True, download_name=filename)

    def _import_date_options():
        """Build date options for import: today, tomorrow, +2 days."""
        from datetime import timedelta
        labels = ["Today", "Tomorrow", "+2 Days"]
        return [
            {"iso": (date.today() + timedelta(days=i)).isoformat(),
             "label": labels[i],
             "display": (date.today() + timedelta(days=i)).strftime("%b %d"),
             "is_today": i == 0}
            for i in range(3)
        ]

    return app
