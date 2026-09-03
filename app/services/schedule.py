"""Daily schedule / detailing board logic."""
import re
from datetime import date, datetime

from app.models import (
    db, DailySchedule, ScheduleEntry, TaskCompletion, Vehicle,
    Replacement, Note, Setting,
)
from app.services import settings, vehicles


def today():
    return date.today()


def get_or_create_schedule(d=None, location=None):
    d = d or today()
    loc = location or vehicles.default_location()
    sched = DailySchedule.query.filter_by(work_date=d, location_id=loc.id).first()
    if not sched:
        sched = DailySchedule(work_date=d, location_id=loc.id)
        db.session.add(sched)
        db.session.commit()
    return sched


def ensure_entry(sched, vehicle, order_index=0, prep_time=None):
    entry = ScheduleEntry.query.filter_by(schedule_id=sched.id,
                                          vehicle_id=vehicle.id).first()
    if not entry:
        entry = ScheduleEntry(
            schedule_id=sched.id,
            vehicle_id=vehicle.id,
            status="pending",
            order_index=order_index,
            prep_time=prep_time,
        )
        db.session.add(entry)
        db.session.flush()
        create_task_rows(entry)
        db.session.commit()
    elif prep_time and entry.prep_time != prep_time:
        entry.prep_time = prep_time
        db.session.commit()
    return entry


def create_task_rows(entry):
    for tname in settings.get_checklist():
        if not any(t.task_name == tname for t in entry.tasks):
            entry.tasks.append(TaskCompletion(
                entry_id=entry.id,
                task_name=tname,
                completed=False,
            ))


def entry_progress(entry):
    tasks = entry.tasks
    if not tasks:
        return 0, 0, 0.0
    done = sum(1 for t in tasks if t.completed)
    total = len(tasks)
    pct = round(done / total * 100) if total else 0
    return done, total, pct


def update_entry_status(entry):
    done, total, pct = entry_progress(entry)
    if total and done == total:
        entry.status = "completed"
    elif done > 0:
        entry.status = "in_progress"
    else:
        entry.status = "pending"
    db.session.commit()
    return entry.status


def toggle_task(entry_id, task_name, checked, employee_id=None):
    entry = ScheduleEntry.query.get(entry_id)
    if not entry:
        return None
    task = next((t for t in entry.tasks if t.task_name == task_name), None)
    if not task:
        task = TaskCompletion(entry_id=entry.id, task_name=task_name)
        entry.tasks.append(task)
    task.completed = bool(checked)
    task.completed_at = datetime.utcnow() if checked else None
    task.employee_id = employee_id if checked else None
    db.session.commit()
    # Record last washed / detailed in history when appropriate
    if checked:
        if task_name.lower() == "sweep":
            vehicles.add_service_record(
                entry.vehicle, service_type="wash",
                employee_id=employee_id, source="checklist",
                at=datetime.utcnow())
        if task_name.lower() == "final inspection":
            vehicles.add_service_record(
                entry.vehicle, service_type="prep",
                employee_id=employee_id, source="checklist",
                at=datetime.utcnow())
    update_entry_status(entry)
    return task


# ---------------------------------------------------------------------------
# Replacements
# ---------------------------------------------------------------------------

def record_replacement(original, replacement, reason="", employee_id=None,
                       source="manual"):
    rep = Replacement(
        original_vehicle_id=original.id,
        replacement_vehicle_id=replacement.id,
        reason=reason,
        replaced_at=datetime.utcnow(),
        employee_id=employee_id,
        source=source,
    )
    db.session.add(rep)
    db.session.commit()
    return rep


def move_entry_to_replacement(sched, original_entry, replacement_vehicle,
                              reason="", employee_id=None):
    """Mirror the original's remaining task state onto the replacement.

    Completed tasks are preserved on the original (historical); only pending
    tasks are carried forward so the replacement starts where the original
    left off.
    """
    replacement_entry = ScheduleEntry.query.filter_by(
        schedule_id=sched.id, vehicle_id=replacement_vehicle.id).first()
    if not replacement_entry:
        replacement_entry = ScheduleEntry(
            schedule_id=sched.id,
            vehicle_id=replacement_vehicle.id,
            status="pending",
            order_index=original_entry.order_index,
            is_replacement=True,
            replacement_of_entry_id=original_entry.id,
        )
        db.session.add(replacement_entry)
        db.session.flush()
        create_task_rows(replacement_entry)
    else:
        replacement_entry.is_replacement = True
        replacement_entry.replacement_of_entry_id = original_entry.id

    # Copy over completed tasks timing/employee so progress is preserved
    original_done = [t for t in original_entry.tasks if t.completed]
    for t in original_done:
        rt = next((x for x in replacement_entry.tasks if x.task_name == t.task_name), None)
        if rt and not rt.completed:
            rt.completed = True
            rt.completed_at = t.completed_at
            rt.employee_id = t.employee_id

    # Record the replacement
    record_replacement(original_entry.vehicle, replacement_vehicle, reason,
                       employee_id)

    db.session.commit()
    update_entry_status(replacement_entry)
    return replacement_entry


