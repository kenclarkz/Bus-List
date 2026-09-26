"""Prep timer: the Start -> Pause -> Resume -> Done workflow per employee.

A vehicle can be worked by a whole crew at the same time, so the unit of work
is the **employee**, not the vehicle: every person who starts work on a vehicle
gets their own :class:`PrepSession` with their own clock, total and event log.
Starting a vehicle that somebody else is already working is therefore allowed,
and pausing or finishing one person's timer never touches anyone else's.

The service owns the whole workflow and refuses invalid transitions (starting a
vehicle you have already started, finishing a vehicle you never started, ...).
All timestamps are Eastern Time and stored as ISO-8601 strings carrying their
UTC offset, so a timer that is still running after a refresh, a reopened tab or
a daylight-saving change keeps counting the same amount of real time.
"""

from app.models import (
    db, Employee, PrepSession, PrepSessionEvent, ScheduleEntry,
)

from . import timeutils

START = "start"
PAUSE = "pause"
RESUME = "resume"
DONE = "done"

EVENT_LABELS = {
    START: "Started",
    PAUSE: "Paused",
    RESUME: "Resumed",
    DONE: "Done",
}

STATUS_LABELS = {
    "none": "Not started",
    "running": "Timer running",
    "paused": "Paused",
    "finished": "Completed",
}


class PrepTimerError(Exception):
    """An invalid prep-timer action (already running, never started, ...)."""

    def __init__(self, message, code=409):
        super().__init__(message)
        self.message = message
        self.code = code


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def sessions_for(entry):
    """Every prep session on a board entry, oldest first.

    One row per employee, so a vehicle worked by three people returns three
    sessions (see the ``(entry_id, employee_id)`` uniqueness rule).
    """
    if entry is None:
        return []
    sessions = getattr(entry, "prep_sessions", None)
    if sessions is None:
        sessions = PrepSession.query.filter_by(entry_id=entry.id).all()
    return sorted(sessions, key=lambda s: s.id or 0)


def active_sessions_for(entry):
    """The sessions on an entry that still count (running or paused)."""
    return [s for s in sessions_for(entry) if s.status in ("running", "paused")]


def employee_sessions_for(entry, employee_id):
    """The sessions an employee holds on an entry, oldest first."""
    emp_id = _employee_id(employee_id)
    return [s for s in sessions_for(entry) if s.employee_id == emp_id]


def employee_active_session(entry, employee_id):
    """The employee's own running/paused session on an entry, or None."""
    active = [s for s in employee_sessions_for(entry, employee_id)
              if s.status in ("running", "paused")]
    return active[-1] if active else None


def session_for(entry, employee_id=None):
    """The prep session a caller means for an entry, or None.

    With an employee this is that employee's session on the vehicle; without
    one (history views, reports) it is the entry's most recent session.
    """
    if entry is None:
        return None
    if employee_id not in (None, ""):
        mine = employee_sessions_for(entry, employee_id)
        if mine:
            return mine[-1]
    sessions = sessions_for(entry)
    return sessions[-1] if sessions else None


def elapsed_seconds(session, at=None):
    """Total *active* prep seconds: banked segments plus the running one."""
    if session is None:
        return 0
    total = int(session.total_seconds or 0)
    if session.status == "running":
        segment = timeutils.load_ts(session.last_event_at)
        now = timeutils.to_eastern(at) or timeutils.now_eastern()
        if segment is not None:
            total += int((now - segment).total_seconds())
    return max(0, total)


def entry_elapsed_seconds(entry, at=None):
    """Active prep seconds recorded on a vehicle by every employee on it.

    Concurrent workers are counted separately: the value is the total labour
    put into the vehicle, so two people working a bus for 10 minutes is 20
    minutes of prep time (see README, "How the prep timer works").
    """
    return sum(elapsed_seconds(s, at=at) for s in sessions_for(entry))


