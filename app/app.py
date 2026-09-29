"""Application factory and route registration."""
import atexit
import os
import re
import json
from datetime import date, datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, \
    jsonify, session, send_file, abort
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeoutError
from werkzeug.utils import secure_filename

from .models import db, Vehicle, Employee, ScheduleEntry, Replacement, Note, \
    DailySchedule, PrepReportImport, TrashPickup, Location, \
    IncidentReport, IncidentNote, IncidentPhoto, UserAccount
from .services import settings, vehicles, schedule as sched_svc
from .services import incidents as incidents_svc
from .services import prep_timer, staff as staff_svc, timeutils
from .services.incidents import ISSUE_TYPES, SEVERITIES, STATUSES, \
    allowed_photo, save_incident_photo, SEVERITY_CLASSES, STATUS_CLASSES

# A request that could not get to the database, rather than one the application
# got wrong: another request held the write lock for longer than SQLite's busy
# timeout, or the connection pool had nothing free. Both are transient and both
# used to escape as an HTML 500 the board could not read.
_DATABASE_BUSY_ERRORS = (OperationalError, PoolTimeoutError)

# The logins a brand-new database starts with, so there is always a way in and
# the first Manager can add real accounts from the Staff page. Once real
# accounts exist these are ordinary rows the Manager can remove like any other.
DEFAULT_ACCOUNTS = (
    ("manager", "Manager", UserAccount.ROLE_MANAGER),
    ("employee", "Employee", UserAccount.ROLE_EMPLOYEE),
    ("driver", "Driver", UserAccount.ROLE_DRIVER),
)

# Pages only a Manager account may visit.
MANAGER_ONLY_ENDPOINTS = {
    "vehicle_list",
    "vehicle_new",
    "vehicle_detail",
    "vehicle_edit",
    "vehicle_toggle_active",
    "employees_page",
    "employee_toggle_active",
    "employee_delete",
    "account_create",
    "account_toggle_active",
    "account_reset_password",
    "account_delete",
    # Incident management (review / edit / assign / note / photos / resolve).
    "incident_edit",
    "incident_note",
    "incident_photo_upload",
    "incident_resolve",
    "incident_photo_delete",
}

# Only ever a role name, never a password: these are checked on every request
# to decide which screens a signed-in person may open.
VALID_ROLES = frozenset(UserAccount.ROLES)


def role_home(role):
    """Default landing page for a signed-in role."""
    if role == "driver":
        return url_for("driver_dashboard")
    return url_for("dashboard")


def _save_uploaded_pdf(data, filename):
    """Save uploaded PDF bytes to disk and return the path."""
    import uuid
    from flask import current_app
    upload_dir = current_app.config.get("UPLOAD_FOLDER") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    safe_name = secure_filename(filename) or "report.pdf"
    unique_name = f"{uuid.uuid4().hex[:12]}_{safe_name}"
    path = os.path.join(upload_dir, unique_name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _parse_imported_by(raw):
    """Parse an 'imported_by' form value into an employee id (or None).

    'manager'            -> (None) the Manager account imported it.
    'employee:<id>'      -> (employee id) a specific employee imported it.
    """
    raw = (raw or "").strip()
    if raw.startswith("employee:"):
        try:
            return int(raw.split(":", 1)[1])
        except (TypeError, ValueError):
            return None
    return None


# ECHO Accident/Incident Report fields captured alongside an incident.
BOOL_REPORT_FIELDS = (
    "police_notified", "injuries_other_party", "employee_injured",
    "employee_citation", "other_driver_is_owner", "other_injuries",
    "other_driver_ticketed", "property_damage", "hazmat_spill",
)

TEXT_REPORT_FIELDS = (
    "driver_name", "police_report_number", "road_name", "intersection_with",
    "county_parish", "city_town", "roadway_conditions", "accident_type",
    "violation_reason", "investigating_supervisor", "employee_supervisor",
    "other_driver_name", "other_driver_address", "other_driver_city",
    "other_driver_state", "other_driver_zip", "other_driver_phone",
    "other_driver_license", "other_driver_license_state", "owner_name",
    "owner_address", "owner_city", "owner_state", "owner_zip", "other_make",
    "other_model", "other_year", "other_color", "other_plate",
    "other_passengers", "insurance_company", "insurance_policy",
    "insurance_address", "insurance_city", "insurance_state", "insurance_zip",
    "insurance_phone", "witnesses", "owner_object_struck",
)


def _parse_yes_no(value):
    """Convert a yes/no form value into True/False/None."""
    value = (value or "").strip().lower()
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


def _apply_report_fields(incident, form):
    """Copy the ECHO Accident/Incident Report form inputs onto a record."""
    for field in TEXT_REPORT_FIELDS:
        setattr(incident, field, (form.get(field) or "").strip() or None)
    for field in BOOL_REPORT_FIELDS:
        setattr(incident, field, _parse_yes_no(form.get(field)))


def _engine_options(db_uri):
    """Engine options for the way the board is actually used.

    A detailing board is a shared screen plus a phone per employee, so several
    requests are always in flight at once: somebody loading the page, somebody
    re-syncing their timers, somebody pressing Start. SQLAlchemy's stock pool
    for a file-backed SQLite database is 5 connections (+10 overflow) and
    sqlite3 gives up on a locked database after 5 seconds, so a handful of
    devices could either exhaust the pool or time out waiting for the write
    lock -- and a timed-out write raised out of the request as a bare 500, which
    the board could only report as "Could not start this vehicle. Try again."
    while the employee's press was refused for a reason that was never shown.
    """
    if not db_uri.startswith("sqlite"):
        return {}
    return {
        # Every device on shift can hold a connection for the length of one page
        # load, so a Start press always finds one free.
        "pool_size": 20,
        "max_overflow": 20,
        "pool_timeout": 60,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
        # sqlite3's busy timeout: wait out a collision instead of failing.
        "connect_args": {"timeout": 30},
    }


def _use_write_ahead_logging(engine):
    """Put a SQLite database into WAL mode on every connection it opens.

    SQLite's default rollback journal lets a single open reader block every
    writer, and makes two writers that overlap deadlock into a "database is
    locked" error. That is exactly the board's traffic pattern: one employee
    working one side of a bus while another starts the other side, with a page
    load or a timer re-sync landing on top. WAL lets readers and the writer run
    at the same time, so one employee's press can no longer take another's down
    with it. ``synchronous=NORMAL`` is safe alongside WAL and much cheaper on a
    small shop machine.
    """
    from sqlalchemy import event

    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            # A pragma is best effort: an in-memory database has no journal to
            # change, and a file another process has locked keeps its current
            # mode. Neither is worth refusing to start over.
            pass
        finally:
            cursor.close()


def create_app(test_config=None):
    app = Flask(__name__)

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    db_uri = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(data_dir, "detail.db"))
    if test_config and test_config.get("SQLALCHEMY_DATABASE_URI"):
        db_uri = test_config["SQLALCHEMY_DATABASE_URI"]
        if "sqlite" in db_uri and db_uri != "sqlite:///:memory:":
            ddir = os.path.dirname(db_uri.replace("sqlite:///", ""))
            if ddir:
                os.makedirs(ddir, exist_ok=True)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        SQLALCHEMY_DATABASE_URI=db_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS=_engine_options(db_uri),
        UPLOAD_FOLDER=os.path.join(base_dir, "uploads"),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    )

    db.init_app(app)
    with app.app_context():
        _use_write_ahead_logging(db.engine)
        db.create_all()
        _migrate()
        seed_defaults()

    register_routes(app)
    if not (test_config or app.config.get("TESTING")):
        _start_auto_end_day_scheduler(app)
    return app


def _migrate():
    """Lightweight column migrations for sqlite (no migration framework)."""
    import sqlite3
    from flask import current_app

    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if not uri.startswith("sqlite:///"):
        return
    path = uri.replace("sqlite:///", "", 1)
    if path == ":memory:":
        return
    con = sqlite3.connect(path)
    # The clock-set upgrade is kept apart from the column work below and never
    # fails quietly. A database left on a rule keyed on the vehicle alone cannot
    # hold a second clock on a vehicle at all, so a crew -- or one person working
    # both sides of a bus -- silently loses every press; the only sign of it used
    # to be a refused insert the board could not explain.
    try:
        _upgrade_prep_sessions(con)
    except Exception:
        current_app.logger.exception(
            "Could not upgrade prep_sessions to one clock per employee per "
            "clock set: this database still holds one clock per vehicle per "
            "clock set, so a second employee pressing Start on a vehicle is "
            "refused. The traceback above says what went wrong; nothing is "
            "lost by trying again on the next start.")
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(schedule_entries)")}
        if "prep_time" not in cols:
            con.execute("ALTER TABLE schedule_entries ADD COLUMN prep_time VARCHAR(40)")
            con.commit()
        tcols = {r[1] for r in con.execute("PRAGMA table_info(vehicle_types)")}
        if "checklist" not in tcols:
            con.execute("ALTER TABLE vehicle_types ADD COLUMN checklist TEXT")
            con.commit()
        # A vehicle type's checklist is its own now -- there is no shared default
        # left for one to fall back on -- so give every type that has never been
        # given a list of its own the standard Inside/Outside list to start from.
        # Types that already have one are left exactly as their manager typed it.
        con.execute(
            "UPDATE vehicle_types SET checklist = ? "
            "WHERE checklist IS NULL OR TRIM(checklist) = ''",
            (settings.standard_type_checklist(),))
        con.commit()
        ecols = {r[1] for r in con.execute("PRAGMA table_info(employees)")}
        if "current_vehicle_id" not in ecols:
            con.execute("ALTER TABLE employees ADD COLUMN current_vehicle_id INTEGER")
            con.commit()
        if "current_vehicle_set_on" not in ecols:
            con.execute("ALTER TABLE employees ADD COLUMN current_vehicle_set_on DATE")
            con.commit()
        # Backfill existing assigned vehicles so they start counting as "today" and
        # get cleared automatically when the next day rolls around.
        con.execute(
            "UPDATE employees SET current_vehicle_set_on = date('now') "
            "WHERE current_vehicle_id IS NOT NULL AND current_vehicle_set_on IS NULL")
        con.commit()
        scols = {r[1] for r in con.execute("PRAGMA table_info(schedule_entries)")}
        if "skip_reason" not in scols:
            con.execute("ALTER TABLE schedule_entries ADD COLUMN skip_reason VARCHAR(255)")
            con.commit()
        scols = {r[1] for r in con.execute("PRAGMA table_info(schedule_entries)")}
        if "pickup_time" not in scols:
            con.execute("ALTER TABLE schedule_entries ADD COLUMN pickup_time VARCHAR(40)")
            con.commit()
        if "driver_code" not in scols:
            con.execute("ALTER TABLE schedule_entries ADD COLUMN driver_code VARCHAR(120)")
            con.commit()
        vcols = {r[1] for r in con.execute("PRAGMA table_info(vehicles)")}
        if "last_dumped" not in vcols:
            con.execute("ALTER TABLE vehicles ADD COLUMN last_dumped DATETIME")
            con.commit()
        if "cleanings_since_dump" not in vcols:
            con.execute("ALTER TABLE vehicles ADD COLUMN cleanings_since_dump INTEGER DEFAULT 0")
            con.commit()
        pcols = {r[1] for r in con.execute("PRAGMA table_info(prep_report_imports)")}
        if "file_path" not in pcols:
            con.execute("ALTER TABLE prep_report_imports ADD COLUMN file_path VARCHAR(512)")
            con.commit()
        icols = {r[1] for r in con.execute("PRAGMA table_info(incident_reports)")}
        INCIDENT_REPORT_COLUMNS = [
            ("driver_name", "VARCHAR(200)"),
            ("police_notified", "BOOLEAN"),
            ("police_report_number", "VARCHAR(80)"),
            ("road_name", "VARCHAR(200)"),
            ("intersection_with", "VARCHAR(200)"),
            ("county_parish", "VARCHAR(120)"),
            ("city_town", "VARCHAR(120)"),
            ("roadway_conditions", "VARCHAR(120)"),
            ("accident_type", "VARCHAR(80)"),
            ("injuries_other_party", "BOOLEAN"),
            ("employee_injured", "BOOLEAN"),
            ("employee_citation", "BOOLEAN"),
            ("violation_reason", "VARCHAR(255)"),
            ("investigating_supervisor", "VARCHAR(200)"),
            ("employee_supervisor", "VARCHAR(200)"),
            ("other_driver_is_owner", "BOOLEAN"),
            ("other_driver_name", "VARCHAR(200)"),
            ("other_driver_address", "VARCHAR(200)"),
            ("other_driver_city", "VARCHAR(120)"),
            ("other_driver_state", "VARCHAR(40)"),
            ("other_driver_zip", "VARCHAR(40)"),
            ("other_driver_phone", "VARCHAR(80)"),
            ("other_driver_license", "VARCHAR(80)"),
            ("other_driver_license_state", "VARCHAR(40)"),
            ("owner_name", "VARCHAR(200)"),
            ("owner_address", "VARCHAR(200)"),
            ("owner_city", "VARCHAR(120)"),
            ("owner_state", "VARCHAR(40)"),
            ("owner_zip", "VARCHAR(40)"),
            ("other_make", "VARCHAR(100)"),
            ("other_model", "VARCHAR(100)"),
            ("other_year", "VARCHAR(20)"),
            ("other_color", "VARCHAR(60)"),
            ("other_plate", "VARCHAR(60)"),
            ("other_passengers", "VARCHAR(40)"),
            ("other_injuries", "BOOLEAN"),
            ("other_driver_ticketed", "BOOLEAN"),
            ("insurance_company", "VARCHAR(200)"),
            ("insurance_policy", "VARCHAR(120)"),
            ("insurance_address", "VARCHAR(200)"),
            ("insurance_city", "VARCHAR(120)"),
            ("insurance_state", "VARCHAR(40)"),
            ("insurance_zip", "VARCHAR(40)"),
            ("insurance_phone", "VARCHAR(80)"),
            ("witnesses", "TEXT"),
            ("property_damage", "BOOLEAN"),
            ("owner_object_struck", "VARCHAR(255)"),
            ("hazmat_spill", "BOOLEAN"),
        ]
        for col, ctype in INCIDENT_REPORT_COLUMNS:
            if col not in icols:
                con.execute(f"ALTER TABLE incident_reports ADD COLUMN {col} {ctype}")
                con.commit()
    except Exception:
        current_app.logger.exception("Database migration did not finish")
    finally:
        con.close()


