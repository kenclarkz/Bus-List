"""Database models for the Detailing Operations Dashboard.

Design notes:
- SQLite is used for a self-contained persistent database. Models are written
  schema-agnostically so a different backend (Postgres/MySQL) can be swapped in
  later without application changes.
- The design supports multiple employees and locations: Location is a first
  class model, Employees have a location, and every daily schedule/vehicle is
  associated with a location.
- Historical data (service history, checklists, replacements, imports, days)
  is never deleted when a fresh prep report is imported.
"""

from datetime import datetime, date

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Location(db.Model):
    __tablename__ = "locations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    vehicles = db.relationship("Vehicle", back_populates="location")
    employees = db.relationship("Employee", back_populates="location")


class VehicleType(db.Model):
    __tablename__ = "vehicle_types"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    cleaning_frequency_days = db.Column(db.Integer, nullable=False, default=7)
    checklist = db.Column(db.Text)  # comma-separated; empty/None => global default
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    vehicles = db.relationship("Vehicle", back_populates="vehicle_type")


class Vehicle(db.Model):
    __tablename__ = "vehicles"

    id = db.Column(db.Integer, primary_key=True)
    unit_number = db.Column(db.String(40), nullable=False)
    vehicle_type_id = db.Column(db.Integer, db.ForeignKey("vehicle_types.id"))
    route = db.Column(db.String(120))
    status = db.Column(db.String(60))
    physical_location = db.Column(db.String(80))
    description = db.Column(db.String(200))
    make = db.Column(db.String(100))
    model = db.Column(db.String(100))
    capacity = db.Column(db.Integer, default=0)
    last_washed = db.Column(db.DateTime)
    last_detailed = db.Column(db.DateTime)
    last_dumped = db.Column(db.DateTime)
    cleanings_since_dump = db.Column(db.Integer, default=0)
    cleaning_frequency = db.Column(db.Integer, default=7)
    notes = db.Column(db.Text)
    active = db.Column(db.Boolean, default=True)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vehicle_type = db.relationship("VehicleType", back_populates="vehicles")
    location = db.relationship("Location", back_populates="vehicles",
                               foreign_keys=[location_id])

    @property
    def has_dump_task(self):
        vtype = self.vehicle_type
        if vtype is not None and vtype.checklist:
            tasks = [x.strip() for x in vtype.checklist.split(",") if x.strip()]
        else:
            from app.services.settings import get_checklist
            tasks = get_checklist()
        return "Dump" in tasks

    @property
    def needs_dump(self):
        if not self.has_dump_task:
            return False
        return (self.cleanings_since_dump or 0) >= 2

    service_history = db.relationship(
        "ServiceRecord", back_populates="vehicle",
        cascade="all, delete-orphan", order_by="ServiceRecord.service_date.desc()"
    )
    replacements_as_original = db.relationship(
        "Replacement", back_populates="original_vehicle",
        foreign_keys="Replacement.original_vehicle_id"
    )
    replacements_as_replacement = db.relationship(
        "Replacement", back_populates="replacement_vehicle",
        foreign_keys="Replacement.replacement_vehicle_id"
    )


class Employee(db.Model):
    __tablename__ = "employees"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"))
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    current_vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"))

    location = db.relationship("Location", back_populates="employees")
    completed_tasks = db.relationship("TaskCompletion", back_populates="employee")
    notes = db.relationship("Note", back_populates="employee")
    replacements = db.relationship(
        "Replacement", back_populates="employee",
        foreign_keys="Replacement.employee_id"
    )
    current_vehicle = db.relationship("Vehicle", foreign_keys=[current_vehicle_id])

    @property
    def initials(self):
        """Return uppercase initials from the employee name (e.g. 'John Smith' -> 'JS')."""
        parts = self.name.strip().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return self.name[:2].upper() if self.name else "?"