def _bank(session, at):
    """Freeze the seconds worked in the running segment into the total.

    Called while the session is still running, so paused time is never billed
    as active prep time.
    """
    total = int(session.total_seconds or 0)
    segment = timeutils.load_ts(session.last_event_at)
    moment = timeutils.to_eastern(at) or timeutils.now_eastern()
    if session.status == "running" and segment is not None:
        total += int((moment - segment).total_seconds())
    session.total_seconds = max(0, total)
    return session.total_seconds


def _event_rows(session, at=None):
    """The session's event log with Eastern 12-hour labels for display."""
    if session is None:
        return []
    rows = []
    for event in session.events:
        rows.append({
            "type": event.event_type,
            "label": EVENT_LABELS.get(event.event_type, event.event_type),
            "employee": event.employee.name if event.employee else None,
            "at": timeutils.fmt_time(event.occurred_at),
            "at_iso": event.occurred_at,
            "elapsed": timeutils.fmt_duration(event.total_seconds or 0),
            "elapsed_seconds": int(event.total_seconds or 0),
        })
    return rows


def _worker_state(session, at=None, vehicle=None):
    """Timer state for one employee's session on a vehicle."""
    now = timeutils.to_eastern(at) or timeutils.now_eastern()
    status = session.status
    elapsed = elapsed_seconds(session, at=now)
    running = status == "running"
    segment = timeutils.load_ts(session.last_event_at)
    vehicle = vehicle if vehicle is not None else session.vehicle
    return {
        "session_id": session.id,
        "entry_id": session.entry_id,
        "employee_id": session.employee_id,
        "employee": session.employee.name if session.employee else None,
        "initials": session.employee.initials if session.employee else None,
        "vehicle": vehicle.unit_number if vehicle else "",
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "active": status in ("running", "paused"),
        "started": timeutils.fmt_time(session.started_at),
        "started_iso": session.started_at,
        "finished": timeutils.fmt_time(session.finished_at),
        "finished_iso": session.finished_at,
        "elapsed": elapsed,
        "elapsed_label": timeutils.fmt_duration(elapsed),
        "clock_label": timeutils.fmt_clock(elapsed),
        # Base for the live timer: seconds already banked when the page was
        # rendered, and the moment the current segment began (epoch ms).
        "base_seconds": int(session.total_seconds or 0) if running else elapsed,
        "segment_epoch": timeutils.epoch_ms(segment) if running and segment else None,
        "server_epoch": timeutils.epoch_ms(now),
        "total_label": timeutils.fmt_duration(elapsed),
        "events": _event_rows(session, at=now),
    }


def _state(sessions, entry=None, at=None):
    """Timer state for a vehicle: every employee's clock plus the vehicle total.

    The top level mirrors a single-employee timer (so the board, the report and
    the history views keep one shape) and ``workers`` carries one entry per
    employee, which is what makes a crew on one vehicle visible.
    """
    now = timeutils.to_eastern(at) or timeutils.now_eastern()
    if entry is not None:
        vehicle = getattr(entry, "vehicle", None)
        entry_id = getattr(entry, "id", None)
    else:
        vehicle = sessions[0].vehicle if sessions else None
        entry_id = sessions[0].entry_id if sessions else None
    workers = [_worker_state(s, at=now, vehicle=vehicle) for s in sessions]
    active = [w for w in workers if w["active"]]
    running = [w for w in workers if w["status"] == "running"]
    if running:
        status = "running"
    elif active:
        status = "paused"
    elif workers:
        status = "finished"
    else:
        status = "none"
    elapsed = sum(w["elapsed"] for w in workers)
    # Banked seconds of the running clocks, plus the segment they started at.
    # With several employees running at once the total is the sum of their
    # own clocks, so the browser adds the per-employee clocks up instead
    # (see the data-prep-sum clock on the board row).
    segments = [w["segment_epoch"] for w in running if w["segment_epoch"]]
    started_iso = min((w["started_iso"] for w in workers if w["started_iso"]),
                      default=None, key=timeutils.load_ts)
    finished_iso = max((w["finished_iso"] for w in workers if w["finished_iso"]),
                       default=None, key=timeutils.load_ts)
    return {
        "entry_id": entry_id,
        "vehicle": vehicle.unit_number if vehicle else "",
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "active": bool(active),
        "workers": workers,
        "worker_count": len(workers),
        "running_count": len(running),
        "workers_label": ", ".join(
            w["employee"] for w in workers if w["employee"]) or None,
        # The first worker stands in for the single-employee case.
        "employee": workers[0]["employee"] if workers else None,
        "initials": workers[0]["initials"] if workers else None,
        "started": timeutils.fmt_time(started_iso) if started_iso else None,
        "started_iso": started_iso,
        "finished": timeutils.fmt_time(finished_iso) if finished_iso else None,
        "finished_iso": finished_iso,
        "elapsed": elapsed,
        "elapsed_label": timeutils.fmt_duration(elapsed),
        "clock_label": timeutils.fmt_clock(elapsed),
        "base_seconds": sum(int(s.total_seconds or 0) for s in sessions
                            if s.status == "running"),
        "segment_epoch": segments[0] if len(segments) == 1 else None,
        "server_epoch": timeutils.epoch_ms(now),
        "total_label": timeutils.fmt_duration(elapsed),
        # Every employee's events in the order they were recorded, so one log
        # tells the whole story of the vehicle.
        "events": [event for s in sessions for event in _event_rows(s, at=now)],
    }


