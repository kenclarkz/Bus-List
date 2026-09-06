"""Application factory and route registration."""
import os
import json
from datetime import date, datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, \
    jsonify, session

from .models import db, Vehicle, Employee, ScheduleEntry, Replacement, Note, \
    DailySchedule, PrepReportImport
from .services import settings, vehicles, schedule as sched_svc

# The only three accounts. Passwords are the lowercase role name. No accounts
# can be created through the app.
ROLE_ACCOUNTS = {
    "employee": {"display": "Employee", "password": "employee"},
    "driver": {"display": "Driver", "password": "driver"},
    "manager": {"display": "Manager", "password": "manager"},
}


def role_home(role):
    """Default landing page for a signed-in role."""
    if role == "driver":
        return url_for("driver_dashboard")
    return url_for("dashboard")


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
        scols = {r[1] for r in con.execute("PRAGMA table_info(schedule_entries)")}
        if "skip_reason" not in scols:
            con.execute("ALTER TABLE schedule_entries ADD COLUMN skip_reason VARCHAR(255)")
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
    if Vehicle.query.count() == 0:
        _seed_vehicles(loc)


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
    rows = []
    for entry in sorted(sched.entries, key=_prep_time_sort_key):
        done, total, pct = sched_svc.entry_progress(entry)
        rows.append({
            "entry": entry,
            "vehicle": entry.vehicle,
            "done": done,
            "total": total,
            "pct": pct,
            "indicator": status_indicator(entry.vehicle.last_washed),
        })
    return rows


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

    @app.context_processor
    def inject_globals():
        user = session.get("user")
        return {
            "today": date.today,
            "checklist": settings.get_checklist(),
            "app_name": "Detailing Operations Dashboard",
            "current_role": user if user in ROLE_ACCOUNTS else "employee",
            "current_user": ROLE_ACCOUNTS.get(user, {}).get(
                "display") if user else None,
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
        # Drivers only see the finished-vehicles screen.
        if user == "driver" and request.endpoint != "driver_dashboard":
            return redirect(url_for("driver_dashboard"))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("user") in ROLE_ACCOUNTS:
            return redirect(role_home(session.get("user")))
        if request.method == "POST":
            username = (request.form.get("username") or "").strip().lower()
            password = request.form.get("password") or ""
            account = ROLE_ACCOUNTS.get(username)
            if account and password == account["password"]:
                session.clear()
                session["user"] = username
                session["username"] = account["display"]
                flash(f"Welcome, {account['display']}", "success")
                return redirect(role_home(username))
            flash("Invalid username or password", "error")
            return redirect(url_for("login"))
        return render_template("login.html")

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
        completed = sum(1 for r in rows if r["entry"].status in ("completed", "skipped"))
        in_progress = sum(1 for r in rows if r["entry"].status == "in_progress")
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

        # Build employee current vehicle map for active employees today
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
            remaining=remaining, overall=overall, overdue=overdue,
            replacements=replacements, types=types, filters=filters,
            employees=employees_list(),
            nav_dates=nav_dates, view_date=view_date,
            imported_dates=imported_dates,
            active_employees=active_employees,
        )

    @app.route("/driver")
    def driver_dashboard():
        """Driver screen: a read-only list of today's finished vehicles."""
        loc = vehicles.default_location()
        sched = sched_svc.get_or_create_schedule(d=date.today(), location=loc)
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
        return render_template("driver.html", rows=rows, total=len(rows))

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
            imp = vehicles.record_import(
                file.filename, applied=False, summary=summary,
                preview=preview, method=method)
            return render_template(
                "import_preview.html",
                preview=preview, warnings=warnings, method=method,
                import_id=imp.id,
                sched_date=sched_dt.isoformat())
        return render_template("import.html", import_dates=_import_date_options())

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
        if session.get("user") != "employee":
            flash("Manager view is read-only", "error")
            return redirect(url_for("dashboard"))
        entry = ScheduleEntry.query.get_or_404(entry_id)
        reason = request.form.get("reason", "").strip()
        if not reason:
            reason = entry.skip_reason or ""
        status = sched_svc.set_entry_skipped(entry, skipped=True, reason=reason)
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
        return redirect(request.referrer or url_for("dashboard"))

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
        sched = sched_svc.get_or_create_schedule(d=sched_dt, location=loc)
        order = (max((e.order_index for e in sched.entries), default=-1) + 1)
        entry = sched_svc.ensure_entry(
            sched, vehicle, order_index=order, prep_time=prep_time)
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
        completed = sum(1 for r in rows if r["entry"].status in ("completed", "skipped"))
        skipped = sum(1 for r in rows if r["entry"].status == "skipped")
        incomplete = total - completed
        overall = round((sum(r["done"] for r in rows) /
                        (sum(r["total"] for r in rows) or 1)) * 100) if rows else 0
        incomplete_rows = [r for r in rows if r["entry"].status not in ("completed", "skipped")]
        completed_rows = []
        for r in rows:
            if r["entry"].status not in ("completed", "skipped"):
                continue
            emp_tasks = {}
            for t in r["entry"].tasks:
                if t.completed and t.employee:
                    emp_tasks.setdefault(t.employee.name, []).append(t.task_name)
            completed_rows.append({**r, "employees": emp_tasks})

        if request.method == "POST" and request.form.get("confirm") == "yes":
            sched.finalized = True
            sched.finalized_at = datetime.utcnow()
            sched.summary = json.dumps(dict(
                total=total, completed=completed, incomplete=incomplete,
                overall=overall))
            db.session.commit()
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
        completed = sum(1 for r in rows if r["entry"].status in ("completed", "skipped"))
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

    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        from .models import VehicleType
        from .services.schedule import refresh_type_entries
        if request.method == "POST":
            for key in ["recent_days", "due_soon_days", "location"]:
                val = request.form.get(key)
                if val is not None:
                    settings.set_setting(key, val)
            checklist = request.form.get("checklist", "")
            if checklist:
                settings.set_setting("checklist", checklist)
            # Per-vehicle-type checklists. A type uses the global default
            # unless its own checklist field is submitted and non-empty.
            for vt in VehicleType.query.all():
                val = request.form.get(f"type_checklist_{vt.id}")
                if val is not None:
                    val = val.strip()
                    if vt.checklist != (val or None):
                        vt.checklist = val or None
                        db.session.commit()
                        refresh_type_entries(vt)
            db.session.commit()
            flash("Settings saved", "success")
            return redirect(url_for("settings_page"))
        vtypes = VehicleType.query.order_by(VehicleType.name).all()
        return render_template("settings.html", settings={
            "recent_days": settings.get_setting("recent_days", 2),
            "due_soon_days": settings.get_setting("due_soon_days", 7),
            "location": settings.get_setting("location") or "Main Depot",
            "checklist": ", ".join(settings.get_checklist()),
        }, vehicle_types=vtypes)

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
