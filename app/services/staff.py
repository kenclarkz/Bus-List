"""Deleting a person for good: their login account and their staff record.

Removing somebody (the Remove button on **Staff**) only closes the door behind
them. The account keeps its row, the staff record keeps its row, and every task,
clock, note and report they ever touched keeps saying who did it. That is the
safe half of the story, and it is how the app has always behaved.

A Manager sometimes needs the other half: a login typed in by mistake, or
somebody who has genuinely left and whose name should not be offered on the
board ever again. So both can be deleted outright.

**Deleting a login deletes nothing else.** The work is recorded against the
staff record, not the account, so every completed task, running timer and
report stays exactly where it is; only the sign-in goes, and the username is
free again for whoever is given it next.

**Deleting a staff record keeps the work too.** Every task check, clock, note,
service record, replacement, import, trash pickup and incident the person
touched is left in place and simply stops being credited to anybody, because
this app never deletes history -- it only stops naming the person in it. A
login still tied to the record is refused rather than left dangling: an Employee
account with no staff record behind it is handed a brand new one the moment that
person signs in, which is not what "delete them" should mean.
"""

from app.models import (
    db, Employee, UserAccount, PrepSession, PrepSessionEvent, TaskCompletion,
    Note, Replacement, ServiceRecord, PrepReportImport, TrashPickup,
    IncidentReport, IncidentNote, IncidentPhoto,
)


class StaffDeleteError(Exception):
    """A staff record that cannot be deleted without breaking something."""

# Every place a piece of work names the person who did it. Deleting a staff
# record walks this list and takes the name off each of those rows, leaving the
# work itself in place. The order is the order the flash message reads in: what
# they checked off, what they timed, then everything else.
WORK_REFERENCES = (
    ("checked task", TaskCompletion, "employee_id"),
    ("prep clock", PrepSession, "employee_id"),
    ("prep clock step", PrepSessionEvent, "employee_id"),
    ("note", Note, "employee_id"),
    ("replacement", Replacement, "employee_id"),
    ("cleaning record", ServiceRecord, "employee_id"),
    ("prep report import", PrepReportImport, "employee_id"),
    ("trash pickup", TrashPickup, "employee_id"),
    ("reported incident", IncidentReport, "reported_by"),
    ("assigned incident", IncidentReport, "assigned_to"),
    ("incident note", IncidentNote, "employee_id"),
    ("incident photo", IncidentPhoto, "uploaded_by"),
)


def work_counts(employee_id):
    """What this person has on record, by kind, skipping anything at zero."""
    if not employee_id:
        return {}
    counts = {}
    for label, model, column in WORK_REFERENCES:
        n = model.query.filter(
            getattr(model, column) == employee_id).count()
        if n:
            counts[label] = n
    return counts


def work_summary(employee_id):
    """``work_counts`` as one readable line, e.g. "3 checked tasks, 1 note"."""
    counts = work_counts(employee_id)
    return ", ".join(f"{n} {label}{'' if n == 1 else 's'}"
                     for label, n in counts.items())


def open_clock(employee_id):
    """A clock this person left running or paused, if any.

    A live clock belongs to the work in front of them, not to their record, so
    it is refused rather than orphaned: the Manager stops the timer the same way
    they stop any other, and the person can then be deleted.
    """
    if not employee_id:
        return None
    return PrepSession.query.filter(
        PrepSession.employee_id == employee_id,
        PrepSession.status.in_(("running", "paused")),
    ).order_by(PrepSession.id.desc()).first()


def tied_accounts(employee_id):
    """Logins still signed in as this person, if any.

    Deleting the record out from under one of these would leave it pointing at
    nothing, and signing in would then quietly hand the person a brand new staff
    record -- undoing the delete without anybody noticing. So they go first.
    """
    if not employee_id:
        return []
    return UserAccount.query.filter_by(employee_id=employee_id).all()


def purge_employee(employee_id):
    """Delete a staff record, keeping every piece of work they ever did.

    Each of the rows in :data:`WORK_REFERENCES` is left in place with the
    person taken off it, so a task somebody checked in March still reads as
    checked in March; the record itself is then gone, so their name is never
    offered on the board again.

    Refuses, rather than orphaning a login, while one is still tied to the
    record: see :func:`tied_accounts`.
    """
    accounts = tied_accounts(employee_id)
    if accounts:
        raise StaffDeleteError(
            f"still signs in on {', '.join(a.username for a in accounts)}")
    for _label, model, column in WORK_REFERENCES:
        model.query.filter(
            getattr(model, column) == employee_id).update(
            {column: None}, synchronize_session=False)
    employee = Employee.query.get(employee_id)
    if employee is not None:
        db.session.delete(employee)
    db.session.commit()