def state(entry, at=None):
    """Everything the board (and the browser) needs to render one vehicle.

    Returned for vehicles that were never started too, so a row can always
    show a consistent timer block. ``elapsed`` is the vehicle's total active
    prep time as of the server's clock; each employee's own clock is in
    ``workers`` and the browser ticks those live from ``segment_epoch``.
    """
    return _state(sessions_for(entry), entry=entry, at=at)


def session_state(session, at=None):
    """Timer state for a stored session (used by history views)."""
    if session is None:
        return _state([], at=at)
    return _worker_state(session, at=at)


def vehicle_history(vehicle, limit=30):
    """Every prep session ever run on a vehicle, most recent first.

    One row per employee per run, so a day worked by a crew of three shows
    three runs. Ordered by id (sessions are created chronologically) so a
    vehicle's history is stable even if two runs straddle a daylight-saving
    offset change.
    """
    sessions = PrepSession.query.filter_by(
        vehicle_id=vehicle.id).order_by(
            PrepSession.id.desc()).limit(limit).all()
    return [session_state(s) for s in sessions]


def board_states(sched, at=None):
    """Timer state for every entry on a day's board, in board order."""
    return [state(entry, at=at) for entry in sched.entries]


def active_worker_states(sched, at=None):
    """The clock of every employee currently working on the board, by id.

    One vehicle can carry a whole crew, so the "Now Working" board asks for
    each employee's *own* clock rather than the vehicle total. Only running or
    paused clocks are included: somebody who is done has left the floor. If
    an employee somehow has more than one clock going, the most recently
    started one is the vehicle they are actually on.
    """
    workers = {}
    for board_state in board_states(sched, at=at):
        for worker in board_state["workers"]:
            if worker["employee_id"] is None or not worker["active"]:
                continue
            current = workers.get(worker["employee_id"])
            started = timeutils.load_ts(worker["started_iso"])
            if current is None or started >= current[0]:
                workers[worker["employee_id"]] = (started, worker)
    return {emp_id: worker for emp_id, (_, worker) in workers.items()}


def total_active_seconds(sched, at=None):
    """Total active prep time recorded on a day's board (all employees)."""
    return sum(entry_elapsed_seconds(e, at=at) for e in sched.entries)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def _now(at=None):
    return timeutils.to_eastern(at) or timeutils.now_eastern()


def _employee_id(employee_id):
    if employee_id in (None, "", "None"):
        return None
    try:
        emp_id = int(employee_id)
    except (TypeError, ValueError):
        return None
    return emp_id if Employee.query.get(emp_id) else None