class DailySchedule(db.Model):
    """A day's work list. One per location per day."""
    __tablename__ = "daily_schedules"

    id = db.Column(db.Integer, primary_key=True)
    work_date = db.Column(db.Date, default=date.today, nullable=False, index=True)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"))
    finalized = db.Column(db.Boolean, default=False)
    finalized_at = db.Column(db.DateTime)
    summary = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    entries = db.relationship(
        "ScheduleEntry", back_populates="schedule",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        # A location may have only one schedule per day
        db.UniqueConstraint("work_date", "location_id", name="uq_schedule_day"),
    )


class ScheduleEntry(db.Model):
    """A vehicle on a given day's schedule."""
    __tablename__ = "schedule_entries"

    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey("daily_schedules.id"), nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    status = db.Column(db.String(40), default="pending")  # pending/in_progress/completed
    is_replacement = db.Column(db.Boolean, default=False)
    replacement_of_entry_id = db.Column(db.Integer)
    order_index = db.Column(db.Integer, default=0)
    prep_time = db.Column(db.String(40))
    skip_reason = db.Column(db.String(255))

    schedule = db.relationship("DailySchedule", back_populates="entries")
    vehicle = db.relationship("Vehicle")
    tasks = db.relationship(
        "TaskCompletion", back_populates="entry",
        cascade="all, delete-orphan"
    )


class TaskCompletion(db.Model):
    """One checklist item for a schedule entry, with timestamp and employee."""
    __tablename__ = "task_completions"

    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("schedule_entries.id"), nullable=False)
    task_name = db.Column(db.String(80), nullable=False)
    completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"))
    notes = db.Column(db.String(255))

    entry = db.relationship("ScheduleEntry", back_populates="tasks")
    employee = db.relationship("Employee", back_populates="completed_tasks")


class ServiceRecord(db.Model):
    """Permanent cleaning/service history for a vehicle."""
    __tablename__ = "service_records"

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    service_type = db.Column(db.String(60), default="prep")  # prep / wash / full detail
    service_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"))
    notes = db.Column(db.Text)
    source = db.Column(db.String(40), default="manual")  # manual / checklist / import

    vehicle = db.relationship("Vehicle", back_populates="service_history")
    employee = db.relationship("Employee")


class Replacement(db.Model):
    """A vehicle substitution (e.g. 142 -> 155)."""
    __tablename__ = "replacements"

    id = db.Column(db.Integer, primary_key=True)
    original_vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    replacement_vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    reason = db.Column(db.String(255))
    replaced_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"))
    source = db.Column(db.String(40), default="manual")  # manual / import

    original_vehicle = db.relationship(
        "Vehicle", back_populates="replacements_as_original",
        foreign_keys=[original_vehicle_id]
    )
    replacement_vehicle = db.relationship(
        "Vehicle", back_populates="replacements_as_replacement",
        foreign_keys=[replacement_vehicle_id]
    )
    employee = db.relationship("Employee", foreign_keys=[employee_id])


class PrepReportImport(db.Model):
    """A record of each prep report PDF processed."""
    __tablename__ = "prep_report_imports"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255))
    imported_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    applied = db.Column(db.Boolean, default=False)
    applied_at = db.Column(db.DateTime)
    schedule_date = db.Column(db.Date)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"))
    extraction_method = db.Column(db.String(40))  # text / table / ocr
    summary = db.Column(db.Text)
    preview_json = db.Column(db.Text)

    employee = db.relationship("Employee")


class Note(db.Model):
    """Free-form activity / daily notes."""
    __tablename__ = "notes"

    id = db.Column(db.Integer, primary_key=True)
    work_date = db.Column(db.Date, default=date.today, nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    schedule_id = db.Column(db.Integer, db.ForeignKey("daily_schedules.id"))

    employee = db.relationship("Employee")


class Setting(db.Model):
    """Key/value application settings (thresholds, defaults)."""
    __tablename__ = "settings"

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(255))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