# The key a prep clock is recorded under: the vehicle's board entry, the
# employee doing the work, and the clock set. Every employee therefore has
# their own timer, and a crew is never refused by the database. A constraint
# covering anything else is stale and has to be rebuilt (see
# ``_upgrade_prep_sessions``).
PREP_SESSION_UNIQUE_KEY = frozenset({"entry_id", "employee_id", "scope"})

# The columns a rebuild carries from one generation of the table into the next.
# ``scope`` is not among them: it did not exist before the Inside/Outside
# split, so it is added -- or defaulted to ``both`` -- in its own right.
PREP_SESSION_CARRIED_COLUMNS = (
    "id", "entry_id", "vehicle_id", "employee_id", "status", "started_at",
    "last_event_at", "finished_at", "total_seconds", "created_at", "updated_at",
)


def _table_uniqueness_rules(con, table):
    """Every uniqueness rule the database actually enforces on ``table``.

    Asked of the database rather than read out of the table's CREATE TABLE
    text, because a rule does not have to be written there. An inline
    ``UNIQUE (...)`` is enforced by an implicit index SQLite creates for it
    (``sqlite_autoindex_...``) and a standalone ``CREATE UNIQUE INDEX`` is not
    mentioned in the DDL at all -- yet the two refuse exactly the same inserts.

    Reading only the DDL is how a board went on refusing a crew for good while
    the upgrade reported itself finished: a database whose clocks were keyed by
    a unique index showed no rule whatsoever, and a table with no rule needs no
    rebuild, so the stale index survived every restart and every employee who
    pressed Start on the second clock of a vehicle lost the press.
    """
    rules = set()
    for index in con.execute(f"PRAGMA index_list({table})"):
        name, unique = index[1], index[2]
        if not unique:
            continue  # an ordinary lookup index refuses nothing
        columns = [row[2] for row in con.execute(
            "PRAGMA index_info('%s')" % name.replace("'", "''"))]
        if not columns or any(column is None for column in columns):
            # An index over an expression or a sort order rather than over
            # columns. It constrains at least as much as the model does, so it
            # is stale by definition and only the rebuild can clear it.
            columns = ["<expression>"]
        rules.add(frozenset(column.lower() for column in columns))
    return rules


def _stale_uniqueness_rules(rules):
    """The rules that leave the employee out of the key.

    A rule is current when it covers the vehicle, the employee *and* the clock
    set. One that also covers something more -- a status, say -- is narrower
    than the model but still lets a crew be timed, so it is left alone. What
    cannot be left alone is a rule missing any part of the key, and every one
    of those is stale, including one that merely sits *beside* a current rule:
    asking whether *any* rule is the key let that current rule make the table
    look finished, so the stale rule beside it went on refusing the second
    employee on a vehicle, press after press, and only a manual edit of the
    database ever cleared it.
    """
    return sorted((rule for rule in rules if not PREP_SESSION_UNIQUE_KEY <= rule),
                  key=sorted)


def _prep_sessions_ddl(scratch=None):
    """The statements the model itself uses to define ``prep_sessions``.

    Read from the model rather than written out here a second time, so a
    rebuilt table is by construction the table ``db.create_all()`` builds: the
    columns, the foreign keys and above all the key cannot drift apart from the
    one ``models.PrepSession`` declares, which is the entire reason the upgrade
    rebuilds the table. ``scratch`` is the name to create it under, which is
    what lets the old table stay in place until the copy has succeeded.
    """
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.schema import CreateIndex, CreateTable

    from .models import PrepSession

    table = PrepSession.__table__
    dialect = sqlite.dialect()
    create = str(CreateTable(table).compile(dialect=dialect)).strip()
    if scratch:
        create = re.sub(r"^CREATE TABLE\s+prep_sessions\b", f"CREATE TABLE {scratch}",
                        create, count=1)
    return create, [str(CreateIndex(index).compile(dialect=dialect)).strip()
                    for index in table.indexes]


def _complete_prep_clock(clock, columns, entry_vehicles):
    """Fill in whatever the table being upgraded did not record.

    The rebuilt table is the model's own, so every column it declares is
    written, and the ones the table being upgraded had no column for have to
    arrive with a value: the vehicle comes from the entry the clock was
    recorded against, a clock that has been closed is finished whether or not
    it said so, a clock that has not is running, a clock with no time of its own
    is stamped from whatever time it does have, and a clock with no seconds
    counted has counted none. Without this the copy aborts on a NOT NULL
    constraint, the abort is only logged, and the stale rule stays exactly where
    it was -- a board that stays broken for want of a manual edit, which is what
    this upgrade exists to stop.
    """
    for column in columns:
        clock.setdefault(column, None)
    if not clock.get("vehicle_id"):
        clock["vehicle_id"] = entry_vehicles.get(clock.get("entry_id"))
    if not clock.get("status"):
        clock["status"] = "finished" if clock.get("finished_at") else "running"
    if not clock.get("total_seconds"):
        clock["total_seconds"] = 0
    for column in ("started_at", "last_event_at"):
        if not clock.get(column):
            clock[column] = next(
                (clock[other] for other in ("started_at", "last_event_at",
                                            "finished_at", "created_at")
                 if clock.get(other)), None)


def _copyable_prep_rows(con, old_columns):
    """The existing prep clocks to copy into the rebuilt table, one per key.

    Each row is a mapping of the rebuilt table's column names to values, with
    the columns the table being upgraded did not have filled in (see
    ``_complete_prep_clock``), so a database from before a column existed is
    still carried across whole. The columns are named after the model rather
    than after whatever the table being upgraded happened to hold, so the copy
    always fills every column of the rebuilt table.

    Rows that collide under the new key are collapsed to the earliest of them
    rather than left to abort the copy: the old rule was the one thing standing
    between the table and a crew, so a database carrying it cannot hold two
    clocks for the same employee on the same vehicle in the same set -- and the
    rebuilt table refuses to hold two either. A weaker leftover rule could let
    that happen, though, and an aborted copy would leave the stale rule in place
    for good, which is exactly the failure this upgrade exists to clear. The
    first clock recorded for a key is the one kept, since that is the run the
    service resolved to.
    """
    from flask import current_app

    from .models import PrepSession

    names = list(PrepSession.__table__.columns.keys())
    select_names, select = ["id"], ["rowid AS id" if "id" not in old_columns else "id"]
    for column in PREP_SESSION_CARRIED_COLUMNS:
        if column != "id" and column in old_columns:
            select_names.append(column)
            select.append(column)
    # A clock recorded before the split has no clock set of its own, and counts
    # towards the Inside *and* the Outside total of its day.
    select_names.append("scope")
    select.append(f"COALESCE(NULLIF(scope, ''), '{PrepSession.SCOPE_BOTH}')")

    entry_vehicles = {}
    if "vehicle_id" not in old_columns:
        entry_vehicles = {row[0]: row[1] for row in con.execute(
            "SELECT id, vehicle_id FROM schedule_entries")}

    kept, seen = [], set()
    for row in con.execute(
            f"SELECT {', '.join(select)} FROM prep_sessions ORDER BY id").fetchall():
        clock = dict(zip(select_names, row))
        _complete_prep_clock(clock, names, entry_vehicles)
        key = (clock.get("entry_id"), clock.get("employee_id"), clock["scope"])
        if key in seen:
            current_app.logger.warning(
                "Dropped a duplicate prep clock (vehicle entry %s, employee %s, "
                "%s prep) while upgrading to one clock per employee per clock "
                "set.", key[0], key[1], key[2])
            continue
        seen.add(key)
        kept.append(clock)
    return names, kept


def _upgrade_prep_sessions(con):
    """Let every vehicle carry a clock per employee per clock set.

    Two things can be stale in an existing database:

    - ``prep_sessions.scope`` did not exist before the Inside/Outside split. It
      is added and every clock already recorded is marked ``both``, so it
      counts towards the Inside *and* the Outside total of its day and no
      recorded second is lost or invented.
    - the uniqueness rule is wrong. It used to be ``UNIQUE (entry_id)`` (one
      timer per vehicle), then ``UNIQUE (entry_id, scope)`` (one per vehicle
      *per clock set*, i.e. a crew refused), then ``UNIQUE (entry_id,
      employee_id)`` (one per employee) and is now ``UNIQUE (entry_id,
      employee_id, scope)`` (one per employee per clock set). SQLite cannot drop
      a constraint with ALTER TABLE, so a table with a stale one is rebuilt with
      the model's own current definition, keeping every session, every total and
      every recorded event.

    Whether a rule is stale is decided by the columns it actually covers, not
    by whether it mentions ``scope``: ``UNIQUE (entry_id, scope)`` mentions
    ``scope`` and still holds one clock per vehicle per side, so checking for
    the word let that database skip the upgrade and keep refusing the second
    employee forever. The rule a press needs is "the employee is in the key",
    so a rule is current only when it covers the vehicle, the employee *and*
    the clock set, and anything else is rebuilt.

    The rules are read from the database itself rather than from the table's
    CREATE TABLE text, and every one of them is judged, because a rule need not
    be written there: a standalone ``CREATE UNIQUE INDEX`` is invisible in the
    DDL, and asking whether *any* rule was the key let a stale one sitting
    beside a current one be overlooked. Either way the symptom was the same --
    "the database still holds only one outside clock per vehicle", refusing the
    second employee on a bus on every press and on every restart, with nothing
    but a manual edit of the database to clear it.

    An upgrade that is interrupted between creating the rebuilt table and
    renaming it over the old one leaves that table behind, and the next boot
    then found its own leftover and gave up -- which left the database on the
    stale rule for good, so every second clock of a vehicle (a colleague
    starting the other side, or one person working both) was refused by the
    insert and the press was lost. The scratch table is therefore cleared
    first, so a half-finished run is picked up rather than poisoning the app.
    """
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='prep_sessions'"
    ).fetchone()
    if not row or not row[0]:
        return
    columns = {r[1] for r in con.execute("PRAGMA table_info(prep_sessions)")}
    if "scope" not in columns:
        # SQLite needs a literal default to add a NOT NULL column, and 'both'
        # is exactly what a clock recorded before the split should count as.
        con.execute("ALTER TABLE prep_sessions ADD COLUMN scope VARCHAR(10) "
                    "NOT NULL DEFAULT 'both'")
        con.commit()
        columns.add("scope")

    stale = _stale_uniqueness_rules(_table_uniqueness_rules(con, "prep_sessions"))
    if not stale:
        # No rule to fight at all, or every rule the table carries is already a
        # per-employee key. Either way a crew can be timed and there is nothing
        # to rebuild.
        return

    scratch = "prep_sessions_scoped"
    con.execute("PRAGMA foreign_keys=OFF")
    # Legacy rename keeps prep_session_events pointing at "prep_sessions"
    # instead of rewriting it to the temporary table name.
    con.execute("PRAGMA legacy_alter_table=ON")
    # What an interrupted earlier run left behind. It is never a table worth
    # keeping: the copy below is about to recreate and refill it.
    con.execute(f"DROP TABLE IF EXISTS {scratch}")
    # The model's own definition, foreign keys included, so the rebuild lands
    # on exactly the table db.create_all() would build -- a hand-written copy of
    # it could drift from the key the service relies on, which is the one thing
    # this rebuild is for. Created under a scratch name, so the old table keeps
    # answering reads until the copy below has succeeded.
    create, indexes = _prep_sessions_ddl(scratch)
    con.execute(create)
    names, clocks = _copyable_prep_rows(con, columns)
    if clocks:
        con.executemany(
            f"INSERT INTO {scratch} ({', '.join(names)}) "
            f"VALUES ({', '.join('?' * len(names))})",
            [[clock[name] for name in names] for clock in clocks])
    con.execute("DROP TABLE prep_sessions")
    con.execute(f"ALTER TABLE {scratch} RENAME TO prep_sessions")
    # The indexes the model declares, created once the old table is gone so
    # their names are free and named for the table the scratch table has just
    # been renamed to.
    for statement in indexes:
        con.execute(statement)
    con.execute("PRAGMA legacy_alter_table=OFF")
    con.commit()


def seed_defaults():
    from .models import Location, Employee, VehicleType
    loc = Location.query.filter_by(name="Main Depot").first()
    if not loc:
        loc = Location(name="Main Depot")
        db.session.add(loc)
        db.session.commit()
    if Employee.query.count() == 0:
        db.session.add(Employee(name="User", location_id=loc.id))
        db.session.commit()
    if Vehicle.query.count() == 0:
        _seed_vehicles(loc)
    else:
        _restore_seed_vehicles(loc)
    _seed_default_accounts()


def _seed_default_accounts():
    """Give a database with no accounts at all the three starting logins.

    Only ever runs on an empty accounts table, so an existing installation
    keeps exactly the accounts its Manager created -- and a database that has
    had them all removed on purpose is not quietly given them back.
    """
    if UserAccount.query.count() > 0:
        return
    for username, name, role in DEFAULT_ACCOUNTS:
        account = UserAccount(username=username, name=name, role=role)
        account.set_password(username)
        db.session.add(account)
    db.session.commit()


