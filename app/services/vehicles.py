"""Vehicle and entity helpers."""
import json
from datetime import datetime

from app.models import (
    db, Vehicle, VehicleType, Location, Employee,
    ServiceRecord, Replacement, PrepReportImport,
)
from app.services import settings


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
                  method=None):
    imp = PrepReportImport(
        filename=filename,
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