def destroy_and_recreate_tasks(entry):
    """Recreate the checklist rows for an entry (keeps completed state)."""
    existing = {t.task_name: t for t in entry.tasks}
    new_names = settings.get_checklist()
    for task in list(entry.tasks):
        if task.task_name not in existing:
            continue
        if task.task_name not in new_names:
            db.session.delete(task)
    for name in new_names:
        if name not in existing:
            entry.tasks.append(TaskCompletion(
                entry_id=entry.id, task_name=name, completed=False))
    db.session.commit()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def build_preview(parsed, location=None):
    """Compare parsed report against DB and produce a preview dict.

    Returns dict with 'new', 'updated', 'removed', 'route_changes',
    'replacements', 'uncertain', 'unchanged'.
    """
    preview = {
        "new": [],
        "updated": [],
        "removed": [],
        "route_changes": [],
        "replacements": [],
        "uncertain": [],
        "unchanged": [],
        "count": 0,
    }
    db_units = {}
    q = Vehicle.query.filter_by(active=True)
    if location:
        q = q.filter_by(location_id=location.id)
    for v in q:
        db_units[v.unit_number.lower()] = v

    seen = set()
    prev_units = set(db_units.keys())
    parsed_units = set(parsed.keys())

    for u in parsed:
        p = parsed[u]
        key = p.unit.lower()
        seen.add(key)
        existing = db_units.get(key)
        v = {"unit": p.unit, "type": p.type, "route": p.route, "raw": p.raw,
             "prep_time": p.prep_time}
        if p.uncertain:
            preview["uncertain"].append(v)

        # substitution hint: "Replace 155" style route
        if p.route and p.route.lower().startswith("replace"):
            m = re.search(r"(?i)replace[\s:-]*(\d{2,6})", p.route)
            if m:
                preview["replacements"].append({
                    "original": p.unit,
                    "replacement": m.group(1),
                    "raw": p.raw,
                })

        if not existing:
            preview["new"].append(v)
        else:
            changes = []
            if p.type and existing.vehicle_type and \
               existing.vehicle_type.name.lower() != p.type.lower():
                changes.append("type")
            if p.route and existing.route and \
               existing.route.lower() != p.route.lower():
                changes.append("route")
            preview["updated" if changes else "unchanged"].append(v)

    # removed = in DB but not in today's report
    for du in prev_units:
        if du not in seen:
            preview["removed"].append({"unit": db_units[du].unit_number,
                                       "route": db_units[du].route})

    preview["count"] = len(seen)
    return preview


def apply_import(preview, location=None, employee_id=None, source="import",
                 schedule_date=None):
    """Apply a preview: create vehicles, update routes, build today's schedule,
    handle replacements, deactivate removed vehicles. Never deletes history."""
    loc = location or vehicles.default_location()
    sched = get_or_create_schedule(schedule_date, loc)
    position = 0

    for item in preview["new"] + preview["updated"] + preview["unchanged"]:
        unit = item["unit"]
        vehicle, _ = vehicles.find_or_create_vehicle(
            unit, vehicle_type=item.get("type"),
            route=item.get("route"),
            location_id=loc.id)
        if item.get("route"):
            vehicle.route = item["route"]
        vehicle.active = True
        ensure_entry(sched, vehicle, order_index=position,
                     prep_time=item.get("prep_time"))
        position += 1
        db.session.commit()

    # Deactivate removed vehicles (never delete data)
    for rem in preview["removed"]:
        vehicle = vehicles.find_vehicle_by_unit(rem["unit"])
        if vehicle:
            vehicle.active = False

    # Substitutions detected in free text are intentionally NOT auto-applied:
    # the direction is ambiguous (e.g. "190 Van Replace 155" may mean 190
    # replaces 155 or vice versa). They are surfaced in the import preview so
    # the operator completes them manually with the Replace Vehicle action,
    # avoiding silent mistakes. Confident, manually-confirmed substitutions
    # are handled by move_entry_to_replacement elsewhere.

    db.session.commit()
    return sched