def _restore_seed_vehicles(loc):
    """Re-activate seeded fleet vehicles that were deactivated by an import.

    Importing a prep report used to set active=False on every vehicle missing
    from the report. Bring those base-fleet units back so the board isn't
    stuck at zero. Vehicles the operator truly retired remain togglable offline;
    the Maintenance unit stays inactive by design.
    """
    changes = False
    for entity, unit, vtype, status, desc, make, model, cap in VEHICLE_SEED_DATA:
        if status == "Maintenance":
            continue
        vehicle = Vehicle.query.filter(
            db.func.lower(Vehicle.unit_number) == str(unit).lower()
        ).filter_by(location_id=loc.id).first()
        if vehicle and not vehicle.active:
            vehicle.active = True
            changes = True
    if changes:
        db.session.commit()


VEHICLE_SEED_DATA = [
    ("UNF", "4301", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4302", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4303", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4304", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4305", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4306", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4307", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "4308", "TRANSITB", "Active", "TRANSIT BUS", "El Dorado ENC", "EZ Rider II", 33),
    ("UNF", "5100", "ADAMINIVAN", "Active", "MINIBUS ADA LIFT", "Ford", "F450", 14),
    ("UNF", "5307", "ADAMINIBUS", "Active", "MINIBUS ADA LIFT", "Starcraft", "Allstar XL 32", 30),
    ("UNF", "5308", "ADAMINIBUS", "Active", "MINIBUS ADA LIFT", "Starcraft", "Allstar XL 32", 30),
    ("ECHO JAX", "7101", "MINIC34", "Active", "MINI COACH", "TEMSA", "TS-30", 34),
    ("ECHO JAX", "8401", "MOTORC", "Active", "MOTORCOACH", "Vanhool", "CX45", 56),
    ("ECHO JAX", "8406", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8414", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8415", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8416", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8437", "ADAMOTORC", "Active", "MOTORCOACH ADA LIFT", "Vanhool", "CX45", 56),
    ("ECHO JAX", "8438", "ADAMOTORC", "Active", "Motorcoach ADA Lift", "VanHool", "CX45", 56),
    ("ECHO JAX", "8439", "ADAMOTORC", "Active", "Motorcoach ADA Lift", "Vanhool", "CX45", 56),
    ("ECHO JAX", "8492", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8493", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8494", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "8495", "MOTORC", "Active", "MOTORCOACH", "VanHool", "CX45", 56),
    ("ECHO JAX", "9101", "SEDAN", "Active", "SEDAN", "Volvo", "S90", 3),
    ("ECHO JAX", "9102", "SEDAN", "Active", "SEDAN", "Volvo", "S90", 3),
    ("ECHO JAX", "9142", "SEDAN", "Active", "SEDAN", "Genesis", "G90", 3),
    ("ECHO JAX", "9145", "SEDAN", "Active", "SEDAN", "Genesis", "G80", 3),
    ("ECHO JAX", "9146", "SEDAN", "Out Of Service", "SEDAN", "Cadillac", "XTS", 3),
    ("ECHO JAX", "9201", "SUVSUB", "Active", "SUV - REID", "CHEVROLET", "SUBURBAN", 6),
    ("ECHO JAX", "9202", "SUVSUB", "Active", "SUV - BUNTEN", "CHEVROLET", "SUBURBAN", 6),
    ("ECHO JAX", "9203", "SUVSUB", "Active", "SUV - RICKETTS", "CHEVROLET", "SUBURBAN", 7),
    ("ECHO JAX", "9204", "SUVSUB", "Active", "SUV - WILLIAMS", "CHEVROLET", "SUBURBAN", 7),
    ("ECHO JAX", "9205", "SUVSUB", "Active", "SUV", "CHEVROLET", "SUBURBAN", 7),
    ("ECHO JAX", "9232", "SUVYUKON", "Active", "SUV", "GMC XL", "YUKON", 7),
    ("ECHO JAX", "9233", "SUVYUKON", "Active", "SUV", "GMC", "Yukon Denali", 5),
    ("ECHO JAX", "9234", "SUVSUB", "Active", "SUV", "Ford", "Expedition", 7),
    ("ECHO JAX", "9301", "Van.", "Active", "", "Ford", "Transit", 14),
    ("MSG", "9313", "ADAMINIVAN", "Active", "", "Ford", "Transit", 12),
    ("ECHO JAX", "9321", "VANSPRINTEREXEC", "Active", "", "Mercedes Benz", "Grech Executive", 13),
    ("ECHO JAX", "9331", "Van.", "Active", "WHITE", "Ford", "Transit", 14),
    ("ECHO JAX", "9332", "VANSPRINTEREXEC", "Active", "MERCEDES SPRINTER", "Mercedes", "Sprinter", 14),
    ("ECHO JAX", "9333", "Van.", "Active", "", "Ford", "Transit", 13),
    ("ECHO JAX", "9334", "TRUCK", "Maintenance", "LUGGAGE VEHICLE ONLY - NO PASSENGERS", "Ford", "Venterra", 0),
    ("ECHO JAX", "9335", "Van.", "Active", "Executive Van", "Ford", "Transit", 13),
    ("ECHO JAX", "9336", "Van.", "Active", "Executive Van", "Ford", "Transit", 13),
    ("MSG", "9341", "Van.", "Active", "Marriott Shuttle", "Ford", "E-350", 14),
    ("MSG", "9342", "Van.", "Active", "Marriott Shuttle", "Ford", "E-350", 14),
    ("ECHO JAX", "9416", "MINIBUS", "Active", "MINI BUS / REAR LUGGAGE", "Ford", "Grech GM33", 28),
    ("ECHO JAX", "9417", "MINIBUS", "Active", "MINI BUS / REAR LUGGAGE", "Ford", "Grech GM 33", 28),
    ("ECHO JAX", "9421", "MINIBUS", "Active", "MINI BUS / REAR LUGGAGE", "GMC", "DIAMOND VIP", 24),
    ("ECHO JAX", "9422", "MINIBUS", "Active", "MINI BUS / REAR LUGGAGE", "Ford", "Grech GM28", 22),
    ("ECHO JAX", "9423", "MINIBUS", "Active", "MINI BUS / REAR LUGGAGE", "Ford", "Grech GM28", 22),
    ("ECHO JAX", "9440", "MINIC40", "Active", "GRECH GM40", "Grech", "GM-40", 40),
]


