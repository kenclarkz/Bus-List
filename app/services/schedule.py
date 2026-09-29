"""Daily schedule / detailing board logic.

A day's schedule is **locked once it is finalized**. Finalization happens
automatically every night at :data:`AUTO_FINALIZE_TIME` Eastern and stores the
day's totals, so a finalized day is a report, not a work list: nothing about it
may change afterwards, because the numbers already saved would stop describing
what happened. Every write path below therefore refuses to touch a finalized
day, and the routes that call them turn that refusal into an answer the person
who pressed can act on. Reopening a day is a Manager-only act
(``reopen_day``), never something an employee can do for themselves.
"""
import re
from datetime import date, datetime

from app.models import (
    db, DailySchedule, ScheduleEntry, TaskCompletion, Vehicle,
    Replacement, Note, Setting,
)
from app.services import prep_timer, settings, vehicles
from app.services import timeutils

# The nightly automatic finalization cutoff, as Eastern wall-clock time. Kept
# here (rather than only in the scheduler) so the documentation of "when does a
# day close" lives with the day itself, and so tests can assert the schedule
# against it.
AUTO_FINALIZE_HOUR = 22
AUTO_FINALIZE_MINUTE = 30


class FinalizedDayError(Exception):
    """A change was refused because that day has already been finalized.

    Raised by every write path that would alter a finalized day's report. The
    routes catch it and answer in the shape the caller expects (JSON for the
    board's fetch calls, a flash + redirect for the form posts), so the lock is
    enforced by the service rather than by the UI hiding buttons.
    """

    def __init__(self, message, work_date=None):
        super().__init__(message)
        self.message = message
        self.work_date = work_date


def is_locked(sched):
    """Whether a day's schedule is finalized and therefore read-only."""
    return bool(sched is not None and sched.finalized)


def locked_message(work_date):
    """The refusal a person sees when they touch a finalized day."""
    when = work_date.strftime('%b %d') if work_date else "this day"
    return (f"{when} was finalized automatically at "
            f"{AUTO_FINALIZE_HOUR:02d}:{AUTO_FINALIZE_MINUTE:02d} Eastern and its "
            f"report is locked. Ask a Manager to reopen it if something needs "
            f"correcting.")


def _refuse_if_locked(sched):
    """Raise :class:`FinalizedDayError` when ``sched`` is finalized.

    The offending day rides along on the exception so the route that catches it
    can send the person back to the report that just refused them.
    """
    if is_locked(sched):
        work_date = sched.work_date if sched is not None else None
        raise FinalizedDayError(locked_message(work_date), work_date=work_date)


def schedule_for_entry(entry):
    """The day an entry belongs to."""
    if entry is None:
        return None
    return entry.schedule or db.session.get(DailySchedule, entry.schedule_id)


def today():
    return timeutils.today_eastern()


def clear_stale_current_vehicles():
    """Clear 'now working' assignments for any employee whose current vehicle
    was set on a previous day. Employees are expected to finish each vehicle the
    same day they start it, so a leftover assignment after midnight is stale
    (e.g. the employee forgot to hit Done) and must not linger on the board."""
    from ..models import Employee

    today_date = today()
    changed = False
    stale = Employee.query.filter(Employee.current_vehicle_id.isnot(None)).all()
    for emp in stale:
        on = emp.current_vehicle_set_on
        if on is None or on < today_date:
            emp.current_vehicle_id = None
            emp.current_vehicle_set_on = None
            changed = True
    if changed:
        db.session.commit()


def get_or_create_schedule(d=None, location=None):
    d = d or today()
    loc = location or vehicles.default_location()
    sched = DailySchedule.query.filter_by(work_date=d, location_id=loc.id).first()
    if not sched:
        sched = DailySchedule(work_date=d, location_id=loc.id)
        db.session.add(sched)
        db.session.commit()
    return sched


def refresh_type_entries(vtype):
    """Re-sync checklist rows for all open (non-finalized) schedule entries
    whose vehicle is of the given vehicle type."""
    if vtype is None:
        return
    for entry in ScheduleEntry.query.join(
            Vehicle, ScheduleEntry.vehicle_id == Vehicle.id
    ).filter(
        Vehicle.vehicle_type_id == vtype.id,
        DailySchedule.finalized.is_(False),
    ).join(DailySchedule, ScheduleEntry.schedule_id == DailySchedule.id):
        destroy_and_recreate_tasks(entry)
    db.session.commit()