def _record(session, event_type, moment, employee_id):
    """Stamp the event and its Eastern timestamp onto the session.

    The running total is banked by _bank() *before* the status changes, so it
    is left alone here: this event captures the total as of this moment.
    """
    session.last_event_at = timeutils.store_ts(moment)
    db.session.add(PrepSessionEvent(
        session_id=session.id,
        event_type=event_type,
        occurred_at=session.last_event_at,
        employee_id=employee_id or session.employee_id,
        total_seconds=int(session.total_seconds or 0),
    ))
    db.session.commit()
    return session


def _unit(entry):
    return getattr(getattr(entry, "vehicle", None), "unit_number", "")


def _resolve_session(entry, employee_id, session_id=None):
    """Which session an action applies to, and why (for error messages).

    An explicit ``session_id`` wins: the button lives in that employee's row,
    so it acts on that employee's clock (which is how a shared board screen
    stays unambiguous). Otherwise the acting employee's own session, and
    failing that the vehicle's only active session when there is no ambiguity.
    """
    if entry is None:
        return None, "missing"
    if session_id not in (None, ""):
        try:
            wanted = int(session_id)
        except (TypeError, ValueError):
            return None, "foreign"
        session = PrepSession.query.get(wanted)
        if session is not None and session.entry_id == entry.id:
            return session, "session"
        return None, "foreign"
    mine = employee_active_session(entry, employee_id)
    if mine is not None:
        return mine, "mine"
    active = active_sessions_for(entry)
    if len(active) == 1:
        return active[0], "only"
    return None, "none"


def _no_session_error(entry, how, action):
    """Explain why an action found no clock to act on."""
    if how == "foreign":
        return f"That timer is not on vehicle {_unit(entry)}"
    unit = _unit(entry)
    if not sessions_for(entry):
        return f"Vehicle {unit} was never started, so it cannot be {action}"
    others = active_sessions_for(entry)
    if others:
        names = ", ".join(
            s.employee.name for s in others if s.employee) or "someone else"
        return (f"You have no prep timer on vehicle {unit} — "
                f"{names} {'is' if len(others) == 1 else 'are'} working it")
    return "Timer is already finished"


def _claim_vehicle(employee_id, entry, at=None):
    """Mark the employee as working on this vehicle (drives Now Working)."""
    employee = Employee.query.get(employee_id) if employee_id else None
    if employee is None:
        return None
    employee.current_vehicle_id = entry.vehicle_id
    employee.current_vehicle_set_on = (at or _now()).date()
    return employee


def _release_employee(employee_id, entry):
    """Free an employee from a vehicle they are no longer working on."""
    if not employee_id:
        return
    employee = Employee.query.get(employee_id)
    if employee is not None and employee.current_vehicle_id == entry.vehicle_id:
        employee.current_vehicle_id = None


def start(entry, employee_id=None, at=None):
    """Start this employee's prep timer (and the vehicle itself).

    The timer begins the instant Start is pressed, so the live clock and the
    recorded history can never drift apart. Other employees may already be
    working the same vehicle: each of them gets their own clock.
    """
    if entry is None:
        raise PrepTimerError("Vehicle not found on today's board", 404)
    moment = _now(at)
    if entry.status == "skipped":
        raise PrepTimerError(
            f"Vehicle {entry.vehicle.unit_number} is skipped and cannot be started")
    if entry.status == "completed":
        raise PrepTimerError(
            f"Vehicle {entry.vehicle.unit_number} is already complete")
    employee_id = _employee_id(employee_id)
    # Only this employee's own history on this vehicle is off limits; somebody
    # else's finished run is nobody's business.
    mine = employee_sessions_for(entry, employee_id)
    if mine:
        session = mine[-1]
        if session.status in ("running", "paused"):
            verb = "running" if session.status == "running" else "paused"
            raise PrepTimerError(
                f"Vehicle {entry.vehicle.unit_number} is already started "
                f"for you (timer {verb})")
        raise PrepTimerError(
            f"Vehicle {entry.vehicle.unit_number} has already been finished "
            f"for you")

    session = PrepSession(
        entry_id=entry.id,
        vehicle_id=entry.vehicle_id,
        employee_id=employee_id,
        status="running",
        started_at=timeutils.store_ts(moment),
        last_event_at=timeutils.store_ts(moment),
        total_seconds=0,
    )
    db.session.add(session)
    db.session.flush()
    _record(session, START, moment, employee_id)

    # Starting a vehicle also puts it in progress on the board and marks the
    # employee as working on it (the pre-timer behaviour, kept intact).
    if entry.status == "pending":
        entry.status = "in_progress"
    _claim_vehicle(employee_id, entry, at=moment)
    db.session.commit()
    return session