def _seed_vehicles(loc):
    from .services.vehicles import get_or_create_vehicle_type
    for entity, unit, vtype, status, desc, make, model, cap in VEHICLE_SEED_DATA:
        vt = get_or_create_vehicle_type(vtype)
        v = Vehicle(
            unit_number=unit,
            vehicle_type_id=vt.id if vt else None,
            status=status,
            description=desc or None,
            make=make,
            model=model,
            capacity=cap,
            active=(status != "Maintenance"),
            cleaning_frequency=vt.cleaning_frequency_days if vt else 7,
            location_id=loc.id,
        )
        db.session.add(v)
    db.session.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def current_date(form=None):
    dt = (form or request.args).get("date")
    if dt:
        try:
            return datetime.strptime(dt, "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.today()


def fmt_days_since(days):
    """Human-friendly 'time since' label (e.g. 'today', '3 days ago')."""
    if days is None:
        return "never"
    if days < 1:
        hours = int(days * 24)
        return "today" if hours <= 12 else f"{hours} hours ago"
    d = round(days)
    return "1 day ago" if d == 1 else f"{d} days ago"


def status_indicator(last_washed):
    if not last_washed:
        return ("Never Washed", "status-unknown")
    now = datetime.utcnow()
    recent_days = int(settings.get_setting("recent_days", 2) or 2)
    due_days = int(settings.get_setting("due_soon_days", 7) or 7)
    diff = (now - last_washed).days + (now - last_washed).seconds / 86400.0
    if diff <= recent_days:
        return ("Recently Washed", "status-recent")
    if diff <= due_days:
        return ("Due Soon", "status-due")
    return ("Overdue", "status-overdue")


def _prep_time_sort_key(entry):
    """Sort schedule entries by prep time (earliest first). Entries without a
    prep time are moved after all timed ones, ordered by their import position."""
    import re
    m = re.match(r"\s*(\d{1,2}):(\d{2})", entry.prep_time or "")
    if not m:
        return (1, entry.order_index)
    return (0, int(m.group(1)) * 60 + int(m.group(2)))


def build_schedule_view(sched):
    entries_by_id = {e.id: e for e in sched.entries}
    # original entry id -> replacement entry (the vehicle that took its place)
    replaced_by = {}
    for e in sched.entries:
        if e.is_replacement and e.replacement_of_entry_id in entries_by_id:
            replaced_by[e.replacement_of_entry_id] = e

    rows = []
    for entry in sorted(sched.entries, key=_prep_time_sort_key):
        done, total, pct = sched_svc.entry_progress(entry)
        original = entries_by_id.get(entry.replacement_of_entry_id) \
            if entry.is_replacement else None
        replacer = replaced_by.get(entry.id)
        if replacer is not None:
            done, total, pct = total, total, 100
        skipped = entry.status == "skipped"
        auto_skipped = (
            skipped and entry.skip_reason == vehicles.TRANSIT_SKIP_REASON
        )
        complete = (
            not auto_skipped
            and not skipped
            and (entry.status == "completed" or replacer is not None)
        )
        prep = prep_timer.state(entry)
        task_groups = sched_svc.entry_task_groups(entry)
        rows.append({
            "entry": entry,
            "vehicle": entry.vehicle,
            "done": done,
            "total": total,
            "pct": pct,
            "is_complete": complete,
            "is_auto_skipped": auto_skipped,
            # Skipped rows are reported separately from other work.
            "is_skipped": skipped and replacer is None,
            "indicator": status_indicator(entry.vehicle.last_washed),
            # Transit buses get their own dropdown at the bottom of the board.
            # The skip reason covers a report calling a vehicle TRANSITB while
            # its stored type says something else.
            "is_transit": (vehicles.is_transit_vehicle(entry.vehicle)
                           or entry.skip_reason == vehicles.TRANSIT_SKIP_REASON),
            # The vehicle this row replaced (for replacement entries).
            "replacement_of": original.vehicle if original else None,
            # The vehicle that replaced this row (for replaced originals).
            "replaced_by": replacer.vehicle if replacer else None,
            # Prep timer: Start / Pause / Resume / Done state for this vehicle,
            # one clock set for the inside work and one for the outside work.
            "prep": prep,
            # Which side of the vehicle the task list has been opened up for.
            # The boxes inside a vehicle and the boxes outside it are only
            # worth showing once somebody is actually working that side, so a
            # vehicle nobody has started reads as a clock and a Start button
            # rather than as a wall of boxes that cannot honestly be ticked
            # yet. A group that already has work ticked in it, or a vehicle
            # that is finished, is always shown: those are a record of what
            # happened, not an invitation.
            "tasks_open": _tasks_open(entry, prep, task_groups),
            # The work each of those two clock sets covers, so a row can say
            # what "Inside" and "Outside" mean for this vehicle's type.
            "prep_tasks": settings.get_type_categorized_checklist(
                entry.vehicle.vehicle_type),
            # The tick boxes themselves, split by this vehicle type's own
            # checklist and in the order it is typed, so every surface that
            # renders this row shows the same tasks in the same order.
            "task_groups": task_groups,
        })
    return rows


def _tasks_open(entry, prep, task_groups):
    """Whether each side of a vehicle's task list is on show.

    Which side a ticked task belongs to comes from the vehicle type's own
    checklist, so the two groups here are the ones the row actually shows.
    Mirrored in the browser (applyTaskGroups in app.js) so a Start press opens
    its group of boxes without waiting for a reload.
    """
    finished = entry.status == "completed"
    return {
        scope: (finished
                or prep["scopes"][scope]["status"] != "none"
                or any(t.completed for t in task_groups[scope]))
        for scope in prep_timer.SCOPES
    }


def schedule_counters(rows):
    """Day totals for a list of schedule view rows.

    Transit vehicles auto-skipped on import stay in the skipped count but are
    excluded from the day's work totals. Manual skips remain incomplete and stay
    in the remaining count.
    """
    applicable_rows = [r for r in rows if not r["is_auto_skipped"]]
    total = len(applicable_rows)
    completed = sum(1 for r in applicable_rows if r["is_complete"])
    in_progress = sum(1 for r in applicable_rows
                      if r["entry"].status == "in_progress"
                      and not r["replaced_by"])
    skipped = sum(1 for r in rows if r["is_skipped"])
    return dict(
        total=total,
        completed=completed,
        in_progress=in_progress,
        skipped=skipped,
        remaining=total - completed - in_progress,
        incomplete=total - completed,
        overdue=sum(1 for r in applicable_rows
                    if r["indicator"][0] == "Overdue"),
        overall=round((sum(r["done"] for r in applicable_rows) /
                       (sum(r["total"] for r in applicable_rows) or 1)) * 100)
        if applicable_rows else 0,
    )


def finalize_day(sched, at=None):
    """Finalize a day's schedule: mark it finalized, record when, and store the
    summary. Returns True if this call finalized it, False if it was already
    finalized (no-op). Shared by the End My Day route and the automatic
    11:50 PM end-of-day job so both produce identical summaries.
    """
    if sched.finalized:
        return False
    counts = schedule_counters(build_schedule_view(sched))
    # A finalized day never leaves a prep timer running in the background.
    for entry in sched.entries:
        prep_timer.stop_active(entry)
    sched.finalized = True
    sched.finalized_at = at or datetime.utcnow()
    sched.summary = json.dumps(dict(
        total=counts["total"], completed=counts["completed"],
        incomplete=counts["incomplete"], skipped=counts["skipped"],
        overall=counts["overall"]))
    db.session.commit()
    return True


def _auto_end_day_job(app):
    """Finalize every schedule the employees left open for today. This is the
    scheduled task body: at 11:50 PM each day, any day that wasn't ended by an
    employee is ended automatically. Idempotent, so an employee who already
    clicked End My Day is never touched."""
    count = 0
    with app.app_context():
        unfinalized = DailySchedule.query.filter_by(
            work_date=date.today(), finalized=False).all()
        for sched in unfinalized:
            if finalize_day(sched):
                count += 1
        if count:
            app.logger.info("Auto end-of-day: finalized %s day(s)", count)
    return count


def _auto_end_time():
    """Parse AUTO_END_DAY_TIME (HH:MM, default 23:50) into (hour, minute).
    Invalid values fall back to 23:50."""
    raw = os.environ.get("AUTO_END_DAY_TIME", "23:50").strip()
    try:
        hour, minute = (int(x) for x in raw.split(":", 1))
    except (TypeError, ValueError):
        hour, minute = 23, 50
    return min(max(hour, 0), 23), min(max(minute, 0), 59)


def _start_auto_end_day_scheduler(app):
    """Start the daily 11:50 PM auto end-of-day job (in-process Background
    Scheduler). Skipped while testing so test apps don't spawn threads.

    Time comes from AUTO_END_DAY_TIME (HH:MM, default 23:50) so operators can
    adjust the cutoff without code changes. Multiple admins/workers firing the
    job at the same instant are harmless because finalize_day() is idempotent.

    The scheduler is an optional add-on: if APScheduler isn't installed the
    site keeps serving normally and the auto end-of-day job is simply disabled
    (a warning is logged) instead of the whole app failing to boot.
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        app.logger.warning(
            "APScheduler is not installed; the automatic "
            "end-of-day job is disabled. Run `pip install -r "
            "requirements.txt` to enable it.")
        return None

    app.logger.info("Starting auto end-of-day scheduler")

    hour, minute = _auto_end_time()

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        _auto_end_day_job,
        args=[app],
        trigger=CronTrigger(hour=hour, minute=minute),
        id="auto_end_day",
        replace_existing=True,
    )
    scheduler.start()

    def _shutdown_scheduler():
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass

    atexit.register(_shutdown_scheduler)
    return scheduler


def _nav_links(role):
    """(endpoint, label, icon) tuples for the current role. Rendered by both
    the classic top-bar layout and the new sidepanel layout."""
    links = []
    if role == "driver":
        links.append(("driver_dashboard", "Finished", "✅"))
        links.append(("settings_page", "Settings", "⚙️"))
        links.append(("change_password", "Password", "🔑"))
        return links
    links.append(("dashboard", "Today", "📋"))
    if role == "manager":
        links.append(("vehicle_list", "Vehicles", "🚌"))
    links.append(("import_report", "Import", "📥"))
    links.append(("end_day", "End Day", "🏁"))
    links.append(("history_days", "History", "🕓"))
    links.append(("incidents_list", "Incidents", "⚠️"))
    links.append(("trash_page", "Trash", "🗑️"))
    if role == "manager":
        links.append(("employees_page", "Staff", "👥"))
    links.append(("settings_page", "Settings", "⚙️"))
    links.append(("change_password", "Password", "🔑"))
    return links


def employees_list():
    return Employee.query.filter_by(active=True).all()


def replacement_count_for_date(d):
    start = datetime.combine(d, datetime.min.time())
    end = datetime.combine(d, datetime.max.time())
    return Replacement.query.filter(Replacement.replaced_at >= start,
                                    Replacement.replaced_at <= end).count()


def notes_for_date(d):
    return Note.query.filter_by(work_date=d).all()


def _render_vehicle_detail(vehicle):
    """The vehicle page: its service history plus every prep clock ever run
    on it (Start / Pause / Resume / Done history, the clock set it belongs to
    and the total active prep time for each of Inside and Outside)."""
    history = prep_timer.vehicle_history(vehicle)
    totals = prep_timer.vehicle_scope_totals(vehicle)
    return render_template(
        "vehicle_detail.html", vehicle=vehicle,
        indicator=status_indicator(vehicle.last_washed),
        prep_history=history,
        prep_total_label=timeutils.fmt_duration(
            sum(s["elapsed"] for s in history)),
        prep_inside_label=timeutils.fmt_duration(totals["inside"]),
        prep_outside_label=timeutils.fmt_duration(totals["outside"]))


def build_import_summary(preview, method):
    return (f"{preview['count']} vehicles parsed. "
            f"New: {len(preview['new'])}, "
            f"Updated: {len(preview['updated'])}, "
            f"Removed: {len(preview['removed'])}, "
            f"Replacements: {len(preview['replacements'])}, "
            f"Uncertain: {len(preview['uncertain'])}, "
            f"Transit (auto-skipped): {len(preview.get('transit', []))} "
            f"[{method}]")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register_routes(app):

    @app.template_filter("fromjson")
    def fromjson_filter(value):
        if not value:
            return {}
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}

    @app.template_filter("incident_severity_class")
    def incident_severity_class_filter(value):
        return SEVERITY_CLASSES.get(value, "muted")

    @app.template_filter("incident_status_class")
    def incident_status_class_filter(value):
        return STATUS_CLASSES.get(value, "muted")

    @app.template_filter("strip_res")
    def strip_res_filter(value):
        """Strip reservation ('Res # ...') notes so they don't clutter the
        dashboard board. Other operational notes are preserved."""
        if not value:
            return value
        return re.sub(r"(?i)\s*\bRes\s*#\s*[\w.*-]+", "", str(value)).strip()

    # --- Eastern Time display helpers -----------------------------------
    # Timestamps are stored timezone-aware; people always read 12-hour AM/PM.

    @app.template_filter("et_time")
    def et_time_filter(value, seconds=False):
        """A stored timestamp as 12-hour Eastern time, e.g. 4:05 PM."""
        return timeutils.fmt_time(value, seconds)

    @app.template_filter("et_datetime")
    def et_datetime_filter(value):
        """A stored timestamp as 'Sep 25, 4:05 PM' Eastern time."""
        return timeutils.fmt_datetime(value)

    @app.template_filter("duration")
    def duration_filter(value):
        """Seconds as a readable duration, e.g. 1h 05m."""
        return timeutils.fmt_duration(value)

    @app.template_filter("prep_time")
    def prep_time_filter(value):
        """A report prep/pickup time as 12-hour Eastern time. The stored value
        is left untouched, so the 24-hour time on the source report survives."""
        return timeutils.prep_time_label(value)

    def _signed_in_account():
        """The account row behind this session, or None when nobody is signed
        in. Re-read on every request so removing an account takes effect at
        once instead of at the next sign-in."""
        account_id = session.get("user_id")
        if not account_id:
            return None
        return UserAccount.query.get(account_id)

    def _employee_for_account(name, employee_id=None):
        """The staff record an Employee account's work is recorded against.

        Uses the one the Manager picked; otherwise matches one already on file
        under that name, so re-adding somebody does not duplicate them; and
        otherwise adds them, because an account with nobody to work as could
        never check off a task.
        """
        if employee_id:
            emp = Employee.query.get(employee_id)
            if emp and emp.active:
                return emp
        existing = Employee.query.filter(
            db.func.lower(Employee.name) == (name or "").strip().lower()).first()
        if existing is not None:
            return existing
        emp = Employee(name=name, location_id=vehicles.default_location().id)
        db.session.add(emp)
        db.session.flush()
        return emp

    def _bound_employee(account):
        """The staff record an individual account is tied to, when it is still
        an active employee. None for Managers and Drivers, who have no board
        work of their own to record."""
        if account is None or account.role != UserAccount.ROLE_EMPLOYEE:
            return None
        if not account.employee_id:
            return None
        emp = Employee.query.get(account.employee_id)
        return emp if emp and emp.active else None

    def _ensure_bound_employee(account):
        """The staff record this Employee account is recorded against, tying
        the account to one if it is not already.

        The account is the person's identity: signing in *is* saying who they
        are, so an account that somehow has no staff record behind it is given
        one -- the one already on file under their name, or a new record -- and
        the link is saved. From then on their tasks and timers are recorded
        against them without anything having to be picked on the way in.
        """
        if account is None or account.role != UserAccount.ROLE_EMPLOYEE:
            return None
        emp = _bound_employee(account)
        if emp is not None:
            return emp
        emp = _employee_for_account(account.name, account.employee_id)
        account.employee = emp
        return emp

    def _sign_in(account):
        """Start a session for ``account``.

        The role is stored alongside the account id so the rest of the request
        handling can still ask "is this an employee?" the way it always has,
        while the account id is what actually identifies the person. Signing in
        also settles who they are: an Employee account is tied to its own staff
        record here, so the board opens with their name on it and every task
        they check and every clock they run is recorded against them.
        """
        session.clear()
        session["user_id"] = account.id
        session["user"] = account.role
        session["username"] = account.name
        emp = _ensure_bound_employee(account)
        if emp is not None:
            session["employee_id"] = emp.id
            session["employee_name"] = emp.name
        account.last_login_at = datetime.utcnow()
        db.session.commit()

    @app.context_processor
    def inject_globals():
        account = _signed_in_account()
        user = account.role if account else None
        # The signed-in person's own staff record, which is what their board
        # work is recorded against. A record taken off the staff list since they
        # signed in does not come back with the session.
        emp = _bound_employee(account)
        emp_id = emp.id if emp is not None else None
        # Resolve the signed-in user's own stored theme ("on"|"off"|"system"|
        # "futuristic"|"halloween"|"bloomberg"|"retro"|"holographic"|
        # "synthwave"|"cosmos"|"cyberpunk"|"aurora"|"ocean"|"crystal"|
        # "matrix"|"dunes") to the value used in the data-theme attribute.
        # CSS defines dark styles for "dark" and the special themes, so "on"
        # must map to "dark"; "system" is resolved live by the browser.
        raw_dark_mode = settings.get_user_theme(user, emp_id)
        resolved_dark_mode = "dark" if raw_dark_mode == "on" else raw_dark_mode
        role = user if user in VALID_ROLES else UserAccount.ROLE_EMPLOYEE
        return {
            "today": date.today,
            "app_name": "Detailing Operations Dashboard",
            "current_role": role,
            "current_user": account.name if account else None,
            "current_account": account,
            "current_employee": emp,
            # An Employee account whose staff record has since been taken off
            # the list: they can see the board but there is nobody for their
            # work to be recorded against, so the board says so.
            "employee_record_removed": bool(
                user == UserAccount.ROLE_EMPLOYEE and emp is None),
            "dark_mode": resolved_dark_mode,
            "layout": settings.get_user_layout(user, emp_id),
            "nav_links": _nav_links(role),
            # The server's clock, so live timers in the browser can correct for
            # a skewed device clock instead of drifting.
            "server_epoch": timeutils.epoch_ms(),
        }

    @app.before_request
    def require_login():
        """Every page except login/logout requires a signed-in account."""
        if request.endpoint in ("static", "login", "logout"):
            return None
        account = _signed_in_account()
        if account is None:
            session.clear()
            return redirect(url_for("login"))
        if not account.active:
            # The Manager removed this account while the person was signed in.
            session.clear()
            flash("Your account has been removed. Please contact your Manager.",
                  "error")
            return redirect(url_for("login"))
        # session["user"] is a copy of the account's role. Trust the row, not
        # the cookie, so a tampered or stale session cannot widen access.
        if session.get("user") != account.role:
            session["user"] = account.role
        user = account.role
        # Drivers only see the finished-vehicles screen (plus their own Settings
        # and Password pages and the incident report submission flow).
        if user == "driver" and request.endpoint not in (
                "driver_dashboard", "settings_page", "change_password",
                "incidents_list", "incident_new", "incident_detail",
                "incident_pdf", "incident_photo"):
            return redirect(url_for("driver_dashboard"))
        # Vehicles, Staff and operational Settings are manager-only.
        if user != "manager" and request.endpoint in MANAGER_ONLY_ENDPOINTS:
            return redirect(url_for("dashboard"))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if _signed_in_account() is not None:
            return redirect(url_for("splash"))
        if request.method == "POST":
            username = (request.form.get("username") or "").strip().lower()
            password = request.form.get("password") or ""
            account = UserAccount.query.filter_by(username=username).first()
            # A removed account is refused exactly like a wrong password, so
            # the login page never confirms that a username exists.
            if account and account.active and account.check_password(password):
                _sign_in(account)
                flash(f"Welcome, {account.name}", "success")
                return redirect(url_for("splash"))
            flash("Invalid username or password", "error")
            return redirect(url_for("login"))
        return render_template("login.html")

    @app.route("/splash")
    def splash():
        """Fullscreen intro animation played after login before the dashboard."""
        return render_template("splash.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("You have been logged out", "success")
        return redirect(url_for("login"))

    @app.route("/account/password", methods=["GET", "POST"])
    def change_password():
        """Let a signed-in person change their own password.

        The Manager sets the first one; this is how an Employee or Driver takes
        it over afterwards. Back on the dashboard when it is done.
        """
        account = _signed_in_account()
        if account is None:
            return redirect(url_for("login"))
        if request.method == "POST":
            current = request.form.get("current_password") or ""
            new = request.form.get("new_password") or ""
            confirm = request.form.get("confirm_password") or ""
            if not account.check_password(current):
                flash("Your current password is not correct", "error")
            elif len(new) < UserAccount.MIN_PASSWORD_LENGTH:
                flash("Your new password must be at least "
                      f"{UserAccount.MIN_PASSWORD_LENGTH} characters", "error")
            elif new != confirm:
                flash("The new passwords do not match", "error")
            else:
                account.set_password(new)
                db.session.commit()
                flash("Your password has been changed", "success")
                return redirect(role_home(account.role))
        return render_template("change_password.html")

    def _acting_employee_id():
        """Who is driving a board action: the employee the board posted (the
        board sends the signed-in employee's id), or the signed-in employee
        when the board didn't send one. A manager has no employee id, so the
        timer is recorded against whoever did the work."""
        posted = (request.json.get("employee_id") if request.is_json
                  else request.form.get("employee_id"))
        if posted:
            return posted
        return session.get("employee_id")

    @app.route("/start-work", methods=["POST"])
    def start_work():
        """Backwards-compatible alias for the Start step of the prep workflow:
        it begins the vehicle's prep timer and marks it in progress."""
        if session.get("user") != "employee":
            return jsonify(ok=False, error="Manager view is read-only"), 403
        emp_id = _acting_employee_id()
        entry_id = request.json.get("entry_id") if request.is_json else request.form.get("entry_id")
        if not emp_id or not entry_id:
            return jsonify(ok=False, error="Missing employee_id or entry_id"), 400
        emp = Employee.query.get(int(emp_id))
        entry = ScheduleEntry.query.get(int(entry_id))
        if not emp or not entry:
            return jsonify(ok=False, error="Invalid employee or entry"), 404
        return _prep_run(entry.id, "start")

    def _prep_run(entry_id, action):
        """Run one Start / Pause / Resume / Done step and answer with JSON.

        Every clock belongs to one of a vehicle's two clock sets (Inside /
        Outside) and to one employee, so a step always acts on one session: the
        one the button belongs to (``session_id``) or the acting employee's own
        in the named ``scope``. Invalid actions (starting a clock you have
        already started, finishing one that was never started, ...) are rejected
        with an explanation and the row keeps its current state, so a mis-click
        can never corrupt the record.

        A step that loses a race for the database is answered in the same shape
        rather than raised: a bare 500 is an HTML page, which the board cannot
        read, so the employee who pressed got nothing but "Could not start this
        vehicle. Try again." and no idea whether their clock had started.
        """
        if session.get("user") != "employee":
            return jsonify(ok=False, error="Manager view is read-only"), 403
        entry = ScheduleEntry.query.get(entry_id)
        if entry is None:
            return jsonify(ok=False, error="Vehicle not found"), 404
        employee_id = _acting_employee_id()
        source = request.json if request.is_json else request.form
        session_id = source.get("session_id") or None
        scope = prep_timer.normalize_scope(source.get("scope"))
        try:
            if action == "start":
                started = prep_timer.start(entry, employee_id, scope=scope)
            elif action == "pause":
                started = prep_timer.pause(entry, employee_id, scope=scope,
                                           session_id=session_id)
            elif action == "resume":
                started = prep_timer.resume(entry, employee_id, scope=scope,
                                             session_id=session_id)
            else:
                started = prep_timer.finish(entry, employee_id, scope=scope,
                                            session_id=session_id)
                # Done closes out the employee who pressed it. The vehicle is
                # only finished on the board once nobody is still working on
                # it *and* both of its clock sets have been finished, so
                # finishing one side never completes the whole vehicle: the
                # other side still has to be started and finished. This is what
                # keeps a crew, who each press Done in their own time, from
                # closing the row halfway through the job.
                if (not prep_timer.active_sessions_for(entry)
                        and prep_timer.all_scopes_finished(entry)):
                    sched_svc.complete_entry(entry)
        except prep_timer.PrepTimerError as err:
            # A refused press still answers with the vehicle's real state, so
            # the board repaints the row instead of leaving the button that was
            # just refused on screen next to a clock that has moved on.
            return jsonify(ok=False, error=err.message,
                           state=prep_timer.state(entry),
                           entry_completed=entry.status == "completed"), err.code
        except _DATABASE_BUSY_ERRORS:
            # Another employee's press, page load or timer re-sync held the
            # write lock. Nothing was written, so answer like any other refused
            # step: a reason the employee can act on, and a row they can press
            # again. Roll back first so the released session is clean.
            db.session.rollback()
            return jsonify(ok=False, error=(
                "The board is busy with another employee's action. "
                "Press the button again in a moment.")), 503
        state = prep_timer.state(entry)
        payload = {
            "ok": True,
            "action": action,
            "scope": scope,
            "state": state,
            "unit": entry.vehicle.unit_number,
            "vehicle": entry.vehicle.unit_number,
            "entry_status": entry.status,
            "entry_completed": entry.status == "completed",
            "still_working": bool(prep_timer.active_sessions_for(entry)),
            # The sides of the vehicle still to be finished, so a press that
            # does not close the row can say which side is outstanding.
            "scopes_outstanding": list(state.get("scopes_outstanding") or []),
        }
        # The clock the press actually moved, so the "Now Working" card ticks
        # the employee's own time rather than the vehicle's total.
        worker = next((w for w in state["workers"]
                       if started is not None and w["session_id"] == started.id),
                      None)
        if worker is not None:
            payload["worker"] = worker
            payload["employee"] = worker["employee"]
            payload["initials"] = worker["initials"]
        if action == "done":
            sched = db.session.get(DailySchedule, entry.schedule_id)
            done, total, pct = sched_svc.entry_progress(entry)
            payload["progress"] = {"done": done, "total": total, "pct": pct}
            payload["incomplete"] = sched_svc.entry_incomplete_labels(entry)
            # Finishing a vehicle changes the day totals, so hand the client
            # freshly calculated counters for the stat tiles.
            counters = schedule_counters(build_schedule_view(sched))
            totals = prep_timer.scope_totals(sched)
            counters["prep_total"] = timeutils.fmt_duration(
                prep_timer.total_active_seconds(sched))
            counters["prep_inside"] = timeutils.fmt_duration(totals["inside"])
            counters["prep_outside"] = timeutils.fmt_duration(totals["outside"])
            payload["counters"] = counters
        return jsonify(payload)

    @app.route("/entry/<int:entry_id>/prep/start", methods=["POST"],
               endpoint="prep_start")
    def prep_start(entry_id):
        return _prep_run(entry_id, "start")

    @app.route("/entry/<int:entry_id>/prep/pause", methods=["POST"],
               endpoint="prep_pause")
    def prep_pause(entry_id):
        return _prep_run(entry_id, "pause")

    @app.route("/entry/<int:entry_id>/prep/resume", methods=["POST"],
               endpoint="prep_resume")
    def prep_resume(entry_id):
        return _prep_run(entry_id, "resume")

    @app.route("/entry/<int:entry_id>/prep/done", methods=["POST"],
               endpoint="prep_done")
    def prep_done(entry_id):
        return _prep_run(entry_id, "done")

    @app.route("/prep/active")
    def prep_active():
        """Timer state for every vehicle on the board being viewed.

        ``sessions`` is keyed by board entry (the vehicle, with its total and
        the state of each of its two clock sets under ``scopes``) and
        ``workers`` by employee, because a vehicle can be worked by a whole
        crew at once and each "Now Working" card needs its own clock.

        The board calls this when the tab comes back to the foreground so a
        timer that ran while the tab was hidden re-syncs to the server clock
        instead of drifting.
        """
        # The board may be showing a past/future date, so use the date it asks
        # about rather than always today.
        d = current_date()
        sched = sched_svc.get_or_create_schedule(
            d=d, location=vehicles.default_location())
        return jsonify(
            ok=True,
            date=d.isoformat(),
            now=timeutils.epoch_ms(),
            sessions={str(e.id): prep_timer.state(e) for e in sched.entries},
            workers={str(emp_id): worker for emp_id, worker
                     in prep_timer.active_worker_states(sched).items()})

    @app.route("/")
    def dashboard():
        from datetime import timedelta
        # Accept ?date=YYYY-MM-DD, default to today
        date_str = request.args.get("date", "").strip()
        try:
            view_date = date.fromisoformat(date_str) if date_str else date.today()
        except ValueError:
            view_date = date.today()

        loc = vehicles.default_location()
        sched = sched_svc.get_or_create_schedule(d=view_date, location=loc)
        has_import = PrepReportImport.query.filter_by(
            applied=True, schedule_date=sched.work_date).first() is not None
        # Show the board if a prep report was imported OR vehicles were added
        # manually, so operators can always see (and work) the day's list.
        rows = build_schedule_view(sched) if (has_import or sched.entries) else []

        counts = schedule_counters(rows)
        total = counts["total"]
        completed = counts["completed"]
        in_progress = counts["in_progress"]
        skipped = counts["skipped"]
        remaining = counts["remaining"]
        overall = counts["overall"]
        overdue = counts["overdue"]
        replacements = replacement_count_for_date(sched.work_date)

        # Build date navigation links (today, tomorrow, +2 days)
        nav_dates = []
        for offset in range(3):
            d = date.today() + timedelta(days=offset)
            nav_dates.append({
                "date": d,
                "label": ["Today", "Tomorrow", "+2 Days"][offset],
                "iso": d.isoformat(),
                "active": view_date == d,
            })

        # Check which dates have imports applied
        imported_dates = set()
        for imp in PrepReportImport.query.filter_by(applied=True).all():
            if imp.schedule_date:
                imported_dates.add(imp.schedule_date.isoformat())

        filters = {
            "unit": request.args.get("unit", "").strip(),
            "type": request.args.get("type", "").strip(),
            "route": request.args.get("route", "").strip(),
            "status": request.args.get("status", "").strip(),
        }
        q_unit = filters["unit"].lower()
        q_type = filters["type"].lower()
        q_route = filters["route"].lower()
        q_status = filters["status"]

        frows = []
        for r in rows:
            v = r["vehicle"]
            if q_unit and q_unit not in v.unit_number.lower():
                continue
            if q_type and not (v.vehicle_type and
                               q_type in v.vehicle_type.name.lower()):
                continue
            if q_route and q_route not in (v.route or "").lower():
                continue
            if q_status and q_status != r["entry"].status:
                continue
            frows.append(r)

        # Transit buses (TRANSITB) are washed by another crew. They stay on the
        # board but in their own dropdown at the bottom, not the main work list.
        main_rows, transit_rows = [], []
        for r in frows:
            (transit_rows if r["is_transit"] else main_rows).append(r)

        types = sorted({v.vehicle_type.name for v in Vehicle.query
                        if v.vehicle_type and v.vehicle_type.name})

        # Build employee current vehicle map for active employees today.
        # Clear any assignments left over from a previous day (employees who
        # forgot to hit Done) so the "Now Working" board doesn't go stale.
        sched_svc.clear_stale_current_vehicles()
        # A vehicle can be worked by several employees at once, so each person
        # gets the clock of *their* session, not the vehicle's total.
        prep_by_employee = prep_timer.active_worker_states(sched)
        active_employees = []
        for emp in Employee.query.filter_by(active=True).order_by(Employee.name).all():
            cv = emp.current_vehicle
            active_employees.append({
                "id": emp.id, "name": emp.name, "initials": emp.initials,
                "current_vehicle": cv.unit_number if cv else None,
                # Live prep timer for whatever they are working on right now.
                "prep": prep_by_employee.get(emp.id) if cv else None,
            })
        prep_total = prep_timer.total_active_seconds(sched)
        prep_scopes = prep_timer.scope_totals(sched)

        return render_template(
            "dashboard.html",
            rows=main_rows, transit_rows=transit_rows,
            all_rows=rows, sched=sched,
            total=total, completed=completed, in_progress=in_progress,
            skipped=skipped, remaining=remaining, overall=overall, overdue=overdue,
            replacements=replacements, types=types, filters=filters,
            employees=employees_list(),
            nav_dates=nav_dates, view_date=view_date,
            imported_dates=imported_dates,
            active_employees=active_employees,
            prep_total=prep_total,
            prep_total_label=timeutils.fmt_duration(prep_total),
            prep_inside_label=timeutils.fmt_duration(prep_scopes["inside"]),
            prep_outside_label=timeutils.fmt_duration(prep_scopes["outside"]),
            prep_running=sum(r["prep"]["running_count"] for r in rows),
        )
    @app.route("/driver")
    def driver_dashboard():
        """Driver screen: read-only list of finished vehicles for today and next two days."""
        loc = vehicles.default_location()
        days = []
        for offset in range(3):
            d = date.today() + timedelta(days=offset)
            sched = sched_svc.get_or_create_schedule(d=d, location=loc)
            rows = []
            for entry in sorted(sched.entries, key=_prep_time_sort_key):
                if entry.status != "completed":
                    continue
                done, total, pct = sched_svc.entry_progress(entry)
                workers = sorted({t.employee.initials for t in entry.tasks
                                  if t.completed and t.employee})
                finished_at = max((t.completed_at for t in entry.tasks
                                   if t.completed_at), default=None)
                rows.append({
                    "entry": entry,
                    "vehicle": entry.vehicle,
                    "done": done,
                    "total": total,
                    "pct": pct,
                    "workers": workers,
                    "finished_at": finished_at,
                })
            days.append({"date": d, "rows": rows})
        return render_template("driver.html", days=days)

    @app.route("/vehicles")
    def vehicle_list():
        loc = vehicles.default_location()
        vq = Vehicle.query.filter_by(location_id=loc.id).all()
        return render_template("vehicles.html", vehicles=vq)

    @app.route("/vehicles/new", methods=["GET", "POST"])
    def vehicle_new():
        if request.method == "POST":
            unit = request.form.get("unit_number")
            if not unit:
                flash("Unit number is required", "error")
                return redirect(url_for("vehicle_new"))
            vehicle, _ = vehicles.find_or_create_vehicle(
                unit,
                vehicle_type=request.form.get("vehicle_type") or None,
                route=request.form.get("route") or None,
                notes=request.form.get("notes") or None,
                location_id=vehicles.default_location().id,
            )
            vehicle.status = request.form.get("status") or "Active"
            vehicle.make = request.form.get("make") or None
            vehicle.model = request.form.get("model") or None
            vehicle.description = request.form.get("description") or None
            try:
                vehicle.capacity = int(request.form.get("capacity", 0))
            except (ValueError, TypeError):
                pass
            db.session.commit()
            flash(f"Vehicle {vehicle.unit_number} created", "success")
            return redirect(url_for("vehicle_detail", vehicle_id=vehicle.id))
        return render_template("vehicle_form.html")

    @app.route("/vehicles/<int:vehicle_id>")
    def vehicle_detail(vehicle_id):
        vehicle = Vehicle.query.get_or_404(vehicle_id)
        return _render_vehicle_detail(vehicle)

    @app.route("/vehicles/<int:vehicle_id>/edit", methods=["GET", "POST"])
    def vehicle_edit(vehicle_id):
        vehicle = Vehicle.query.get_or_404(vehicle_id)
        if request.method == "POST":
            vehicle.unit_number = request.form.get("unit_number", vehicle.unit_number)
            vehicle.route = request.form.get("route") or None
            vehicle.status = request.form.get("status") or "Active"
            vehicle.notes = request.form.get("notes") or None
            vehicle.make = request.form.get("make") or None
            vehicle.model = request.form.get("model") or None
            vehicle.description = request.form.get("description") or None
            vehicle.active = request.form.get("active") == "on"
            vt_name = request.form.get("vehicle_type")
            if vt_name:
                vehicle.vehicle_type = vehicles.get_or_create_vehicle_type(vt_name)
            try:
                vehicle.cleaning_frequency = int(
                    request.form.get("cleaning_frequency", vehicle.cleaning_frequency))
                vehicle.capacity = int(request.form.get("capacity", 0))
            except (ValueError, TypeError):
                pass
            db.session.commit()
            flash("Vehicle updated", "success")
            return redirect(url_for("vehicle_detail", vehicle_id=vehicle.id))
        return render_template("vehicle_form.html", vehicle=vehicle)

    @app.route("/vehicles/<int:vehicle_id>/toggle-active", methods=["POST"])
    def vehicle_toggle_active(vehicle_id):
        vehicle = Vehicle.query.get_or_404(vehicle_id)
        vehicle.active = not vehicle.active
        db.session.commit()
        return redirect(url_for("vehicle_detail", vehicle_id=vehicle.id))

    @app.route("/import", methods=["GET", "POST"])
    def import_report():
        if request.method == "POST":
            file = request.files.get("pdf")
            if not file or not file.filename:
                flash("Please choose a prep report PDF", "error")
                return redirect(url_for("import_report"))
            data = file.read()
            # Which day to import for (default today)
            sched_date = request.form.get("sched_date", "").strip()
            try:
                sched_dt = date.fromisoformat(sched_date) if sched_date else date.today()
            except ValueError:
                sched_dt = date.today()
            from app.services.pdf_parser import parse_prep_report
            from app.services import schedule as ss
            parsed, method, warnings = parse_prep_report(data, file.filename)
            preview = ss.build_preview(parsed, location=vehicles.default_location())
            summary = build_import_summary(preview, method)
            file_path = _save_uploaded_pdf(data, file.filename)
            imp = vehicles.record_import(
                file.filename, applied=False, summary=summary,
                preview=preview, method=method, file_path=file_path,
                employee_id=_parse_imported_by(request.form.get("imported_by")))
            return render_template(
                "import_preview.html",
                preview=preview, warnings=warnings, method=method,
                import_id=imp.id,
                sched_date=sched_dt.isoformat(),
                employees=employees_list(),
                imported_by_employee_id=imp.employee_id)
        return render_template("import.html", import_dates=_import_date_options(),
                               employees=employees_list())

    @app.route("/import/<int:import_id>/view")
    def import_view(import_id):
        imp = PrepReportImport.query.get_or_404(import_id)
        if not imp.file_path or not os.path.isfile(imp.file_path):
            flash("Original PDF file is no longer available", "error")
            return redirect(url_for("history_days"))
        return render_template(
            "import_view.html", imp=imp, pdf_url=url_for("import_pdf", import_id=import_id))

    @app.route("/import/<int:import_id>/pdf")
    def import_pdf(import_id):
        imp = PrepReportImport.query.get_or_404(import_id)
        if not imp.file_path or not os.path.isfile(imp.file_path):
            flash("Original PDF file is no longer available", "error")
            return redirect(url_for("history_days"))
        return send_file(imp.file_path, mimetype="application/pdf")

    @app.route("/import/<int:import_id>/apply", methods=["POST"])
    def import_apply(import_id):
        imp = PrepReportImport.query.get_or_404(import_id)
        preview = json.loads(imp.preview_json)
        sched_date_str = request.form.get("sched_date", "").strip()
        try:
            sched_dt = date.fromisoformat(sched_date_str) if sched_date_str else date.today()
        except ValueError:
            sched_dt = date.today()
        sched = sched_svc.apply_import(
            preview,
            location=vehicles.default_location(),
            employee_id=request.form.get("employee_id") or None,
            source="import",
            schedule_date=sched_dt)
        imp.applied = True
        imp.applied_at = datetime.utcnow()
        imp.schedule_date = sched.work_date
        imported_by_raw = request.form.get("imported_by")
        if imported_by_raw is not None:
            imp.employee_id = _parse_imported_by(imported_by_raw)
        db.session.commit()
        flash(f"Prep report applied for {sched_dt.strftime('%b %d')}. Work list updated.", "success")
        return redirect(url_for("dashboard", date=sched_dt.isoformat()))

    @app.route("/task/<int:entry_id>/<path:task_name>", methods=["POST"])
    def task_toggle(entry_id, task_name):
        if session.get("user") != "employee":
            return jsonify(ok=False, error="Manager view is read-only"), 403
        checked = request.form.get("checked") == "true"
        # The board sends the signed-in employee's id, and the session already
        # knows it too -- so a request without one is still recorded against
        # the person who actually pressed the tick.
        emp = _acting_employee_id()
        task = sched_svc.toggle_task(entry_id, task_name, checked, emp)
        done = total = pct = None
        status = None
        if task:
            done, total, pct = sched_svc.entry_progress(task.entry)
            # A vehicle is finished by its last task, so the status changes on
            # exactly the press that completes it. Hand it back so the client can
            # tick the card from what was recorded, in either direction, instead
            # of waiting for a reload to find out.
            status = task.entry.status
        return jsonify(ok=True, done=done, total=total, pct=pct, status=status,
                       entry_status=status)

    @app.route("/entry/<int:entry_id>/skip", methods=["POST"])
    def entry_skip(entry_id):
        wants_json = request.headers.get("Accept", "") == "application/json"
        if session.get("user") != "employee":
            if wants_json:
                return jsonify(ok=False, error="Manager view is read-only"), 403
            flash("Manager view is read-only", "error")
            return redirect(url_for("dashboard"))
        entry = ScheduleEntry.query.get_or_404(entry_id)
        reason = request.form.get("reason", "").strip()
        if not reason:
            reason = entry.skip_reason or ""
        # A skipped vehicle is not being worked on, so its clock stops here.
        prep_timer.stop_active(entry, employee_id=_acting_employee_id())
        status = sched_svc.set_entry_skipped(entry, skipped=True, reason=reason)
        if wants_json:
            # Skipping never completes a vehicle, so hand the client the
            # recalculated day totals to keep the stat tiles honest.
            sched = db.session.get(DailySchedule, entry.schedule_id)
            counts = schedule_counters(build_schedule_view(sched))
            return jsonify(ok=True, unit=entry.vehicle.unit_number, reason=reason,
                           counters=counts)
        flash(f"Vehicle {entry.vehicle.unit_number} marked as skipped "
              f"(not counted as completed)", "success")
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/entry/<int:entry_id>/unskip", methods=["POST"])
    def entry_unskip(entry_id):
        if session.get("user") != "employee":
            flash("Manager view is read-only", "error")
            return redirect(url_for("dashboard"))
        entry = ScheduleEntry.query.get_or_404(entry_id)
        sched_svc.set_entry_skipped(entry, skipped=False)
        flash(f"Vehicle {entry.vehicle.unit_number} un-skipped", "success")
        # Drop back to the same row after the full reload instead of the top.
        ref = (request.referrer or url_for("dashboard")).split("#", 1)[0]
        return redirect(f"{ref}#row-{entry_id}")

    @app.route("/entry/<int:entry_id>/complete", methods=["POST"])
    def entry_complete(entry_id):
        if session.get("user") != "employee":
            return jsonify(ok=False, error="Manager view is read-only"), 403
        entry = ScheduleEntry.query.get_or_404(entry_id)
        # A vehicle finished any other way still closes out its prep timer, so
        # no timer is ever left running in the background.
        prep_timer.stop_active(entry, employee_id=_acting_employee_id())
        sched_svc.complete_entry(entry)
        done, total, pct = sched_svc.entry_progress(entry)
        incomplete = sched_svc.entry_incomplete_labels(entry)
        # Finishing a vehicle off early (with the boxes not all ticked) still
        # moves the day totals, so hand the client freshly calculated counters
        # for the stat tiles, and name the tasks it left undone so the row can
        # say so rather than looking like a vehicle that was ticked off in full.
        sched = db.session.get(DailySchedule, entry.schedule_id)
        return jsonify(ok=True, done=done, total=total, pct=pct,
                       unit=entry.vehicle.unit_number,
                       entry_status=entry.status,
                       entry_completed=entry.status == "completed",
                       incomplete=incomplete,
                       counters=schedule_counters(build_schedule_view(sched)),
                       state=prep_timer.state(entry))

    @app.route("/schedule/<int:entry_id>/replace", methods=["POST"])
    def entry_replace(entry_id):
        entry = ScheduleEntry.query.get_or_404(entry_id)
        repl_unit = request.form.get("replacement_unit", "").strip()
        reason = request.form.get("reason", "")
        if not repl_unit:
            flash("Replacement vehicle unit is required", "error")
            return redirect(url_for("dashboard"))
        vehicle, _ = vehicles.find_or_create_vehicle(
            repl_unit, location_id=vehicles.default_location().id)
        employee_id = request.form.get("employee_id") or None
        sched_svc.move_entry_to_replacement(
            entry.schedule, entry, vehicle, reason, employee_id)
        flash(f"Vehicle {entry.vehicle.unit_number} replaced by "
              f"{vehicle.unit_number}", "success")
        return redirect(url_for("dashboard"))

    @app.route("/schedule/add", methods=["POST"])
    def schedule_add():
        sched_date = request.form.get("date", "").strip()
        try:
            sched_dt = date.fromisoformat(sched_date) if sched_date else date.today()
        except ValueError:
            sched_dt = date.today()
        unit = request.form.get("unit_number", "").strip()
        if not unit:
            flash("Unit number is required", "error")
            return redirect(url_for("dashboard", date=sched_dt.isoformat()))
        loc = vehicles.default_location()
        vehicle, _ = vehicles.find_or_create_vehicle(
            unit,
            vehicle_type=request.form.get("vehicle_type") or None,
            route=request.form.get("route") or None,
            location_id=loc.id,
        )
        # If the vehicle already exists, surface any changes the operator entered.
        if request.form.get("route"):
            vehicle.route = request.form["route"]
        vehicle.status = request.form.get("status") or vehicle.status or "Active"
        vehicle.active = True
        prep_time = request.form.get("prep_time") or None
        pickup_time = request.form.get("pickup_time") or None
        driver_code = request.form.get("driver_code") or None
        sched = sched_svc.get_or_create_schedule(d=sched_dt, location=loc)
        order = (max((e.order_index for e in sched.entries), default=-1) + 1)
        entry = sched_svc.ensure_entry(
            sched, vehicle, order_index=order, prep_time=prep_time,
            pickup_time=pickup_time, driver_code=driver_code)
        db.session.commit()
        flash(f"Vehicle {vehicle.unit_number} added to {sched_dt.strftime('%b %d')}'s board",
              "success")
        return redirect(url_for("dashboard", date=sched_dt.isoformat()))

    @app.route("/notes/<path:date>", methods=["POST"])
    def add_note(date):
        text = request.form.get("text", "").strip()
        employee_id = request.form.get("employee_id") or None
        if text:
            d = datetime.strptime(date, "%Y-%m-%d").date()
            sess = DailySchedule.query.filter_by(
                work_date=d,
                location_id=vehicles.default_location().id).first()
            note = Note(work_date=d, text=text, employee_id=employee_id,
                        schedule_id=sess.id if sess else None)
            db.session.add(note)
            db.session.commit()
            flash("Note added", "success")
        return redirect(request.referrer or url_for("end_day", date=date))

    @app.route("/end", methods=["GET", "POST"])
    def end_day():
        d = current_date()
        loc = vehicles.default_location()
        sched = sched_svc.get_or_create_schedule(d, loc)
        rows = build_schedule_view(sched)
        notes = notes_for_date(d)
        replacements = replacement_count_for_date(d)

        counts = schedule_counters(rows)
        total = counts["total"]
        completed = counts["completed"]
        skipped = counts["skipped"]
        incomplete = counts["incomplete"]
        overall = counts["overall"]
        applicable_rows = [r for r in rows if not r["is_auto_skipped"]]
        incomplete_rows = [r for r in applicable_rows if not r["is_complete"]]
        prep_scopes = prep_timer.scope_totals(sched)
        completed_rows = []
        for r in applicable_rows:
            if not r["is_complete"]:
                continue
            emp_tasks = {}
            for t in r["entry"].tasks:
                if t.completed and t.employee:
                    emp_tasks.setdefault(t.employee.name, []).append(t.task_name)
            completed_rows.append({**r, "employees": emp_tasks})

        if request.method == "POST" and request.form.get("confirm") == "yes":
            finalize_day(sched)
            flash("Day finalized and saved to history", "success")
            return redirect(url_for("history_days"))

        # Date nav for end day (today, tomorrow, +2 days)
        from datetime import timedelta
        nav_dates = []
        for offset in range(3):
            nd = date.today() + timedelta(days=offset)
            nav_dates.append({
                "date": nd, "iso": nd.isoformat(),
                "label": ["Today", "Tomorrow", "+2 Days"][offset],
                "active": d == nd,
            })

        # Per-employee stats
        emp_done = {}
        total_tasks = 0
        for r in applicable_rows:
            for t in r["entry"].tasks:
                total_tasks += 1
                if t.completed and t.employee:
                    emp_done[t.employee.name] = emp_done.get(t.employee.name, 0) + 1
        employee_stats = sorted(
            [{"name": n,
              "initials": Employee.query.filter_by(name=n).first().initials,
              "done": d, "total": total_tasks,
              "pct": round(d / total_tasks * 100) if total_tasks else 0}
             for n, d in emp_done.items()],
            key=lambda x: x["name"])

        return render_template(
            "end_day.html", rows=rows, sched=sched, notes=notes,
            replacements=replacements, d=d,
            total=total, completed=completed, incomplete=incomplete,
            skipped=skipped,
            overall=overall, incomplete_rows=incomplete_rows,
            completed_rows=completed_rows,
            finalized=sched.finalized, employees=employees_list(),
            nav_dates=nav_dates, employee_stats=employee_stats,
            prep_states=[r["prep"] for r in rows],
            prep_total_label=timeutils.fmt_duration(
                prep_timer.total_active_seconds(sched)),
            prep_inside_label=timeutils.fmt_duration(
                prep_scopes["inside"]),
            prep_outside_label=timeutils.fmt_duration(
                prep_scopes["outside"]),
            prep_timed=sum(1 for r in rows if r["prep"]["status"] == "finished"),
            prep_running=sum(r["prep"]["running_count"] for r in rows))

    @app.route("/print/<path:date>")
    def print_report(date):
        d = datetime.strptime(date, "%Y-%m-%d").date()
        loc = vehicles.default_location()
        sched = sched_svc.get_or_create_schedule(d, loc)
        rows = build_schedule_view(sched)
        notes = notes_for_date(d)
        replacements = replacement_count_for_date(d)
        counts = schedule_counters(rows)
        total = counts["total"]
        completed = counts["completed"]
        skipped = counts["skipped"]
        overall = counts["overall"]
        applicable_rows = [r for r in rows if not r["is_auto_skipped"]]
        prep_total = prep_timer.total_active_seconds(sched)
        prep_scopes = prep_timer.scope_totals(sched)
        # Per-employee stats
        emp_done = {}
        total_tasks = 0
        for r in applicable_rows:
            for t in r["entry"].tasks:
                total_tasks += 1
                if t.completed and t.employee:
                    emp_done[t.employee.name] = emp_done.get(t.employee.name, 0) + 1
        employee_stats = sorted(
            [{"name": n,
              "initials": Employee.query.filter_by(name=n).first().initials,
              "done": d, "total": total_tasks,
              "pct": round(d / total_tasks * 100) if total_tasks else 0}
             for n, d in emp_done.items()],
            key=lambda x: x["name"])
        return render_template(
            "print_report.html", rows=rows, notes=notes, d=d, sched=sched,
            replacements=replacements, total=total, completed=completed,
            skipped=skipped,
            overall=overall, employee_stats=employee_stats,
            prep_states=[r["prep"] for r in rows],
            prep_total_label=timeutils.fmt_duration(prep_total),
            prep_inside_label=timeutils.fmt_duration(prep_scopes["inside"]),
            prep_outside_label=timeutils.fmt_duration(prep_scopes["outside"]),
            prep_timed=sum(1 for r in rows if r["prep"]["status"] == "finished"),
            eastern_tz=timeutils.EASTERN_TZ)

    @app.route("/history")
    def history_days():
        days = DailySchedule.query.order_by(
            DailySchedule.work_date.desc()).limit(60).all()
        imports = PrepReportImport.query.order_by(
            PrepReportImport.imported_at.desc()).limit(30).all()
        replacements = Replacement.query.order_by(
            Replacement.replaced_at.desc()).limit(50).all()
        return render_template(
            "history.html", days=days, imports=imports, replacements=replacements)

    @app.route("/import/<int:import_id>/delete", methods=["POST"])
    def import_delete(import_id):
        imp = PrepReportImport.query.get_or_404(import_id)
        count = vehicles.remove_import(imp)
        flash(f"Prep report import deleted along with {count} vehicle(s)", "success")
        return redirect(url_for("history_days"))

    @app.route("/schedule/<int:schedule_id>/delete", methods=["POST"])
    def schedule_delete(schedule_id):
        sched = DailySchedule.query.get_or_404(schedule_id)
        if sched.work_date == date.today():
            flash("Today's board cannot be deleted", "error")
            return redirect(url_for("history_days"))
        count = sched_svc.delete_schedule(sched)
        flash(f"Deleted previous day {sched.work_date.strftime('%b %d %Y')} "
              f"with {count} vehicle(s)", "success")
        return redirect(url_for("history_days"))

    @app.route("/history/vehicle/<int:vehicle_id>")
    def vehicle_history(vehicle_id):
        vehicle = Vehicle.query.get_or_404(vehicle_id)
        return _render_vehicle_detail(vehicle)

    def _unique_username(name, requested=""):
        """A free username for a new account.

        The Manager can type one; otherwise it is built from the person's
        name ("Jane Doe" -> "janedoe") and given a number when that is taken.
        """
        base = (requested or "").strip().lower()
        if not base:
            base = re.sub(r"[^a-z0-9]+", "", (name or "").lower()) or "user"
        candidate = base
        suffix = 2
        while UserAccount.query.filter_by(username=candidate).first() is not None:
            candidate = f"{base}{suffix}"
            suffix += 1
        return candidate

    @app.route("/employees", methods=["GET", "POST"])
    def employees_page():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if name:
                db.session.add(Employee(
                    name=name, location_id=vehicles.default_location().id))
                db.session.commit()
                flash("Employee added", "success")
            return redirect(url_for("employees_page"))
        return render_template("employees.html",
                               employees=Employee.query.all(),
                               account_name=(request.args.get("name") or "").strip(),
                               accounts=UserAccount.query.order_by(
                                   UserAccount.role, UserAccount.name).all())

    @app.route("/employees/<int:employee_id>/toggle-active", methods=["POST"])
    def employee_toggle_active(employee_id):
        employee = Employee.query.get_or_404(employee_id)
        employee.active = not employee.active
        if not employee.active:
            # Free the removed employee from any vehicle they were working on.
            employee.current_vehicle_id = None
            # ...and close their account too, so somebody the Manager has taken
            # off the staff list cannot keep signing in on it.
            for account in UserAccount.query.filter_by(
                    employee_id=employee.id).all():
                account.active = False
        db.session.commit()
        return redirect(url_for("employees_page"))

    @app.route("/employees/<int:employee_id>/delete", methods=["POST"])
    def employee_delete(employee_id):
        """Delete a staff record for good, rather than just taking it off the
        list.

        Remove keeps the person on file so everything they ever did still says
        who did it. Delete is the Manager saying the record itself was wrong or
        is no longer wanted, so the row goes -- but the work does not: every
        task they checked, clock they ran and note they wrote stays exactly
        where it is, just no longer credited to anybody.

        Two things are refused rather than left broken. A login still tied to
        the record goes first, because an Employee account with no staff record
        behind it is handed a brand new one the moment that person signs in --
        which would quietly undo the delete. A clock still running does too: it
        belongs to the work in front of them, and the Manager stops it the same
        way they stop any other.
        """
        employee = Employee.query.get_or_404(employee_id)
        name = employee.name

        accounts = staff_svc.tied_accounts(employee.id)
        if accounts:
            who = ", ".join(a.username for a in accounts)
            flash(f"{name} still signs in on {who}. Delete that login first, "
                  "then the staff record.", "error")
            return redirect(url_for("employees_page"))

        clock = staff_svc.open_clock(employee.id)
        if clock is not None:
            unit = clock.vehicle.unit_number if clock.vehicle else "their vehicle"
            state = "running" if clock.status == "running" else "paused"
            flash(f"{name} still has a {state} clock on {unit}. Stop that clock "
                  "first, then the staff record can go.", "error")
            return redirect(url_for("employees_page"))

        work = staff_svc.work_summary(employee.id)
        staff_svc.purge_employee(employee.id)
        if work:
            flash(f"{name} has been deleted from the staff list. Their work is "
                  f"still on file ({work}), no longer credited to anybody.",
                  "success")
        else:
            flash(f"{name} has been deleted from the staff list", "success")
        return redirect(url_for("employees_page"))

    @app.route("/accounts", methods=["POST"])
    def account_create():
        """Add an account for one person and hand them their credentials."""
        name = (request.form.get("name") or "").strip()
        role = (request.form.get("role") or "").strip().lower()
        password = request.form.get("password") or ""
        username = (request.form.get("username") or "").strip().lower()
        # A refusal comes back with the name still filled in, so the Manager
        # does not retype the one thing that was right.
        back = url_for("employees_page", name=name) if name \
            else url_for("employees_page")
        if not name:
            flash("Enter the person's name", "error")
            return redirect(back)
        if role not in VALID_ROLES:
            flash("Choose whether this is an Employee, Driver or Manager "
                  "account", "error")
            return redirect(back)
        if len(password) < UserAccount.MIN_PASSWORD_LENGTH:
            flash(f"Give them a password of at least "
                  f"{UserAccount.MIN_PASSWORD_LENGTH} characters", "error")
            return redirect(back)
        if username and UserAccount.query.filter_by(username=username).first():
            flash(f"The username {username} is already taken", "error")
            return redirect(back)

        account = UserAccount(
            username=_unique_username(name, username),
            name=name,
            role=role,
        )
        account.set_password(password)
        if role == UserAccount.ROLE_EMPLOYEE:
            account.employee = _employee_for_account(
                name, request.form.get("employee_id"))
        db.session.add(account)
        db.session.commit()
        flash(f"Account created for {name} — username: {account.username}",
              "success")
        return redirect(url_for("employees_page"))

    @app.route("/accounts/<int:account_id>/toggle-active", methods=["POST"])
    def account_toggle_active(account_id):
        """Remove or restore an account. Removing one ends the session of
        anybody signed in on it, from their very next page."""
        account = UserAccount.query.get_or_404(account_id)
        if account.active and account.is_manager and \
                _active_manager_count() <= 1:
            # Otherwise removing the last Manager locks everyone out of the
            # Staff page with no way back in.
            flash("This is the only active Manager account. Add another "
                  "Manager before removing this one.", "error")
            return redirect(url_for("employees_page"))
        account.active = not account.active
        db.session.commit()
        flash(f"{account.name}'s account has been "
              f"{'restored' if account.active else 'removed'}", "success")
        return redirect(url_for("employees_page"))

    @app.route("/accounts/<int:account_id>/reset-password", methods=["POST"])
    def account_reset_password(account_id):
        """Set a new password for somebody who cannot get into their own."""
        account = UserAccount.query.get_or_404(account_id)
        password = request.form.get("password") or ""
        if len(password) < UserAccount.MIN_PASSWORD_LENGTH:
            flash(f"Give {account.name} a password of at least "
                  f"{UserAccount.MIN_PASSWORD_LENGTH} characters", "error")
            return redirect(url_for("employees_page"))
        account.set_password(password)
        db.session.commit()
        flash(f"{account.name}'s password has been reset. Tell them the new "
              "one so they can change it to something only they know.",
              "success")
        return redirect(url_for("employees_page"))

    @app.route("/accounts/<int:account_id>/delete", methods=["POST"])
    def account_delete(account_id):
        """Delete a login for good, rather than just closing it.

        Remove keeps the row so it can be reactivated. Delete is the Manager
        saying the username is never coming back -- a login typed in by
        mistake, or somebody who never started -- so it goes, and the username
        is free again for whoever is given it next.

        The person's work is not touched by any of it: tasks, timers and
        reports are recorded against their staff record, not their login, so
        every bit of it stays exactly where it is. Deleting the login is the
        first half of removing somebody; the staff record follows once the
        account is gone.

        The last active Manager account is refused, exactly as it is for
        Remove, because deleting it would lock everyone out of the Staff page
        with no way back in.
        """
        account = UserAccount.query.get_or_404(account_id)
        if account.active and account.is_manager and \
                _active_manager_count() <= 1:
            flash("This is the only active Manager account. Add another "
                  "Manager before deleting this one.", "error")
            return redirect(url_for("employees_page"))
        name = account.name
        username = account.username
        # Deleting the login the Manager is signed in on ends their session
        # here rather than on their next page, where the message would be lost
        # with the rest of the session.
        signing_in = account.id == session.get("user_id")
        db.session.delete(account)
        db.session.commit()
        if signing_in:
            session.clear()
            flash("Your own account has been deleted, so you have been signed "
                  "out. Sign in with another account.", "success")
            return redirect(url_for("login"))
        flash(f"{name}'s account ({username}) has been deleted", "success")
        return redirect(url_for("employees_page"))

    def _active_manager_count():
        return UserAccount.query.filter_by(
            role=UserAccount.ROLE_MANAGER, active=True).count()

    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        from .models import VehicleType
        from .services.schedule import refresh_type_entries
        account = _signed_in_account()
        user = account.role if account else None
        emp_id = session.get("employee_id")
        if request.method == "POST":
            # Theme is the one setting every account can change, and it is
            # stored per-user so one person's choice never changes someone
            # else's appearance.
            dark_mode = request.form.get("dark_mode")
            if dark_mode in settings.THEME_CHOICES:
                settings.set_user_theme(user, emp_id, dark_mode)
            # Layout is another per-user appearance choice: classic (top bar,
            # the default site design) or sidepanel (the new design).
            layout = request.form.get("layout")
            if layout in settings.LAYOUT_CHOICES:
                settings.set_user_layout(user, emp_id, layout)
            # The remaining operational settings are manager-only.
            if user == "manager":
                for key in ["recent_days", "due_soon_days", "location"]:
                    val = request.form.get(key)
                    if val is not None:
                        settings.set_setting(key, val)
                # Per-vehicle-type checklists (Inside + Outside). A type has no
                # shared list to fall back on: these two fields are its list, and
                # they are what that type's vehicles show on the board.
                for vt in VehicleType.query.all():
                    in_val = request.form.get(f"type_checklist_inside_{vt.id}")
                    out_val = request.form.get(f"type_checklist_outside_{vt.id}")
                    if in_val is None and out_val is None:
                        continue
                    in_tasks = [x.strip() for x in (in_val or "").split(",")
                                if x.strip()]
                    out_tasks = [x.strip() for x in (out_val or "").split(",")
                                 if x.strip()]
                    combined = settings.format_type_checklist_for_storage(
                        in_tasks, out_tasks)
                    new_val = combined or None
                    if vt.checklist != new_val:
                        vt.checklist = new_val
                        db.session.commit()
                        refresh_type_entries(vt)
            db.session.commit()
            flash("Settings saved", "success")
            return redirect(url_for("settings_page"))
        vtypes = VehicleType.query.order_by(VehicleType.name).all()
        for vt in vtypes:
            cat = settings.get_type_categorized_checklist(vt)
            vt._inside = ", ".join(cat["inside"])
            vt._outside = ", ".join(cat["outside"])
        return render_template("settings.html", settings={
            "recent_days": settings.get_setting("recent_days", 2),
            "due_soon_days": settings.get_setting("due_soon_days", 7),
            "location": settings.get_setting("location") or "Main Depot",
            "dark_mode": settings.get_user_theme(user, emp_id),
            "layout": settings.get_user_layout(user, emp_id),
        }, vehicle_types=vtypes)

    @app.route("/trash", methods=["GET", "POST"])
    def trash_page():
        """Trash tab: shows the last time trash was picked up from each lot
        and lets staff record a new pickup."""
        if request.method == "POST":
            picked = request.form.get("picked_at", "").strip()
            when = datetime.utcnow()
            if picked:
                try:
                    when = datetime.fromisoformat(picked)
                except ValueError:
                    pass
            pickup = TrashPickup(
                location_id=request.form.get("location_id")
                or vehicles.default_location().id,
                picked_up_at=when,
                notes=request.form.get("notes", "").strip() or None,
                employee_id=request.form.get("employee_id") or None,
            )
            db.session.add(pickup)
            db.session.commit()
            flash("Trash pickup recorded", "success")
            return redirect(url_for("trash_page"))

        default_loc = vehicles.default_location()
        locations = Location.query.order_by(Location.name).all()
        if not locations:
            locations = [default_loc]
        rows = []
        for loc in locations:
            last = TrashPickup.query.filter_by(
                location_id=loc.id).order_by(
                    TrashPickup.picked_up_at.desc()).first()
            never = last is None
            days_since = None
            freshness = ("Never", "muted")
            if last:
                diff = datetime.utcnow() - last.picked_up_at
                days_since = diff.days + diff.seconds / 86400.0
                if days_since <= 1:
                    freshness = ("Recent", "success")
                elif days_since <= 7:
                    freshness = ("Due", "warn")
                else:
                    freshness = ("Overdue", "danger")
            rows.append({
                "location": loc,
                "last_pickup": last,
                "never": never,
                "days_since": fmt_days_since(days_since),
                "freshness": freshness,
            })
        return render_template("trash.html", rows=rows,
                               employees=employees_list(),
                               locations=locations,
                               default_location=default_loc,
                               now_iso=datetime.utcnow().strftime("%Y-%m-%dT%H:%M"))

    # ------------------------------------------------------------------
    # Incident reports
    # ------------------------------------------------------------------

    def _incident_actor_id():
        """Resolve who is recording an incident action: the signed-in employee
        id, or None for the Manager account."""
        if session.get("user") == "employee" and session.get("employee_id"):
            try:
                return int(session["employee_id"])
            except (TypeError, ValueError):
                return None
        return None

    @app.route("/incidents")
    def incidents_list():
        filters = {
            "unit": request.args.get("unit", "").strip(),
            "type": request.args.get("type", "").strip(),
            "severity": request.args.get("severity", "").strip(),
            "employee": request.args.get("employee", "").strip(),
            "status": request.args.get("status", "").strip(),
        }
        q = IncidentReport.query.join(
            Vehicle, IncidentReport.vehicle_id == Vehicle.id)
        if filters["unit"]:
            q = q.filter(
                Vehicle.unit_number.ilike(f"%{filters['unit']}%"))
        if filters["type"]:
            q = q.filter(IncidentReport.issue_type == filters["type"])
        if filters["severity"]:
            q = q.filter(IncidentReport.severity == filters["severity"])
        if filters["status"]:
            q = q.filter(IncidentReport.status == filters["status"])
        if filters["employee"]:
            try:
                emp_id = int(filters["employee"])
            except ValueError:
                emp_id = None
            if emp_id:
                q = q.filter(db.or_(IncidentReport.reported_by == emp_id,
                                    IncidentReport.assigned_to == emp_id))
        incidents = q.order_by(IncidentReport.created_at.desc()).all()

        summary = {
            "open": IncidentReport.query.filter_by(status="Open").count(),
            "in_progress": IncidentReport.query.filter_by(
                status="In Progress").count(),
            "resolved": IncidentReport.query.filter_by(status="Resolved").count(),
            "total": IncidentReport.query.count(),
        }
        return render_template(
            "incidents.html", incidents=incidents, filters=filters,
            issue_types=ISSUE_TYPES, severities=SEVERITIES, statuses=STATUSES,
            employees=Employee.query.filter_by(active=True)
            .order_by(Employee.name).all(),
            summary=summary)

    @app.route("/incidents/new", methods=["GET", "POST"])
    def incident_new():
        if request.method == "POST":
            vehicle = None
            vehicle_id = request.form.get("vehicle_id", "").strip()
            if vehicle_id:
                vehicle = Vehicle.query.get(int(vehicle_id))
            if not vehicle:
                unit = request.form.get("unit_number", "").strip()
                if unit:
                    vehicle, _ = vehicles.find_or_create_vehicle(
                        unit, location_id=vehicles.default_location().id)
            if not vehicle:
                flash("Please choose a vehicle", "error")
                return redirect(url_for("incident_new"))

            issue_type = request.form.get("issue_type", "").strip()
            if issue_type not in ISSUE_TYPES:
                flash("Please choose a valid issue type", "error")
                return redirect(url_for("incident_new"))
            description = request.form.get("description", "").strip()
            if not description:
                flash("Description is required", "error")
                return redirect(url_for("incident_new"))
            severity = request.form.get("severity", "Medium").strip()
            if severity not in SEVERITIES:
                severity = "Medium"
            occurred_at = datetime.utcnow()
            occurred_raw = request.form.get("occurred_at", "").strip()
            if occurred_raw:
                try:
                    occurred_at = datetime.fromisoformat(occurred_raw)
                except ValueError:
                    pass
            # Reported by: managers pick from the list, employees are always
            # the currently selected employee.
            reported_by = _incident_actor_id()
            if reported_by is None and session.get("user") == "manager":
                raw = request.form.get("reported_by", "").strip()
                try:
                    reported_by = int(raw) if raw else None
                except ValueError:
                    reported_by = None

            incident = IncidentReport(
                vehicle_id=vehicle.id,
                issue_type=issue_type,
                severity=severity,
                description=description,
                location=request.form.get("location", "").strip() or None,
                occurred_at=occurred_at,
                reported_by=reported_by,
            )
            _apply_report_fields(incident, request.form)
            db.session.add(incident)
            db.session.flush()

            for f in request.files.getlist("photos"):
                if f and f.filename:
                    photo = incidents_svc.add_photo(
                        incident, f, uploaded_by=reported_by)
                    if photo is None:
                        flash(f"Skipped photo '{f.filename}': "
                              "unsupported file type", "warn")
            db.session.commit()
            flash("Incident report submitted", "success")
            return redirect(url_for("incident_detail", incident_id=incident.id))

        preselect = request.args.get("vehicle", "").strip()
        try:
            preselect_id = int(preselect)
        except ValueError:
            preselect_id = None
        return render_template(
            "incident_new.html",
            vehicles=Vehicle.query.order_by(Vehicle.unit_number).all(),
            employees=Employee.query.filter_by(active=True)
            .order_by(Employee.name).all(),
            issue_types=ISSUE_TYPES, severities=SEVERITIES,
            preselect_id=preselect_id,
            now_iso=datetime.now().strftime("%Y-%m-%dT%H:%M"))

    @app.route("/incidents/<int:incident_id>")
    def incident_detail(incident_id):
        incident = IncidentReport.query.get_or_404(incident_id)
        return render_template(
            "incident_detail.html", incident=incident,
            issue_types=ISSUE_TYPES, severities=SEVERITIES, statuses=STATUSES,
            employees=Employee.query.filter_by(active=True)
            .order_by(Employee.name).all())

    @app.route("/incidents/<int:incident_id>/edit", methods=["POST"])
    def incident_edit(incident_id):
        incident = IncidentReport.query.get_or_404(incident_id)
        prev_status = incident.status
        issue_type = request.form.get("issue_type", "").strip()
        if issue_type in ISSUE_TYPES:
            incident.issue_type = issue_type
        severity = request.form.get("severity", "").strip()
        if severity in SEVERITIES:
            incident.severity = severity
        status = request.form.get("status", "").strip()
        if status in STATUSES and status != prev_status:
            incident.status = status
            if status == "Resolved":
                incident.resolved_at = incident.resolved_at or datetime.utcnow()
            else:
                incident.resolved_at = None
        location = request.form.get("location", "").strip()
        incident.location = location or None
        description = request.form.get("description", "").strip()
        if description:
            incident.description = description
        occurred_raw = request.form.get("occurred_at", "").strip()
        if occurred_raw:
            try:
                incident.occurred_at = datetime.fromisoformat(occurred_raw)
            except ValueError:
                pass
        assigned_raw = request.form.get("assigned_to", "").strip()
        if assigned_raw:
            try:
                incident.assigned_to = int(assigned_raw)
            except ValueError:
                pass
        elif "assigned_to" in request.form:
            incident.assigned_to = None
        _apply_report_fields(incident, request.form)
        db.session.commit()
        flash("Incident updated", "success")
        return redirect(url_for("incident_detail", incident_id=incident.id))

    @app.route("/incidents/<int:incident_id>/note", methods=["POST"])
    def incident_note(incident_id):
        incident = IncidentReport.query.get_or_404(incident_id)
        note = incidents_svc.add_note(
            incident, request.form.get("text", ""),
            employee_id=_incident_actor_id())
        db.session.commit()
        flash("Note added", "success") if note else flash(
            "Note cannot be empty", "error")
        return redirect(url_for("incident_detail", incident_id=incident.id))

    @app.route("/incidents/<int:incident_id>/photos", methods=["POST"])
    def incident_photo_upload(incident_id):
        incident = IncidentReport.query.get_or_404(incident_id)
        actor = _incident_actor_id()
        added = 0
        skipped = 0
        for f in request.files.getlist("photos"):
            if not f or not f.filename:
                continue
            photo = incidents_svc.add_photo(incident, f, uploaded_by=actor)
            if photo is None:
                skipped += 1
            else:
                added += 1
        db.session.commit()
        if added:
            flash(f"{added} photo(s) added", "success")
        else:
            flash("No new photos added", "warn")
        if skipped:
            flash(f"{skipped} photo(s) skipped: unsupported file type", "warn")
        return redirect(url_for("incident_detail", incident_id=incident.id))

    @app.route("/incidents/<int:incident_id>/resolve", methods=["POST"])
    def incident_resolve(incident_id):
        incident = IncidentReport.query.get_or_404(incident_id)
        incident.status = "Resolved"
        incident.resolution_notes = request.form.get(
            "resolution_notes", "").strip() or None
        incident.resolved_at = datetime.utcnow()
        db.session.commit()
        flash("Incident marked as resolved", "success")
        return redirect(url_for("incident_detail", incident_id=incident.id))

    @app.route("/incidents/photo/<int:photo_id>")
    def incident_photo(photo_id):
        photo = IncidentPhoto.query.get_or_404(photo_id)
        if not os.path.isfile(photo.file_path):
            abort(404)
        return send_file(photo.file_path)

    @app.route("/incidents/photo/<int:photo_id>/delete", methods=["POST"])
    def incident_photo_delete(photo_id):
        photo = IncidentPhoto.query.get_or_404(photo_id)
        incident_id = photo.incident_id
        if photo.file_path and os.path.isfile(photo.file_path):
            try:
                os.remove(photo.file_path)
            except OSError:
                pass
        db.session.delete(photo)
        db.session.commit()
        flash("Photo removed", "success")
        return redirect(url_for("incident_detail", incident_id=incident_id))

    @app.route("/incidents/<int:incident_id>/pdf")
    def incident_pdf(incident_id):
        """Download the incident filled onto the ECHO Accident/Incident Report."""
        import io
        from .services.incident_report_pdf import render_incident_pdf
        incident = IncidentReport.query.get_or_404(incident_id)
        data = render_incident_pdf(incident)
        unit = incident.vehicle.unit_number or "vehicle"
        filename = f"incident_{incident.id}_{unit}_report.pdf"
        return send_file(io.BytesIO(data), mimetype="application/pdf",
                         as_attachment=True, download_name=filename)

    def _import_date_options():
        """Build date options for import: today, tomorrow, +2 days."""
        from datetime import timedelta
        labels = ["Today", "Tomorrow", "+2 Days"]
        return [
            {"iso": (date.today() + timedelta(days=i)).isoformat(),
             "label": labels[i],
             "display": (date.today() + timedelta(days=i)).strftime("%b %d"),
             "is_today": i == 0}
            for i in range(3)
        ]

    return app