def ensure_entry(sched, vehicle, order_index=0, prep_time=None,
                 pickup_time=None, driver_code=None):
    _refuse_if_locked(sched)
    entry = ScheduleEntry.query.filter_by(schedule_id=sched.id,
                                          vehicle_id=vehicle.id).first()
    if not entry:
        entry = ScheduleEntry(
            schedule_id=sched.id,
            vehicle_id=vehicle.id,
            status="pending",
            order_index=order_index,
            prep_time=prep_time,
            pickup_time=pickup_time,
            driver_code=driver_code,
        )
        db.session.add(entry)
        db.session.flush()
        create_task_rows(entry)
        db.session.commit()
    else:
        if prep_time is not None and entry.prep_time != prep_time:
            entry.prep_time = prep_time
        if pickup_time is not None and entry.pickup_time != pickup_time:
            entry.pickup_time = pickup_time
        if driver_code is not None and entry.driver_code != driver_code:
            entry.driver_code = driver_code
        db.session.commit()
    return entry


def _entry_categorized_checklist(entry):
    vehicle = entry.vehicle
    vtype = vehicle.vehicle_type if vehicle else None
    return settings.get_type_categorized_checklist(vtype)


def _entry_checklist(entry):
    categorized = _entry_categorized_checklist(entry)
    return categorized["inside"] + categorized["outside"]


def entry_task_groups(entry):
    """The entry's task rows split into its vehicle type's 'inside' and
    'outside' groups, in the order that type's checklist lists them.

    The board, the end-of-day summary and the printed report all render a
    vehicle's tasks from here, so each one shows exactly the Inside and Outside
    tasks its own type says, in the order they are typed. Task rows the current
    checklist no longer mentions (work already recorded against a list that has
    since been edited) are kept and shown at the end of their own group rather
    than dropped from the record.
    """
    categorized = _entry_categorized_checklist(entry)
    by_name = {}
    for task in entry.tasks:
        by_name.setdefault(task.task_name, []).append(task)

    groups = {"inside": [], "outside": []}
    for scope in prep_timer.SCOPES:
        for name in categorized[scope]:
            waiting = by_name.get(name)
            if waiting:
                groups[scope].append(waiting.pop(0))
    for name, waiting in by_name.items():
        groups[settings.task_category(name, categorized)].extend(waiting)
    return groups


def entry_incomplete_labels(entry):
    """The tasks an entry was closed with unticked, in its vehicle type's own
    order, each labelled with the side of the vehicle it belongs to.

    This is the wording of the board's "Not completed" note, so a note written by
    the browser after a press says exactly what a page reload would have said.
    """
    groups = entry_task_groups(entry)
    return [f"{task.task_name} ({scope})"
            for scope in prep_timer.SCOPES
            for task in groups[scope]
            if not task.completed]


def _outside_tasks_complete(entry):
    vehicle = entry.vehicle
    if vehicle is None:
        return False
    outside = settings.get_type_categorized_checklist(
        vehicle.vehicle_type)["outside"]
    if not outside:
        return False
    completed = {
        task.task_name.strip().casefold()
        for task in entry.tasks
        if task.completed
    }
    return all(name.strip().casefold() in completed for name in outside)


def create_task_rows(entry):
    for tname in _entry_checklist(entry):
        if not any(t.task_name == tname for t in entry.tasks):
            entry.tasks.append(TaskCompletion(
                entry_id=entry.id,
                task_name=tname,
                completed=False,
            ))


def entry_progress(entry):
    """Return (done, total, pct) for an entry.

    A skipped vehicle does NOT count toward completion, so its progress stays at
    whatever was actually done (usually nothing) and it never reads as 100%.
    """
    tasks = entry.tasks
    if not tasks:
        done, total = 0, 0
    else:
        done = sum(1 for t in tasks if t.completed)
        total = len(tasks)
    pct = round(done / total * 100) if total else 0
    return done, total, pct


def update_entry_status(entry):
    if getattr(entry, "status", None) == "skipped":
        return "skipped"
    done, total, pct = entry_progress(entry)
    if total and done == total:
        entry.status = "completed"
    elif done > 0:
        entry.status = "in_progress"
    else:
        entry.status = "pending"
    db.session.commit()
    return entry.status


