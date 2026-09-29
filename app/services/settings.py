"""Settings helpers: configurable thresholds and defaults."""
from app.models import Setting, db


DEFAULTS = {
    "recent_days": "2",       # recently washed if last_washed within this many days
    "due_soon_days": "7",     # due soon if last_washed older than recent but under this
    "location": None,         # default location name for schedules/vehicles
    "dark_mode": "off",       # global fallback theme: off | on | system | futuristic | halloween | bloomberg | retro | holographic | synthwave | cosmos | cyberpunk | aurora | ocean | crystal | matrix | dunes
    "layout": "classic",      # global fallback layout: classic | sidepanel
}

# The task list a vehicle type starts with, split into the work inside the
# vehicle and the work outside it. There is no checklist outside a vehicle
# type: this is what a newly created type (or one that has never been given a
# list of its own) is worked from, and a manager edits each type's own copy in
# Settings. The board shows a vehicle type's list exactly as it is typed here,
# in this order, for that type alone.
STANDARD_INSIDE_TASKS = ("Sweep", "Mop", "Windows", "Seats", "Bathroom")
STANDARD_OUTSIDE_TASKS = ("Dump", "Bay Checked", "Final Inspection")


def standard_categorized_checklist():
    """The Inside/Outside split a vehicle type starts from."""
    return {
        "inside": list(STANDARD_INSIDE_TASKS),
        "outside": list(STANDARD_OUTSIDE_TASKS),
    }


# Valid theme choices. Each user may store their own under a per-user key.
# "off" is the plain light palette, "on" is the plain dark palette and
# "system" follows the OS; everything else is a themed look defined in
# app/static/css/style.css. The 3D themes (synthwave, cosmos, cyberpunk,
# aurora, ocean, crystal, matrix, dunes) all have continuous background
# animation, so they honour prefers-reduced-motion.
THEME_CHOICES = ("off", "on", "system", "futuristic", "halloween", "bloomberg",
                 "retro", "holographic", "synthwave", "cosmos", "cyberpunk",
                 "aurora", "ocean", "crystal", "matrix", "dunes")

# Valid layout choices. Layout is how the site chrome is arranged (top bar vs
# sidebar); it is independent of the color theme. Each user may store their
# own under a per-user key. "classic" is the default layout.
LAYOUT_CHOICES = ("classic", "sidepanel")


def theme_key(user, employee_id=None):
    """DB key storing one user's own theme choice (per-account, and per
    employee when the shared employee account has picked a name)."""
    if user == "employee" and employee_id:
        return f"theme:employee:{employee_id}"
    return f"theme:{user or 'employee'}"


def get_user_theme(user, employee_id=None):
    """A user's own theme choice, falling back to the global default."""
    if user:
        own = get_setting(theme_key(user, employee_id))
        if own:
            return own
    return get_setting("dark_mode", "off") or "off"


def set_user_theme(user, employee_id=None, value="off"):
    """Persist a user's own theme choice. Ignored for unknown values."""
    if value not in THEME_CHOICES:
        return False
    set_setting(theme_key(user, employee_id), value)
    return True


def layout_key(user, employee_id=None):
    """DB key storing one user's own layout choice (per-account, and per
    employee when the shared employee account has picked a name)."""
    if user == "employee" and employee_id:
        return f"layout:employee:{employee_id}"
    return f"layout:{user or 'employee'}"


def get_user_layout(user, employee_id=None):
    """A user's own layout choice, falling back to the global default."""
    if user:
        own = get_setting(layout_key(user, employee_id))
        if own:
            return own
    return get_setting("layout", "classic") or "classic"


def set_user_layout(user, employee_id=None, value="classic"):
    """Persist a user's own layout choice. Ignored for unknown values."""
    if value not in LAYOUT_CHOICES:
        return False
    set_setting(layout_key(user, employee_id), value)
    return True


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


def task_category(task_name, categorized):
    """Which of a vehicle's two clock sets a task belongs to.

    A task and the clock set that opens it are always the same one, so the
    board asks this rather than working it out again wherever it needs to know.
    A task that is not on either list (a name left behind by an edited
    checklist) is treated as inside.

    ``categorized`` is the checklist of the vehicle's own type, as returned by
    :func:`get_type_categorized_checklist`: which side a task belongs to is a
    fact about that type's list, never a global one, so it is always passed in
    rather than guessed at.
    """
    if task_name in categorized["outside"]:
        return "outside"
    return "inside"


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
    """Return the flat checklist for a vehicle type, inside tasks first.

    A type that has never been given a list of its own is worked from
    :data:`STANDARD_INSIDE_TASKS` / :data:`STANDARD_OUTSIDE_TASKS`, so every
    vehicle on the board always has tasks to tick.
    """
    categorized = get_type_categorized_checklist(vehicle_type)
    return categorized["inside"] + categorized["outside"]


def get_type_categorized_checklist(vehicle_type):
    """Return a vehicle type's own 'inside' and 'outside' task lists, in the
    order they are typed.

    This is the single source of the checklist: the board, the end-of-day
    summary and the printed report all read a vehicle's list from here, so a
    type shows exactly what its own Inside and Outside lists say, in that
    order, and no other type's tasks ever appear on it.
    """
    if vehicle_type is not None and vehicle_type.checklist:
        return _parse_categorized_checklist(vehicle_type.checklist)
    return standard_categorized_checklist()


def standard_type_checklist():
    """The stored form of the standard Inside/Outside split, for a vehicle type
    created without one of its own."""
    return format_type_checklist_for_storage(
        STANDARD_INSIDE_TASKS, STANDARD_OUTSIDE_TASKS)


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