def pause(entry, employee_id=None, at=None, session_id=None):
    """Pause a running timer, recording when the pause happened."""
    session, how = _resolve_session(entry, employee_id, session_id)
    if session is None:
        raise PrepTimerError(
            _no_session_error(entry, how, "paused"))
    if session.status == "paused":
        raise PrepTimerError("Timer is already paused")
    if session.status == "finished":
        raise PrepTimerError("Timer is already finished")
    if session.status != "running":
        raise PrepTimerError("Timer is not running, so it cannot be paused")
    moment = _now(at)
    _bank(session, moment)
    session.status = "paused"
    _record(session, PAUSE, moment, employee_id)
    return session


def resume(entry, employee_id=None, at=None, session_id=None):
    """Resume a paused timer without losing the work time already banked."""
    session, how = _resolve_session(entry, employee_id, session_id)
    if session is None:
        raise PrepTimerError(
            _no_session_error(entry, how, "resumed"))
    if session.status == "running":
        raise PrepTimerError("Timer is already running")
    if session.status == "finished":
        raise PrepTimerError("Timer is already finished")
    if session.status != "paused":
        raise PrepTimerError("Timer is not paused, so it cannot be resumed")
    moment = _now(at)
    session.status = "running"
    _record(session, RESUME, moment, employee_id)
    return session


def finish(entry, employee_id=None, at=None, session_id=None):
    """Stop this employee's timer and freeze their total active prep time.

    Everybody else's clock on the vehicle keeps running: the vehicle is only
    finished on the board once the last employee working it is done.
    """
    session, how = _resolve_session(entry, employee_id, session_id)
    if session is None:
        raise PrepTimerError(
            _no_session_error(entry, how, "finished"))
    if session.status == "finished":
        raise PrepTimerError("Timer is already finished")
    moment = _now(at)
    _bank(session, moment)
    session.status = "finished"
    session.finished_at = timeutils.store_ts(moment)
    _record(session, DONE, moment, employee_id)
    if employee_active_session(entry, session.employee_id) is None:
        _release_employee(session.employee_id, entry)
    db.session.commit()
    return session


def stop_active(entry, employee_id=None, at=None, session_id=None):
    """Stop any running/paused timer for any other completion path.

    Used when a vehicle is completed some other way (a full checklist, the
    End My Day page, ...) so a timer is never left running in the background.
    A paused timer is closed exactly where it paused, so the paused time is
    never billed as active prep time. Every employee working the vehicle is
    stopped, because the vehicle itself is finished. Returns the last session
    stopped, or None if there was nothing to stop.
    """
    if session_id not in (None, ""):
        session, how = _resolve_session(entry, employee_id, session_id)
        if session is None or session.status == "finished":
            return None
        return finish(entry, employee_id=employee_id, at=at,
                      session_id=session_id)
    stopped = None
    for session in list(active_sessions_for(entry)):
        if session.status == "finished":
            continue
        stopped = finish(entry, employee_id=session.employee_id, at=at,
                         session_id=session.id)
    return stopped


def get_entry(entry_id):
    """Look up a board entry, raising PrepTimerError(404) when missing."""
    entry = ScheduleEntry.query.get(entry_id)
    if entry is None:
        raise PrepTimerError("Vehicle not found on today's board", 404)
    return entry