def complete_entry(entry, employee_id=None):
    """Force-complete an entry even if not all tasks are done.

    Incomplete tasks are left as-is (not checked) so the manager can see
    exactly what was and wasn't completed. The entry is marked completed
    and the employee is freed from the vehicle.

    Refused outright on a finalized day: the vehicle's status is part of the
    report that was already saved.
    """
    _refuse_if_locked(schedule_for_entry(entry))
    if entry.status not in ("in_progress", "pending"):
        return entry.status
    entry.status = "completed"
    if entry.vehicle_id:
        from ..models import Employee
        Employee.query.filter_by(current_vehicle_id=entry.vehicle_id).update(
            {"current_vehicle_id": None}, synchronize_session=False)
    db.session.commit()
    return entry.status


def set_entry_skipped(entry, skipped=True, reason=""):
    """Mark a vehicle as skipped or un-skip it.

    Manual skips never count toward completion: the entry keeps its own progress
    and is reported separately as skipped. Transit auto-skips are also reported
    separately and excluded from the day's work totals.

    A finalized day is locked, so neither skipping nor un-skipping is possible
    there: the saved report says how many vehicles were skipped and why.
    """
    _refuse_if_locked(schedule_for_entry(entry))
    if skipped:
        entry.status = "skipped"
        entry.skip_reason = (reason or "").strip()[:255] or None
    else:
        entry.status = "pending"
        entry.skip_reason = None
        update_entry_status(entry)
    # Skipping frees the vehicle from anyone currently working it.
    if entry.vehicle_id:
        from ..models import Employee
        Employee.query.filter_by(current_vehicle_id=entry.vehicle_id).update(
            {"current_vehicle_id": None}, synchronize_session=False)
    db.session.commit()
    return entry.status


