"""Application factory and route registration."""
import os
import json
from datetime import date, datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

from .models import db, Vehicle, Employee, ScheduleEntry, Replacement, Note, \
    DailySchedule, PrepReportImport
from .services import settings, vehicles, schedule as sched_svc


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
        seed_defaults()

    register_routes(app)
    return app


def seed_defaults():
    from .models import Location, Employee, VehicleType
    loc = Location.query.filter_by(name="Main Depot").first()
    if not loc:
        loc = Location(name="Main Depot")
        db.session.add(loc)
        db.session.commit()
    if Employee.query.count() == 0:
        db.session.add(Employee(name="User", location_id=loc.id, active=True))
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


def build_schedule_view(sched):
    rows = []
    for entry in sorted(sched.entries, key=lambda e: e.order_index):
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
        return {
            "today": date.today,
            "checklist": settings.get_checklist(),
            "app_name": "Detailing Operations Dashboard",
        }

    @app.route("/")
    def dashboard():
        loc = vehicles.default_location()
        sched = sched_svc.get_or_create_schedule(location=loc)
        rows = build_schedule_view(sched)

        total = len(rows)
        completed = sum(1 for r in rows if r["entry"].status == "completed")
        in_progress = sum(1 for r in rows if r["entry"].status == "in_progress")
        remaining = total - completed - in_progress
        overall = round((sum(r["done"] for r in rows) /
                        (sum(r["total"] for r in rows) or 1)) * 100) if rows else 0
        overdue = sum(1 for r in rows if r["indicator"][0] == "Overdue")
        replacements = replacement_count_for_date(sched.work_date)

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
        return render_template(
            "dashboard.html",
            rows=frows, all_rows=rows, sched=sched,
            total=total, completed=completed, in_progress=in_progress,
            remaining=remaining, overall=overall, overdue=overdue,
            replacements=replacements, types=types, filters=filters,
            employees=employees_list(),
        )

    @app.route("/vehicles")
    def vehicle_list():
        loc = vehicles.default_location()
        vq = Vehicle.query.filter_by(location_id=loc.id).filter(
            Vehicle.active.is_(True)).all()
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
                sched_date=date.today().isoformat())
        return render_template("import.html")

    @app.route("/import/<int:import_id>/apply", methods=["POST"])
    def import_apply(import_id):
        imp = PrepReportImport.query.get_or_404(import_id)
        preview = json.loads(imp.preview_json)
        sched = sched_svc.apply_import(
            preview,
            location=vehicles.default_location(),
            employee_id=request.form.get("employee_id") or None,
            source="import")
        imp.applied = True
        imp.applied_at = datetime.utcnow()
        db.session.commit()
        flash("Prep report applied. Today's work list updated.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/task/<int:entry_id>/<path:task_name>", methods=["POST"])
    def task_toggle(entry_id, task_name):
        checked = request.form.get("checked") == "true"
        emp = request.form.get("employee_id") or None
        task = sched_svc.toggle_task(entry_id, task_name, checked, emp)
        done = total = pct = None
        if task:
            done, total, pct = sched_svc.entry_progress(task.entry)
        return jsonify(ok=True, done=done, total=total, pct=pct)

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
        completed = sum(1 for r in rows if r["entry"].status == "completed")
        incomplete = total - completed
        overall = round((sum(r["done"] for r in rows) /
                        (sum(r["total"] for r in rows) or 1)) * 100) if rows else 0
        incomplete_rows = [r for r in rows if r["entry"].status != "completed"]
        completed_rows = []
        for r in rows:
            if r["entry"].status != "completed":
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

        return render_template(
            "end_day.html", rows=rows, sched=sched, notes=notes,
            replacements=replacements, d=d,
            total=total, completed=completed, incomplete=incomplete,
            overall=overall, incomplete_rows=incomplete_rows,
            completed_rows=completed_rows,
            finalized=sched.finalized, employees=employees_list())

    @app.route("/print/<path:date>")
    def print_report(date):
        d = datetime.strptime(date, "%Y-%m-%d").date()
        loc = vehicles.default_location()
        sched = sched_svc.get_or_create_schedule(d, loc)
        rows = build_schedule_view(sched)
        notes = notes_for_date(d)
        replacements = replacement_count_for_date(d)
        total = len(rows)
        completed = sum(1 for r in rows if r["entry"].status == "completed")
        overall = round((sum(r["done"] for r in rows) /
                        (sum(r["total"] for r in rows) or 1)) * 100) if rows else 0
        return render_template(
            "print_report.html", rows=rows, notes=notes, d=d, sched=sched,
            replacements=replacements, total=total, completed=completed,
            overall=overall)

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
        if request.method == "POST":
            for key in ["recent_days", "due_soon_days", "location"]:
                val = request.form.get(key)
                if val is not None:
                    settings.set_setting(key, val)
            checklist = request.form.get("checklist", "")
            if checklist:
                settings.set_setting("checklist", checklist)
            flash("Settings saved", "success")
            return redirect(url_for("settings_page"))
        return render_template("settings.html", settings={
            "recent_days": settings.get_setting("recent_days", 2),
            "due_soon_days": settings.get_setting("due_soon_days", 7),
            "location": settings.get_setting("location") or "Main Depot",
            "checklist": ", ".join(settings.get_checklist()),
        })

    return app
