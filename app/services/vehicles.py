"""Vehicle and entity helpers."""
import json
import os
import re
from datetime import datetime

from app.models import (
    db, Vehicle, VehicleType, Location, Employee,
    ServiceRecord, Replacement, PrepReportImport,
)
from app.services import settings

# Vehicle-type codes (as they appear on ECHO reports) that belong to the
# transit fleet. Transit buses are washed by their own crew, so anything with
# one of these types is skipped instead of being handed to the detail bay.
TRANSIT_TYPE_CODES = {"transitb", "transitbus", "transit"}

# Reason recorded on transit entries that the importer auto-skipped.
TRANSIT_SKIP_REASON = "Transit — auto-skipped on import"


def is_transit_type(name):
    """True when a vehicle-type name from a report denotes a transit bus.

    Matching ignores case, spaces, dashes and underscores so 'TRANSITB',
    'TRANSIT BUS' and 'transit_bus' all count. Only the vehicle *type* is
    considered: a Ford Transit van (type 'Van.') is not a transit bus.
    """
    if not name:
        return False
    return re.sub(r"[^a-z0-9]", "", str(name).lower()) in TRANSIT_TYPE_CODES


def is_transit_vehicle(vehicle):
    """True when the vehicle's stored type is a transit type."""
    if vehicle is None:
        return False
    vtype = getattr(vehicle, "vehicle_type", None)
    return is_transit_type(vtype.name if vtype else None)


class Savable:
    def save(self):
        db.session.commit()


def get_or_create_location(name):
    if not name:
        name = settings.default_location()
    loc = Location.query.filter_by(name=name).first()
    if not loc:
        loc = Location(name=name)
        db.session.add(loc)
        db.session.commit()
    return loc


def default_location():
    return get_or_create_location(settings.default_location())


def get_or_create_employee(name):
    return name


def get_or_create_vehicle_type(name):
    if not name:
        return None
    vt = VehicleType.query.filter_by(name=name).first()
    if not vt:
        vt = VehicleType(name=name, cleaning_frequency_days=settings.get_setting(
            "default_frequency", 7))
        db.session.add(vt)
        db.session.commit()
    return vt


def find_vehicle_by_unit(unit):
    if unit is None:
        return None
    return Vehicle.query.filter(
        db.func.lower(Vehicle.unit_number) == str(unit).lower()
    ).filter(
        Vehicle.active.is_(True)
    ).first()


def find_or_create_vehicle(unit, vehicle_type=None, route=None, notes=None,
                           location_id=None):
    """Return (vehicle, is_new)."""
    vehicle = find_vehicle_by_unit(unit)
    if vehicle:
        return vehicle, False

    vt = get_or_create_vehicle_type(vehicle_type)
    vehicle = Vehicle(
        unit_number=unit,
        vehicle_type_id=vt.id if vt else None,
        route=route,
        cleaning_frequency=vt.cleaning_frequency_days if vt else settings.get_setting(
            "default_frequency", 7),
        notes=notes,
        active=True,
        location_id=location_id or default_location().id,
    )
    db.session.add(vehicle)
    db.session.commit()
    return vehicle, True


def add_service_record(vehicle, service_type="prep", employee_id=None, notes=None,
                       source="manual", at=None):
    if service_type == "wash":
        vehicle.last_washed = at or datetime.utcnow()
    if service_type == "prep":
        vehicle.last_detailed = at or datetime.utcnow()
    if service_type == "dump":
        vehicle.last_dumped = at or datetime.utcnow()
        vehicle.cleanings_since_dump = 0
    rec = ServiceRecord(
        vehicle_id=vehicle.id,
        service_type=service_type,
        service_date=at or datetime.utcnow(),
        employee_id=employee_id,
        notes=notes,
        source=source,
    )
    db.session.add(rec)
    db.session.commit()
    return rec


def record_import(filename, applied, summary, preview, employee_id=None,
                  method=None, file_path=None):
    imp = PrepReportImport(
        filename=filename,
        file_path=file_path,
        applied=applied,
        applied_at=datetime.utcnow() if applied else None,
        employee_id=employee_id,
        extraction_method=method,
        summary=summary,
        preview_json=json.dumps(preview),
    )
    db.session.add(imp)
    db.session.commit()
    return imp


def remove_import(imp):
    """Remove a prep report import along with the information it introduced.

    - Newly created vehicles (from preview 'new') are deleted together with
      their schedule entries, tasks, and service history.
    - Vehicles the import deactivated (preview 'removed') are reactivated.
    - The import record itself is deleted.
    - If no applied import remains for that schedule's day, that day's work
      list is cleared so the Today board resets to empty.
    """
    from app.models import ScheduleEntry, DailySchedule

    preview = json.loads(imp.preview_json or "{}")
    units_new = [item.get("unit") for item in preview.get("new", [])
                 if item.get("unit")]
    units_removed = [item.get("unit") for item in preview.get("removed", [])
                     if item.get("unit")]

    for unit in units_new:
        vehicle = Vehicle.query.filter(
            db.func.lower(Vehicle.unit_number) == str(unit).lower()
        ).first()
        if vehicle:
            # Detach any replacements that reference this vehicle so the
            # vehicle delete doesn't trip the NOT NULL foreign keys on the
            # replacements rows (which must name both an original and a
            # replacement vehicle).
            Replacement.query.filter(db.or_(
                Replacement.original_vehicle_id == vehicle.id,
                Replacement.replacement_vehicle_id == vehicle.id,
            )).delete(synchronize_session=False)
            for entry in ScheduleEntry.query.filter_by(
                    vehicle_id=vehicle.id).all():
                db.session.delete(entry)
            Employee.query.filter_by(current_vehicle_id=vehicle.id).update(
                {"current_vehicle_id": None})
            db.session.delete(vehicle)

    for unit in units_removed:
        vehicle = Vehicle.query.filter(
            db.func.lower(Vehicle.unit_number) == str(unit).lower()
        ).first()
        if vehicle:
            vehicle.active = True

    sched_date = imp.schedule_date
    if imp.file_path and os.path.isfile(imp.file_path):
        try:
            os.remove(imp.file_path)
        except OSError:
            pass
    db.session.delete(imp)
    db.session.flush()

    if sched_date is not None:
        remaining = PrepReportImport.query.filter_by(
            applied=True, schedule_date=sched_date).count()
        if remaining == 0:
            for sched in DailySchedule.query.filter_by(work_date=sched_date).all():
                # Clear current vehicle for employees working vehicles on
                # this day's board, since the board is being reset to empty.
                removed_ids = [e.vehicle_id for e in sched.entries]
                if removed_ids:
                    Employee.query.filter(
                        Employee.current_vehicle_id.in_(removed_ids)
                    ).update({"current_vehicle_id": None},
                             synchronize_session=False)
                for entry in list(sched.entries):
                    db.session.delete(entry)

    db.session.commit()

    return len(units_new)