def toggle_task(entry_id, task_name, checked, employee_id=None):
    entry = ScheduleEntry.query.get(entry_id)
    if not entry:
        return None
    # Ticking (or unticking) a box changes the day totals, so it is refused on a
    # finalized day -- including a stray request that arrives after the nightly
    # job closed the books.
    _refuse_if_locked(schedule_for_entry(entry))
    was_outside_complete = _outside_tasks_complete(entry)
    task = next((t for t in entry.tasks if t.task_name == task_name), None)
    if not task:
        task = TaskCompletion(entry_id=entry.id, task_name=task_name)
        entry.tasks.append(task)
    task.completed = bool(checked)
    task.completed_at = datetime.utcnow() if checked else None
    task.employee_id = employee_id if checked else None
    # Update employee's current vehicle when they check a task
    if checked and employee_id:
        from ..models import Employee
        emp = Employee.query.get(employee_id)
        if emp:
            emp.current_vehicle_id = entry.vehicle_id
            emp.current_vehicle_set_on = timeutils.today_eastern()
    db.session.commit()
    # Record last washed / detailed in history when appropriate
    if checked:
        if (not was_outside_complete
                and _outside_tasks_complete(entry)):
            vehicles.add_service_record(
                entry.vehicle, service_type="wash",
                employee_id=employee_id, source="checklist",
                at=datetime.utcnow())
        if task_name.lower() == "sweep":
            vehicle = entry.vehicle
            vehicle.cleanings_since_dump = (vehicle.cleanings_since_dump or 0) + 1
            db.session.commit()
        if task_name.lower() == "dump":
            vehicles.add_service_record(
                entry.vehicle, service_type="dump",
                employee_id=employee_id, source="checklist",
                at=datetime.utcnow())
        if task_name.lower() == "final inspection":
            vehicles.add_service_record(
                entry.vehicle, service_type="prep",
                employee_id=employee_id, source="checklist",
                at=datetime.utcnow())
    else:
        # Un-checking a task should un-do its effect on the vehicle.
        if task_name.lower() == "sweep":
            v = entry.vehicle
            v.cleanings_since_dump = max(0, (v.cleanings_since_dump or 0) - 1)
            db.session.commit()
    update_entry_status(entry)
    # A completed vehicle is no longer "currently working" — clear it so the
    # employee(s) move on to the next vehicle.
    if entry.status == "completed" and entry.vehicle_id:
        from ..models import Employee
        Employee.query.filter_by(current_vehicle_id=entry.vehicle_id).update(
            {"current_vehicle_id": None}, synchronize_session=False)
        db.session.commit()
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
    left off. A finalized day is locked, so a substitution cannot be recorded
    against one after the fact.
    """
    _refuse_if_locked(sched)
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
    new_names = _entry_checklist(entry)
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


def clear_employee_assignments(vehicle_ids):
    """Clear current_vehicle_id for any employee pointed at the given vehicles."""
    if not vehicle_ids:
        return
    from ..models import Employee
    Employee.query.filter(Employee.current_vehicle_id.in_(vehicle_ids)).update(
        {"current_vehicle_id": None}, synchronize_session=False)


def delete_schedule(sched):
    """Delete a day's work: its entries, tasks, notes and replacements.

    Vehicle records and service history are kept; only the day's work is
    removed. Employees currently assigned to a vehicle in that day are freed
    so the board doesn't show stale 'now working' state.

    A finalized day cannot be deleted: it is the saved report of a shift that
    has already been closed, and taking it away is not a Manager's ordinary
    housekeeping. It has to be reopened first (and then a Manager is free to
    delete the reopened day if it really was wrong).
    """
    if sched is None:
        return 0
    _refuse_if_locked(sched)
    vehicle_ids = [e.vehicle_id for e in sched.entries if e.vehicle_id]
    clear_employee_assignments(vehicle_ids)

    replacements = Replacement.query.filter(
        Replacement.replaced_at >= datetime.combine(sched.work_date, datetime.min.time()),
        Replacement.replaced_at <= datetime.combine(sched.work_date, datetime.max.time()),
    ).all()
    for rep in replacements:
        db.session.delete(rep)

    Note.query.filter_by(work_date=sched.work_date).delete(
        synchronize_session=False)

    count = len(sched.entries)
    db.session.delete(sched)
    db.session.commit()
    return count


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def build_preview(parsed, location=None):
    """Compare parsed report against DB and produce a preview dict.

    Returns dict with 'new', 'updated', 'removed', 'route_changes',
    'replacements', 'uncertain', 'unchanged' and 'transit'.
    """
    preview = {
        "new": [],
        "updated": [],
        "removed": [],
        "route_changes": [],
        "replacements": [],
        "uncertain": [],
        "unchanged": [],
        "transit": [],
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
             "prep_time": p.prep_time, "notes": p.notes,
             "pickup_time": p.pickup_time, "driver_code": p.driver_code}
        if p.uncertain:
            preview["uncertain"].append(v)

        # Transit buses are washed by another crew: they are still imported and
        # shown on the board, but skipped (see apply_import) and parked in the
        # transit dropdown instead of the main work list.
        if vehicles.is_transit_type(p.type) or vehicles.is_transit_vehicle(existing):
            preview["transit"].append(v)

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
            if p.notes and (existing.notes or "").strip() != p.notes.strip():
                changes.append("notes")
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
    handle replacements, deactivate removed vehicles. Never deletes history.

    Refused on a finalized day: rebuilding a closed day's work list would
    rewrite the report the nightly job already saved.
    """
    loc = location or vehicles.default_location()
    sched = get_or_create_schedule(schedule_date, loc)
    _refuse_if_locked(sched)
    position = 0

    for item in preview["new"] + preview["updated"] + preview["unchanged"]:
        unit = item["unit"]
        vehicle, _ = vehicles.find_or_create_vehicle(
            unit, vehicle_type=item.get("type"),
            route=item.get("route"),
            location_id=loc.id)
        if item.get("route"):
            vehicle.route = item["route"]
        if item.get("notes") and \
                (vehicle.notes or "").strip() != item["notes"].strip():
            vehicle.notes = item["notes"].strip()
        vehicle.active = True
        entry = ensure_entry(sched, vehicle, order_index=position,
                             prep_time=item.get("prep_time"),
                             pickup_time=item.get("pickup_time"),
                             driver_code=item.get("driver_code"))
        # Transit buses are not washed in this bay, so they are imported onto
        # the board but skipped and excluded from the day's work totals. Work
        # already done on the entry, or a manual skip, is never overwritten.
        if entry.status not in ("completed", "skipped") and (
                vehicles.is_transit_type(item.get("type"))
                or vehicles.is_transit_vehicle(vehicle)):
            set_entry_skipped(entry, skipped=True,
                              reason=vehicles.TRANSIT_SKIP_REASON)
        position += 1
        db.session.commit()

    # NOTE: removed vehicles used to be deactivated here. That made seeded
    # fleet vehicles vanish after every import because seed_defaults() only
    # seeds on an empty table. Vehicles not on today's report are simply left
    # off the schedule; they stay active so they appear on future days and can
    # be toggled manually if truly retired.

    # Substitutions detected in free text are intentionally NOT auto-applied:
    # the direction is ambiguous (e.g. "190 Van Replace 155" may mean 190
    # replaces 155 or vice versa). They are surfaced in the import preview so
    # the operator completes them manually with the Replace Vehicle action,
    # avoiding silent mistakes. Confident, manually-confirmed substitutions
    # are handled by move_entry_to_replacement elsewhere.

    db.session.commit()
    return sched
