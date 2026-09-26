"""Prep timer: the Start -> Pause -> Resume -> Done workflow per vehicle.

Every vehicle on a day's board gets at most one :class:`PrepSession`. The
service owns the whole workflow and refuses invalid transitions (starting a
vehicle that is already running, finishing one that was never started, ...).
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

def session_for(entry):
    """The prep session for a schedule entry, or None if never started."""
    if entry is None:
        return None
    session = getattr(entry, "prep_session", None)
    if session is not None:
        return session
    return PrepSession.query.filter_by(entry_id=entry.id).first()


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


def _state(session, entry=None, at=None):
    """Timer state for a session (or for the absence of one)."""
    now = timeutils.to_eastern(at) or timeutils.now_eastern()
    if entry is not None:
        vehicle = getattr(entry, "vehicle", None)
        entry_id = getattr(entry, "id", None)
    else:
        vehicle = session.vehicle if session else None
        entry_id = session.entry_id if session else None
    status = session.status if session else "none"
    elapsed = elapsed_seconds(session, at=now)
    running = status == "running"
    segment = timeutils.load_ts(session.last_event_at) if session else None
    return {
        "entry_id": entry_id,
        "vehicle": vehicle.unit_number if vehicle else "",
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "active": status in ("running", "paused"),
        "employee": session.employee.name if session and session.employee else None,
        "initials": session.employee.initials if session and session.employee else None,
        "started": timeutils.fmt_time(session.started_at) if session else None,
        "started_iso": session.started_at if session else None,
        "finished": timeutils.fmt_time(session.finished_at) if session else None,
        "finished_iso": session.finished_at if session else None,
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


def state(entry, at=None):
    """Everything the board (and the browser) needs to render one timer.

    Returned for entries that were never started too, so a row can always show
    a consistent timer block. ``elapsed`` is the active prep time as of the
    server's clock; while the session is running the browser adds
    ``segment_epoch`` to it every second to tick the clock live.
    """
    return _state(session_for(entry), entry=entry, at=at)


def session_state(session, at=None):
    """Timer state for a stored session (used by history views)."""
    return _state(session, at=at)


def vehicle_history(vehicle, limit=30):
    """Every prep session ever run on a vehicle, most recent first.

    Ordered by id (sessions are created chronologically) so a vehicle's history
    is stable even if two runs straddle a daylight-saving offset change.
    """
    sessions = PrepSession.query.filter_by(
        vehicle_id=vehicle.id).order_by(
        PrepSession.id.desc()).limit(limit).all()
    return [session_state(s) for s in sessions]


def board_states(sched, at=None):
    """Timer state for every entry on a day's board, in board order."""
    return [state(entry, at=at) for entry in sched.entries]


def total_active_seconds(sched, at=None):
    """Total active prep time recorded on a day's board."""
    return sum(elapsed_seconds(session_for(e), at=at) for e in sched.entries)


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


def start(entry, employee_id=None, at=None):
    """Start the vehicle's prep timer (and the vehicle itself).

    The timer begins the instant Start is pressed, so the live clock and the
    recorded history can never drift apart.
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
    session = session_for(entry)
    if session is not None:
        if session.status in ("running", "paused"):
            verb = "running" if session.status == "running" else "paused"
            raise PrepTimerError(
                f"Vehicle {entry.vehicle.unit_number} is already started "
                f"(timer {verb})")
        raise PrepTimerError(
            f"Vehicle {entry.vehicle.unit_number} has already been finished")

    employee_id = _employee_id(employee_id)
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
    employee = Employee.query.get(employee_id) if employee_id else None
    if employee is not None:
        employee.current_vehicle_id = entry.vehicle_id
        employee.current_vehicle_set_on = timeutils.now_eastern().date()
    db.session.commit()
    return session


def pause(entry, employee_id=None, at=None):
    """Pause a running timer, recording when the pause happened."""
    session = session_for(entry)
    if session is None:
        raise PrepTimerError(
            f"Vehicle {getattr(getattr(entry, 'vehicle', None), 'unit_number', '')} "
            f"was never started, so it cannot be paused")
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


def resume(entry, employee_id=None, at=None):
    """Resume a paused timer without losing the work time already banked."""
    session = session_for(entry)
    if session is None:
        raise PrepTimerError(
            f"Vehicle {getattr(getattr(entry, 'vehicle', None), 'unit_number', '')} "
            f"was never started, so it cannot be resumed")
    if session.status == "running":
        raise PrepTimerError("Timer is already running")
    if session.status == "finished":
        raise PrepTimerError("Timer is already finished")
    if session.status != "paused":
        raise PrepTimerError("Timer is not paused, so it cannot be resumed")
    moment = _now(at)
    session.status = "running"
    if employee_id:
        session.employee_id = _employee_id(employee_id) or session.employee_id
    _record(session, RESUME, moment, employee_id)
    return session


def finish(entry, employee_id=None, at=None):
    """Stop the timer and freeze the vehicle's total active prep time."""
    session = session_for(entry)
    if session is None:
        raise PrepTimerError(
            f"Vehicle {getattr(getattr(entry, 'vehicle', None), 'unit_number', '')} "
            f"was never started, so it cannot be finished")
    if session.status == "finished":
        raise PrepTimerError("Timer is already finished")
    moment = _now(at)
    _bank(session, moment)
    session.status = "finished"
    session.finished_at = timeutils.store_ts(moment)
    if employee_id:
        session.employee_id = _employee_id(employee_id) or session.employee_id
    _record(session, DONE, moment, employee_id)
    return session


def stop_active(entry, employee_id=None, at=None):
    """Stop a running/paused timer for any other completion path.

    Used when a vehicle is completed some other way (a full checklist, the
    End My Day page, ...) so a timer is never left running in the background.
    A paused timer is closed exactly where it paused, so the paused time is
    never billed as active prep time. Returns the finished session, or None if
    there was nothing to stop.
    """
    session = session_for(entry)
    if session is None or session.status == "finished":
        return None
    return finish(entry, employee_id=employee_id, at=at)


def get_entry(entry_id):
    """Look up a board entry, raising PrepTimerError(404) when missing."""
    entry = ScheduleEntry.query.get(entry_id)
    if entry is None:
        raise PrepTimerError("Vehicle not found on today's board", 404)
    return entry
