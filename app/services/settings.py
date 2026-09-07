"""Settings helpers: configurable thresholds and defaults."""
from app.models import Setting, db


DEFAULTS = {
    "recent_days": "2",       # recently washed if last_washed within this many days
    "due_soon_days": "7",     # due soon if last_washed older than recent but under this
    "location": None,         # default location name for schedules/vehicles
    "checklist": "Sweep,Mop,Windows,Seats,Bathroom,Dump,Bay Checked,Final Inspection",
    "checklist_inside": "Sweep,Mop,Windows,Seats,Bathroom",
    "checklist_outside": "Dump,Bay Checked,Final Inspection",
    "dark_mode": "off",       # off | on | system
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
    """Return the combined flat list of all tasks (inside + outside)."""
    inside = get_checklist_inside()
    outside = get_checklist_outside()
    return inside + outside


def get_checklist_inside():
    """Return the list of Inside tasks."""
    raw = get_setting("checklist_inside")
    if not raw:
        return ["Sweep", "Mop", "Windows", "Seats", "Bathroom"]
    return [x.strip() for x in raw.split(",") if x.strip()]


def get_checklist_outside():
    """Return the list of Outside tasks."""
    raw = get_setting("checklist_outside")
    if not raw:
        return ["Dump", "Bay Checked", "Final Inspection"]
    return [x.strip() for x in raw.split(",") if x.strip()]


def get_categorized_checklist():
    """Return a dict with 'inside' and 'outside' task lists."""
    return {
        "inside": get_checklist_inside(),
        "outside": get_checklist_outside(),
    }


def _parse_categorized_checklist(raw):
    """Parse a comma-separated checklist string that may use 'Inside:' and
    'Outside:' prefixes. Returns dict with 'inside' and 'outside' keys.

    Format: "Inside: Sweep,Mop | Outside: Dump,Bay Checked"
    Or legacy flat: "Sweep,Mop,Dump" -> all go to 'inside' for backward compat.
    """
    result = {"inside": [], "outside": []}
    if not raw:
        return result
    raw = raw.strip()
    if "Inside:" in raw or "Outside:" in raw:
        import re
        inside_match = re.search(r"Inside:\s*([^|]*)", raw)
        outside_match = re.search(r"Outside:\s*([^|]*)", raw)
        if inside_match:
            result["inside"] = [x.strip() for x in inside_match.group(1).split(",") if x.strip()]
        if outside_match:
            result["outside"] = [x.strip() for x in outside_match.group(1).split(",") if x.strip()]
    else:
        result["inside"] = [x.strip() for x in raw.split(",") if x.strip()]
    return result


def get_type_checklist(vehicle_type):
    """Return the flat comma-split checklist for a vehicle type, or the global
    default checklist when the type has none set."""
    if vehicle_type is not None and vehicle_type.checklist:
        categorized = _parse_categorized_checklist(vehicle_type.checklist)
        return categorized["inside"] + categorized["outside"]
    return get_checklist()


def get_type_categorized_checklist(vehicle_type):
    """Return a dict with 'inside' and 'outside' task lists for a vehicle type,
    falling back to the global categorized default when the type has none set."""
    if vehicle_type is not None and vehicle_type.checklist:
        return _parse_categorized_checklist(vehicle_type.checklist)
    return get_categorized_checklist()


def format_type_checklist_for_storage(inside_tasks, outside_tasks):
    """Format categorized tasks into a comma-separated string for VehicleType.checklist."""
    parts = []
    if inside_tasks:
        parts.append("Inside: " + ", ".join(inside_tasks))
    if outside_tasks:
        parts.append("Outside: " + ", ".join(outside_tasks))
    return " | ".join(parts)


def default_location():
    name = get_setting("location")
    if not name:
        return "Main Depot"
    return name
