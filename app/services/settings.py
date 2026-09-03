"""Settings helpers: configurable thresholds and defaults."""
from app.models import Setting, db


DEFAULTS = {
    "recent_days": "2",       # recently washed if last_washed within this many days
    "due_soon_days": "7",     # due soon if last_washed older than recent but under this
    "location": None,         # default location name for schedules/vehicles
    "checklist": "Sweep,Mop,Windows,Seats,Bathroom,Dump,Bay Checked,Final Inspection",
}


def get_setting(key, default=None):
    s = Setting.query.get(key)
    if s is not None:
        return s.value
    return DEFAULTS.get(key, default)


def set_setting(key, value):
    s = Setting.query.get(key)
    if s is None:
        s = Setting(key=key, value=str(value))
        db.session.add(s)
    else:
        s.value = str(value)
    db.session.commit()


def get_checklist():
    raw = get_setting("checklist")
    if not raw:
        return ["Sweep", "Mop", "Windows", "Seats", "Bathroom", "Dump",
                "Bay Checked", "Final Inspection"]
    return [x.strip() for x in raw.split(",") if x.strip()]


def get_type_checklist(vehicle_type):
    """Return the comma-split checklist for a vehicle type, or the global
    default checklist when the type has none set."""
    if vehicle_type is not None and vehicle_type.checklist:
        return [x.strip() for x in vehicle_type.checklist.split(",")
                if x.strip()]
    return get_checklist()


def default_location():
    name = get_setting("location")
    if not name:
        return "Main Depot"
    return name
