# Detailing Operations Dashboard

A production-ready, responsive web app that replaces the paper vehicle **prep
report** for a bus and vehicle detailing operation. It manages the daily prep
report, drives the crew's work through a live checklist board, tracks cleaning
history and prep time, handles vehicle substitutions, tracks trash pickups and
incident reports, and generates an end-of-day printable summary automatically.

Built as a real working application with a persistent database, PDF parsing &
OCR, an import preview/approval flow, a daily checklist board, replacement
logic, history views, a searchable dashboard, and a printable day-end report.
It is not a static mockup.

---

## What this app is for

A detailing operation washes and cleans a fleet of buses and vehicles every day.
The company's dispatcher publishes a **prep report** each morning listing which
units must be prepped, when they are due, and which driver each one is assigned
to. On paper, that report becomes a clipboard the crew ticks off one vehicle at
a time — and the tick sheet can be lost, the cleaning history can never be
searched, nobody can prove how long a vehicle actually took, and there is no
record of a substitution.

This app replaces that clipboard end to end:

- **What is due today** comes from uploading the company's own prep-report PDF.
- **What has been done** is a live board the crew works from their phones.
- **How long it took and who did it** is timed on the server, down to each
  employee and each side of the vehicle (inside / outside).
- **What was actually washed** is kept as permanent, searchable history.
- **What happened on shift** — replacements, skip reasons, notes, trash pickups
  and incidents — is recorded alongside the day and printed on the daily report.
- **What the driver needs to know** is a read-only screen of finished vehicles.

It is designed for one depot today (Location is a first-class model, so more
depots can be added) and for a shared floor screen plus a phone in every
employee's hand, working the same board at the same instant.

---

## Who uses it — roles & permissions

Every person signs in with **their own account**. The account's role decides
which screens open and which actions are accepted; the role is re-read from the
database on every request, so a change takes effect immediately and a tampered
session can never widen access.

| Role | Lands on | Purpose |
|------|----------|---------|
| **Manager** | Today's Board | Runs the operation: staff, fleet, thresholds, incidents, corrections. |
| **Employee** | Today's Board | The cleaning crew: works the board, ticks tasks, runs the clocks. |
| **Driver** | Finished Vehicles | Consumes the result: sees what is ready to pick up and reports incidents. |

### What each role can do

| Capability | Manager | Employee | Driver |
|------------|:-------:|:--------:|:------:|
| Sign in / change own password | ✅ | ✅ | ✅ |
| Choose own theme, layout & text size | ✅ | ✅ | ✅ |
| View the daily board and day totals | ✅ | ✅ | — |
| Tick checklist tasks | 🚫 read-only | ✅ | — |
| Start / Pause / Resume / Done prep clocks, **+ Add Me** | 🚫 read-only | ✅ | — |
| **Done With Vehicle** / **Skip** / **Un-skip** | 🚫 read-only | ✅ | — |
| Replace a vehicle on the board | ✅ | ✅ | — |
| Add a vehicle to a day's board manually | ✅ | ✅ | — |
| Import a prep-report PDF (upload + apply) | ✅ | ✅ | — |
| Add a note to the day report | ✅ | ✅ | — |
| Day Report (`/end`) & printable report | ✅ | ✅ | — |
| History (days, imports, replacements) | ✅ (view) | ✅ | — |
| Delete an unfinalized past day / an import | ✅ | ✅ | — |
| **Reopen** a finalized day | ✅ | 🚫 | — |
| Vehicles page (add / edit / retire) | ✅ | — | — |
| Staff page (employees & logins) | ✅ | — | — |
| Operational Settings (thresholds, per-type checklists) | ✅ | — | — |
| Trash pickups | ✅ | ✅ | — |
| Incident: report, view, download PDF | ✅ | ✅ | ✅ |
| Incident management: edit / assign / note / photos / resolve | ✅ | 🚫 | 🚫 |
| Finished Vehicles screen | ✅ (via board) | ✅ (via board) | ✅ |
| Report an incident from each screen | ✅ | ✅ | ✅ |

Notes on the rules:

- **The Manager board is read-only for the cleaning work itself.** The server
  refuses a tick, a clock press, a skip or a Done from a Manager account with
  *"Manager view is read-only"*, and the buttons are disabled. A Manager can
  still add or replace a vehicle, add a note, import a report and manage all of
  Staff, Vehicles, Incidents and Settings.
- **Drivers only see the Finished Vehicles screen**, plus their own Settings and
  Password pages and the incident-report flow. Any other page redirects them
  back to Finished Vehicles.
- **An Employee account is tied to that person's staff record**, so a tick or a
  clock press is recorded against them with no name picker.

---

## The daily workflow

1. **Morning — import.** A Manager or employee uploads the prep-report PDF on
   the **Import** tab. The app extracts the units, types, routes, prep/pickup
   times, driver codes and any substitutions, shows an **Import Preview**, and
   waits for **Apply Updates** before anything is written. Multiple imports per
   day are allowed.
2. **All day — work the board.** The **Today** board shows the day's folded
   vehicle list. The crew presses **Start** on a vehicle's Inside or Outside
   work, ticks tasks, presses **Done**, and can **Replace**, **Skip**,
   **Un-skip**, **+ Add Me** or **Done With Vehicle** as the day demands. Every
   press is timed and attributed to the signed-in employee.
3. **Anytime — handle the unexpected.** Record a **trash pickup** on the Trash
   tab, or file an **incident** from any screen. Both are kept in history.
4. **10:30 PM Eastern — automatic close.** An in-process scheduler finalizes the
   day: running clocks stop, totals are saved, and the day becomes an immutable
   report. A Manager can **reopen** a finalized day to correct it.
5. **Next morning — review.** The **Day Report** and printable PDF show the
   previous day's totals, prep-time log, event detail and notes; **History**
   keeps every previous day, import and replacement.

---

## Every feature

### 1. Vehicle database (fleet registry)
Unit number, type, route, status/location, description, make, model, capacity,
last washed, last detailed, **last dumped** and **cleanings since dump**,
cleaning frequency, notes, and active/inactive. Click a vehicle to see its
complete service history, replacements and permanent prep-time history.
The vehicle type carries its own checklist, so a vehicle shows exactly the tasks
its type lists.

### 2. PDF prep report import
Upload the company's PDF prep report. Auto-extracts unit numbers, types, routes,
prep times, pickup times, driver codes, and substitutions. Supports text PDFs,
tabular reports, and scanned PDFs via OCR (best-effort). Unit numbers are
normalized so `BUS 142`, `Unit 142`, and `142` all match vehicle 142. Both the
**ECHO prep report** (`sample_prep_report.pdf`) and the **Vehicle Wash Report**
(`sample_vehicle_wash_report.pdf`) templates are supported — either one can be
uploaded. Shows an **Import Preview** (new / updated / removed / route changes /
replacements / uncertain) and requires clicking **Apply Updates** before
anything touches the database. Multiple imports per day are allowed and
historical data is never deleted. Vehicles whose type is **TRANSITB** (transit
buses) are imported and shown but skipped automatically — they are washed by
another crew — and are collected in their own dropdown at the bottom of the
board. The original PDF is stored and can be re-viewed from History.

### 3. Daily Detailing Board
After import, today's work list is created automatically. The board is a list of
**folded** vehicles: each row shows the unit number, the vehicle type, the
driver it runs for, and the report time it is due (a driver and a report time
only when the report supplied them), plus a **✓ on any finished vehicle**, so
the day's work reads as a to-do list down a column of forty rows without opening
a single one. A tap opens the row to show route, status, last washed, the prep
clocks, the dump status, the task list and progress, so a long day fits on one
screen. The tick appears the moment the last task is checked, and comes off
again if one is un-checked; a skipped vehicle is never ticked, because skipping
is not a way of finishing one.

The task list is broken into two categories — **Inside** (Sweep, Mop, Windows,
Seats, Bathroom) and **Outside** (Dump, Bay Checked, Final Inspection) — both
configurable in Settings, and each category's boxes stay folded away until
**Start** has been pressed for that side of the vehicle, so a vehicle nobody has
picked up yet reads as two clocks and their Start buttons rather than as a wall
of boxes that cannot honestly be ticked yet. A category with a box already
ticked, and a finished vehicle's whole list, are never folded away: those are a
record of what happened. Every checkbox saves a completion timestamp and the
employee. Progress shows `6/8 — 75%`.

Under the task list is **Done With Vehicle** — the crew's own way out of a row
when they are finished with it **whether or not every task is checked**. It is
offered only once at least one box on the vehicle (Inside or Outside) has been
ticked, so a row nobody has begun recording work on cannot be finished as
empty. It counts the vehicle as completed on the board exactly like ticking
every box off does, stops any clock still running on it, moves the day's totals,
and names the tasks it left undone on the row, so a vehicle finished early can
never be mistaken for one worked through in full.

The board also supports **date navigation** (Today / Tomorrow / +2 Days), a
**+ Add Vehicle** button to put a unit on a day without a PDF, search and filter
by unit / type / route / status, and a **Transit Buses** dropdown so transit
units stay visible without crowding the work list.

### 4. Per-vehicle prep timer (Start / Pause / Resume / Done)
Each vehicle on the board gets **two independent clock sets, one for the inside
work and one for the outside work**, and each clock is recorded against the
employee doing the work. Every vehicle's Inside and Outside **Start** buttons
are on the board from the start, so anyone can press one without any further
step, and pressing one is also what opens up that side's task list.
**Start** begins the clock and puts the vehicle in progress; **Pause** and
**Resume** stop and restart it without ever losing the time already worked;
**Done** stops the clock, freezes that person's total active prep time and
finishes that clock set, and **finishes the vehicle only once both of its sets
are done and no clock is still running** — so finishing one side never completes
the whole vehicle, and the other side still has to be started and finished. The
two sets never borrow time from each other: working the outside of a vehicle
does not advance its inside clock, and the vehicle's headline number is the sum
of both sides.

**Several employees can work the same vehicle at the same time**, on the same
side or on opposite sides: the row shows a clock per employee per set, a
combined vehicle clock, and an **+ Add Me** button for anyone else to start
their own clock, and each person's Pause/Resume/Done only ever touches their own
clock. One employee can run an inside and an outside clock at the same time, and
a second clock in the *same* set must be paused (or finished) first. Only the
buttons valid for the current state are shown, and an out-of-order action
(starting a set that is already running for you, finishing one that was never
started, resuming a running timer) is refused with a clear reason instead of
corrupting the record.

Time is tracked while the page is closed — the server owns the clock, so a
refresh, a backgrounded tab, or a device with a wrong clock never loses or
invents time. Every Start/Pause/Resume/Done is kept as permanent event history
for the vehicle (shown on the board, the vehicle's history page, and the
report), and completing a vehicle any other way (full checklist, the nightly
close) stops its clock too. All times are recorded and displayed in **Eastern
Time** (America/New_York), including the report's 24-hour prep/pickup times,
which are displayed as 12-hour AM/PM without altering the stored values.

### 5. Vehicle replacements
**Replace Vehicle** moves remaining applicable daily prep requirements to the
replacement while preserving completed work and historical records.
Replacements are clearly displayed on the board and recorded in history with
original, replacement, timestamp, reason and employee.

### 6. Smart status, last washed & dump tracking
Last Washed auto-updates when all Outside tasks for the vehicle are complete;
Inside tasks do not count as a wash. Last Detailed updates on the final
inspection. For types whose checklist includes **Dump**, the app counts
cleanings since the last dump and raises a **Needs Dump** badge (after two
cleanings) on the board, the vehicle page, the driver screen and the printable
report; ticking **Dump** records a dump and resets the counter. Configurable
visual indicators mark vehicles **Recently Washed / Due Soon / Overdue**.

### 7. Dashboard
Today's totals: total, completed, in progress, remaining, overdue,
replacements, worst overall completion %, and the day's total **active prep
time** (all employees' clocks added together, so two people working the same
vehicle count as two workers). A **Now Working** strip shows every employee
currently on the floor with their vehicle and a live clock — including one who
has picked a vehicle but not started a clock on it yet. Search and filter by
unit number, type, route, and status. Every folded row shows its unit number,
vehicle type, driver (when the wash report supplied one) and report (prep) time,
and the opened row adds the route and the pickup time (displayed as 12-hour
AM/PM Eastern time). Transit buses (TRANSITB) are pulled out of the main work
list into a **Transit Buses** dropdown at the bottom of the board.

### 8. Day Report & automatic end of day
There is no manual "end my day" button. Every day is finalized **automatically
at 10:30 PM Eastern** (`America/New_York`) by the in-process scheduler; this
cutoff is configurable via `AUTO_END_DAY_TIME` (HH:MM, Eastern). At the close the
day's schedule is finalized: any timer still running is stopped, the
completed/incomplete totals are calculated and saved as the day's **summary**,
unfinished checklist items, replacements and notes are kept, the completion % is
computed, and the day is saved to history and locked.

The **Day Report** page (`/end`) is a read-only view of that record with totals,
completed/incomplete lists, per-employee task counts, replacements, dump status
and notes, and it generates a clean printable daily summary (Print / Save as
PDF). The printable report includes a **Prep Time Log** with each vehicle's
Start/Pause/Resume/Done history, a **Prep Event Detail** section, and the day's
**total active prep time**.

A **finalized day is immutable**: check-offs, prep timers, skips, un-skips,
replacements, adding vehicles, notes and imports are all refused by the server,
and a finalized day cannot be deleted. If a correction is genuinely needed, a
**Manager** can **reopen** the day from History; reopening clears the lock and
the stored summary, and the next automatic run finalizes it again with the
corrected totals. No work is ever lost.

### 9. History
Previous days, vehicle cleaning history, prep report imports, replacements, and
(per-vehicle) completed checklists. Every finalized day can be re-opened from
here by a Manager; an unfinalized past day (never today) can be deleted along
with a prep-report import that is no longer wanted. Every vehicle's page also
keeps its permanent **Prep Time History**: one row per run with the employee,
status, start, finish, total active prep time, and the full
Start/Pause/Resume/Done event log.

### 10. Incident Reports
A dedicated tab where anyone — Manager, Employee or Driver — can report an issue
for any vehicle: type (Mechanical / Interior / Exterior / Damage / Safety /
Other), severity, location, description, date/time, and employee, with one or
more photo uploads. Managers can review, edit, assign, add notes, attach extra
photos, and resolve issues (Open → In Progress → Resolved), and filter the list
by vehicle, issue type, severity, employee, and status. Every incident is
permanently linked to the vehicle's history page. Incident reports follow the
official **ECHO East Coast Accident/Incident Report** form: the app's form
captures every field on that template — police notification, injuries, citations,
other driver/owner/insurance details, witnesses, roadway conditions and so on —
and a **Download PDF** button renders the completed report onto the blank form
for managers to save, print, or email. Available to all roles including Drivers,
who get a **Report an Incident** button on their Finished Vehicles screen.

### 11. Trash pickups
A dedicated **Trash** tab records when the trash was picked up from each lot,
with location, time, notes and the employee who logged it. It shows the last
pickup and the time since for every lot, with a freshness badge, so the crew can
see at a glance which lot is due.

### 12. Individual accounts
Every Employee and Driver signs in with their own username and password instead
of sharing one login for the whole crew. A Manager creates and removes accounts
from **Staff**, sets the first password, and can reset one somebody has
forgotten; each person then changes it to their own from the **Password** tab.
Signing in is what says who is working, so an Employee account is tied to that
person: their tasks and timers are recorded against them and the board opens on
their name with no name picker. Removing an account takes effect at once — the
person is signed out and cannot sign back in — while their completed work and
history are kept. A Manager can also **Delete** an account or a staff record for
good: the username is freed up, or the name comes off the board, while the work
that person did stays on file unattributed.

### 13. User interface, layouts, themes & accessibility
Mobile/tablet/desktop responsive with large checkboxes and buttons, minimal
typing, clean professional styling, color-coded statuses, and fast search. Two
site layouts are available and can be switched per user in **Settings**:
**Classic** (top navigation bar, the default design) and **Side Panel** (a fixed
sidebar navigation, sticky toolbar and wider content column). Layouts are
independent of the color themes (Light / Dark / System / Futuristic / Halloween /
Bloomberg Terminal / Retro 90s / Holographic), and a further group of **3D &
motion** themes (Synthwave, Cosmos, Cyberpunk, Aurora, Ocean, Crystal, Matrix,
Dunes) adds a three-dimensional scene with a continuously animated background.
Those themes follow the operating system's reduced-motion setting, and where the
browser supports it their parallax is tied to scroll position. **Text size** can
also be set per user (Default / Large / Largest), scaling every text size in the
app independently of theme and layout. After a Manager or Employee signs in, a
fullscreen **splash intro animation** plays before the board (skippable; Drivers
go straight to their Finished Vehicles screen), and the board's Refresh
shows a themed bus pulling away instead of a blank screen.

### 14. Data architecture
Real persistent database (SQLite via SQLAlchemy). Models: Locations,
Vehicle Types, Vehicles, Employees, User Accounts, Daily Schedules, Schedule
Entries, Checklist Tasks (Task Completions), Prep Sessions & Prep Session
Events, Cleaning/Service History, Vehicle Replacements, Prep Report Imports,
Activity/Notes, Trash Pickups, Settings, and Incident Reports (with notes &
photos). Designed to support multiple employees and locations; Location is a
first-class model.

### 15. Finished Vehicles (driver screen)
The Driver's home screen is a read-only list of **completed** vehicles for today
and the next two days, so a driver can see what is ready to pick up. Each row
shows the unit, type, route, report/prep time, pickup time, driver code, who
finished it (initials), the finish time, the completion percentage, and a
**Needs Dump** badge when the vehicle requires it. Drivers can file an incident
without leaving the screen.

---

## Quick start

Requires Python 3.9+.

```bash
pip install -r requirements.txt
python run.py
```

Open http://127.0.0.1:5000

A default **Main Depot** location and a **User** employee are created the first
time the app runs, along with the three starting logins — `manager`, `employee`
and `driver` (each password is the same as the username). Sign in as
**manager** and use **Staff** to give each Employee and Driver their own
account; the starting logins are ordinary accounts you can remove once you have.

### Generate a sample prep report (to try Import)

```bash
pip install -r requirements.txt     # needs PyMuPDF
python scripts/make_sample_report.py sample_prep_report.pdf                  # ECHO prep report
python scripts/make_sample_report.py --wash sample_vehicle_wash_report.pdf   # Vehicle Wash Report
```

The repository also ships ready-made copies of both templates
(`sample_prep_report.pdf` and `sample_vehicle_wash_report.pdf`). Either one can be
uploaded via **Import** → choose the PDF → review the preview → **Apply
Updates**.

---

## OCR / scanned PDFs

The importer extracts selectable text and tables with **PyMuPDF** out of the
box. For scanned (image-only) PDFs it falls back to OCR: each page is rendered
with PyMuPDF and read with **Tesseract**. `pytesseract` and `Pillow` are
included in `requirements.txt`; the only extra step is the system `tesseract`
binary:

```bash
# System tool only (Python deps are already in requirements.txt)
# Ubuntu/Debian:
sudo apt-get install -y tesseract-ocr
# macOS: brew install tesseract
```

No `poppler-utils` or `pdf2image` are required. If OCR is unavailable, the app
degrades gracefully: it warns that the scanned PDF could not be read and points
to the missing dependency rather than guessing.

---

## Accounts and sign-in

There used to be exactly three logins hardcoded in `app/app.py` — one shared
`employee`, one shared `driver` and one `manager`, each with its password in
plain text in the source. **Sign-in is now a database table.**

- **`UserAccount`** (`app/models.py`) is one person's sign-in: `username`,
  `name`, `role` (`manager` / `employee` / `driver`), `password_hash`, an
  optional link to an `Employee`, and `active`. Passwords are stored as a
  salted hash via `werkzeug.security.generate_password_hash` and checked with
  `check_password_hash`; the plain text is never written down anywhere.
  Usernames are compared case-insensitively, and the row is the single source of
  truth for the role — the session only caches the account's id, and
  `require_login` re-reads it on every request.
- **A Manager adds an account** from **Staff** (`/employees`) with a name, a
  password and an account type. The username is optional: leave it blank and it
  is built from the name (`Jane Doe` → `janedoe`, with a number appended if
  that is taken). Passwords must be at least 4 characters.
- **An Employee account is tied to that person.** Creating one for a name that
  is not on the staff list adds the staff record; a name that is already there
  reuses the existing record rather than duplicating it. The person is signed in
  as themselves, so a tick on the board is attributed to them even if the request
  carries no employee id.
- **Signing in is what says who is working.** There is no name picker: the
  account *is* the person's identity, so `_sign_in` resolves it to a staff record
  and the board opens already showing `Working as: <their name>`. An account that
  somehow has no staff record behind it is given one on the spot — the one
  already on file under that name, or a new one — and the link is saved, so
  nobody is ever left without somebody to record their work against. The shared
  `employee` login is just another account and works the same way: it signs in as
  the staff record called "Employee" and stops being a way to work the board as
  anybody. The Manager can remove it once everyone has an account of their own.
- **The board changes hands by signing in as the next person.** Each person
  signs in with their own username, and everything they press is recorded
  against them.
- **Removing an account takes effect immediately.** The account's `active` flag
  is re-read on every request, so the person is signed out on their next page
  load and refused at the login page; their completed work, timers and history
  are untouched. Removing an employee from the staff list closes their account
  too, and the last active Manager account cannot be removed at all — otherwise
  nobody could get back into the Staff page.
- **Deleting takes either of them off the books for good.** Remove is reversible
  and keeps the person on file; **Delete** is not, and is there for a login or a
  staff record the Manager never wants to see again.
  - **`POST /accounts/<id>/delete`** removes the `UserAccount` row itself. The
    username is free again for whoever is given it next, and the person can
    never sign in on it. Deleting the account a Manager is signed in on clears
    the session and returns them to the login page with a note saying why. The
    last active Manager account is refused, exactly as it is for Remove.
  - **`POST /employees/<id>/delete`** (`app/services/staff.py`) removes the
    `Employee` row, but never the work. Every task, timer, note, report and
    incident that person touched is kept and simply stops being credited to
    anybody, so the history still adds up. Two things are refused rather than
    left broken: a login still tied to the record — delete it first, since an
    Employee login with no staff record behind it is handed a brand new one the
    moment that person signs in, which would quietly undo the delete — and a
    clock still `running` or `paused`, which belongs to the work in front of
    them.
- **Everyone can change their own password** from the **Password** tab
  (`/account/password`), which asks for the current password and confirms the
  new one, then returns to the dashboard. A Manager can also reset a forgotten
  password directly from the Staff table.
- **A removed or wrong login says the same thing** ("Invalid username or
  password"), so the login page never confirms that a username exists.

---

## Configuration

Database and secret are configured via environment variables (see
`.env.example`):

| Variable | Default |
|----------|---------|
| `SECRET_KEY` | `dev-secret-change-me` |
| `DATABASE_URL` | `sqlite:///data/detail.db` |
| `PORT` | `5000` |
| `AUTO_END_DAY_TIME` | `22:30` (Eastern wall-clock time the automatic end-of-day job runs, `HH:MM`; defaults to 10:30 PM) |

Thresholds (Recently Washed / Due Soon), the color theme, the site layout
(**Classic** top-bar design or the newer **Side Panel** sidebar design), and the
**text size** are all configurable in the **Settings** page at runtime. Theme,
layout and text size are saved per account, so each user keeps their own
appearance; the thresholds, the active location and the per-type checklists are
Manager-only.

The task list belongs to the vehicle type, not to the site: each type carries its
own checklist, split into **Inside** (interior cleaning) and **Outside**
(exterior/service) categories, and a vehicle shows exactly the tasks its own type
lists, in the order they are typed. A type that has never been given a list of
its own starts from the standard one (Sweep, Mop, Windows, Seats, Bathroom /
Dump, Bay Checked, Final Inspection), and clearing both fields returns it to that
list. There is no global default checklist to override.

**Text size** is zoomed text for anyone who finds the default hard to read:
**Default**, **Large** (120%) or **Largest** (140%). Every font size in
`app/static/css/style.css` is written in `rem`, so a choice only has to scale
the root font size and the whole app follows — it is independent of the theme
and the layout, and no component needs a rule of its own. Printed pages keep
their own fixed size, since paper has a fixed size; the generated report
(`templates/print_report.html`) sets its sizes in `px` throughout and is
unaffected either way.

A theme is just an id from `THEME_CHOICES` in `app/services/settings.py`; the
look itself is a `html[data-theme="<id>"]` block in
`app/static/css/style.css`. Adding one means four edits: register the id in
`THEME_CHOICES`, add the CSS block (plus a `@media print` block that forces
light tokens so reports don't ink out), add an `<option>` to the `dark_mode`
select in `templates/settings.html`, and add a test pair in
`tests/test_app.py`.

A text size is the same shape: an id from `TEXT_SIZE_CHOICES`, stored per user
under a `text_size:<user>` key and rendered as `data-text-size` on `<html>`.
Its CSS is one `html[data-text-size="<id>"] { font-size: <percent>; }` rule,
because all text is sized in `rem`. A test asserts every non-default choice
really has such a rule and really scales up, so an offered size cannot be one
the stylesheet ignores.

---

## Testing

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

The test suite covers unit normalization, text-PDF parsing, import
preview/apply, the checklist & progress, replacements, end-of-day finalization,
settings, and vehicle CRUD, plus the prep timer workflow: Start → Pause →
Resume → Done, per-vehicle independence, resume keeping prior work time,
refresh accuracy, invalid actions being rejected, timers being stopped by other
completion paths, the Eastern Time formatting, and the report/vehicle history
output. It also covers several employees timing the same vehicle at once
(independent clocks, one press acting on only one person's timer, the vehicle
only completing once the last person is done *and* both sides are, crew
reporting), the automatic upgrade of an older database to allow a crew per
vehicle, and the two clock sets per vehicle: each side timing apart, a paused
clock not blocking the other side, finishing one side never completing the
vehicle, one employee running both sides of one vehicle at once, two employees
working opposite sides of one vehicle, three employees on one vehicle at once,
and the board/end-of-day/print report splitting the day by clock set. It also
covers the two ways a vehicle is finished: every box ticked off, and **Done With
Vehicle** with boxes left — the vehicle counting as completed either way, its
clocks stopping, the day totals moving and the tasks left undone being named —
and the task list of each side staying folded away until that side is started.

---

## Project layout

```
app/
  app.py                 # Flask app factory + routes
  models.py              # SQLAlchemy models (schema)
  static/                # CSS + JS (and the splash intro video)
  templates/             # Jinja2 templates
  services/
    pdf_parser.py        # PDF text/table/OCR extraction + unit normalization
    schedule.py          # daily board, checklist, replacements, preview/apply
    prep_timer.py        # inside/outside clocks per employee per vehicle + history
    settings.py          # configurable thresholds + per-vehicle-type checklists
    timeutils.py         # Eastern Time storage, parsing and display helpers
    vehicles.py          # entity helpers, import/journal records
    staff.py             # employee/account lifecycle helpers
    incidents.py         # incident photos/notes helpers
    incident_report_pdf.py  # renders an incident onto the ECHO form PDF
scripts/
  make_sample_report.py  # generates a sample prep report PDF
tests/                   # pytest suite
run.py                   # local dev server
data/                    # SQLite database (created at runtime)
```

---

## How the prep timer works

The clock lives on the server, so a timer can never drift from the record that
is eventually reported.

- **One session per employee per vehicle per clock set per day.** A
  `PrepSession` stores its `scope` (`inside` or `outside`), the running total in
  seconds, the status (`running` / `paused` / `finished`), the start and finish
  timestamps, and the employee. Each Start/Pause/Resume/Done is
  also appended to a `PrepSessionEvent` with its own Eastern timestamp, the
  employee who pressed it, and the running total at that moment.
- **Inside and outside are timed apart.** A vehicle shows two panels — **Inside
  Prep** and **Outside Prep** — and each keeps its own total, its own employees
  and its own event log. The buttons carry the set they belong to, so pressing
  Done on the outside never touches an inside clock. The vehicle's headline
  clock is the sum of both sets, which is what the day total and the report
  count, so a vehicle with ten minutes inside and six outside reports sixteen.
- **A vehicle is only complete once both of its sides are.** Pressing Done
  finishes the clock set it was pressed in; the vehicle itself is closed by the
  **last** Done, once every clock on it is finished and both of its clock sets
  have been. A side nobody has started yet counts as outstanding, because that
  work still has to be timed and finished, and it can be started after the other
  side is already done. Until then the row stays open, its badge reads *One side
  done* rather than *Completed*, and the board names the side the vehicle is
  still waiting on.
- **A whole crew can share a vehicle, from either side, all at once.** A vehicle
  has one session per employee per set, so a second (or third) person just
  presses Start — or **+ Add Me** — and gets a clock of their own. Two people
  can wash the outside while a third sweeps the inside, and each of them is
  stamped separately for each side they work, so a vehicle worked inside by two
  and outside by three reports five clocks. One person can also run an inside
  clock *and* an outside clock side by side, because the two sets are timed
  apart. Pausing, resuming or finishing affects only the session that was
  pressed, and the vehicle is only marked complete once its last clock finishes
  *and* both of its clock sets have been. The vehicle's clock and its
  report lines are the sum of everybody's time on it, so two people working the
  same vehicle for an hour correctly report two hours of prep work.
- **One clock per person per clock set.** A person never runs two clocks in the
  *same* set on the same vehicle on the same day, so the same press cannot be
  recorded twice; the other set is always theirs to start, whether it is idle,
  already being worked by a colleague, or their own running clock. The refusal
  names the clock that is in the way, and because the press is always recorded
  against the person who made it, that clock is always the employee's own: it
  says it is already counting, or that the set of work is done.
- **Every press belongs to the person who signed in.** Each employee works the
  board from their own account, so a press is recorded against them and needs no
  name to be picked on the way in. A crew can still share one screen to work one
  vehicle: each person signs in in turn, presses Start on their side, and gets a
  clock of their own.
- **Existing databases are upgraded in place.** Sessions from the one-clock-per-
  vehicle schema are backfilled as `scope = 'both'`, so they count towards both
  the inside and the outside totals while still being reported only once, and
  the first launch after this change rebuilds that one table to keep every
  session, every total and every recorded event before the per-employee, per-set
  constraint takes over. The rebuilt table is the model's own definition, so the
  key it lands on cannot drift from the one the app relies on. A table is only
  considered current when **every** key it enforces covers the vehicle, **the
  employee** and the clock set, so a database left on an older key — one clock
  per vehicle per side, say — is rebuilt too instead of quietly going on
  refusing the second employee on a bus. Those keys are read from the database
  itself rather than from the table's `CREATE TABLE` text, because a key does not
  have to be written there: a `CREATE UNIQUE INDEX` is invisible in the DDL and
  refuses exactly the same inserts, and a stale key sitting *beside* a current
  one is just as capable of refusing a crew as a lone one. The rebuild is
  idempotent, so later launches leave the table alone. Nothing has to be done by
  hand, on any of these.
- **Active time is the sum of the active segments only.** Pausing banks the
  seconds worked so far; the paused stretch is never billed. Resuming starts a
  new segment on top of the banked total, so previous work is never lost.
- **The browser only displays.** Each running timer is rendered with the
  seconds already banked plus the exact moment its current segment began, and
  the page carries the server's clock. The browser adds the two together each
  second. A refresh re-syncs from the server, and a tab coming back to the
  foreground re-syncs too, so time spent with the page closed or hidden is
  added — never lost — and a device with a wrong clock is corrected. A press
  redraws only the vehicle it touched, building the clock list of a clock set
  that had none, so the very first clock on a side of a vehicle shows up (with
  its own Pause/Done buttons) as soon as Start is pressed.
- **Only valid actions are offered or accepted.** Starting a running vehicle,
  resuming a running timer, pausing a finished one, or finishing a vehicle that
  was never started are all rejected with an explanation and leave the record
  untouched. A rejection is answered with the vehicle's real state as well as
  the reason, so the board repaints the row rather than leaving the refused
  button on screen. Managers get a read-only board.
- **A crew working at the same time never blocks itself.** Several employees are
  on the board from their own devices at once, so a press regularly overlaps
  somebody else's page load or timer re-sync. The SQLite database is therefore
  opened in **WAL** mode, with a 30s busy timeout and a pool big enough for the
  devices on shift, so a reader can never lock a writer out — one employee
  starting the inside is not refused because another is working the outside.
  A press that still cannot reach the database is answered as JSON with a
  "press again" message rather than an error page, and the board re-reads the
  vehicle's clocks so nobody is left wondering whether their clock started.
  (WAL keeps a `detail.db-wal` / `detail.db-shm` pair next to the database; a
  copy of the database taken while the app is running should include them.)
- **A press the board could not read never guesses.** When the answer to a press
  is not JSON (a login redirect, an error page, a dropped connection) the board
  re-reads the vehicle from the server *and waits for that read* before it says
  anything, then reports what is really recorded: that your clock is running
  (so the press did land and there is nothing to press again), that a named
  colleague is already working that clock set (press **+ Add Me** to run a clock
  of your own), that your session had ended (so the press was never recorded), or
  — only when the re-read failed too — that the clock on screen
  may be out of date and the page should be reloaded. It never claims a re-check
  that did not happen.
- **A refresh is not a blank screen.** Re-reading the page from the server is an
  empty gap for as long as the server takes to answer, so the board's
  **Refresh** puts a bus on the road for it, drawn in the current theme's
  colours. Any control that re-reads the page (marked `data-reload-page`)
  refreshes behind it, the overlay is held for a moment before the reload so the
  bus is seen pulling away rather than flashing once, and it drops itself again
  if the reload somehow never happens — a board stuck behind a screen nobody can
  tap past would be worse than the blank one it replaced. With
  `prefers-reduced-motion` set the bus parks mid-road instead of driving, and
  the overlay still appears.
- **Nothing runs forever.** Finishing a timer is not the only way a clock stops:
  completing a vehicle through its checklist, or ending the day, stops any
  timer still running for that vehicle.
- **Eastern Time.** Every recorded timestamp is stored with its Eastern UTC
  offset (`-05:00` in winter, `-04:00` in summer) and displayed as 12-hour
  AM/PM, so a report can always be read unambiguously. The report's original
  24-hour prep and pickup times are converted for display only — what was
  imported is what is stored.

---

## How replacements & history integrity work

- **The PDF controls the daily schedule; the database controls permanent
  history.** Importing a new prep report never overwrites or deletes cleaning
  history (`service_records`), past checklists, replacements, or finalized
  days.
- Vehicles that disappear from a report are **deactivated**, not deleted, so
  their history remains.
- Transit buses (type `TRANSITB`, `TRANSIT BUS`, `TRANSIT`) are auto-skipped
  when they arrive on a report: they are excluded from today's work totals and
  completion, are never marked as washed, and are shown in the board's Transit
  Buses dropdown instead of the main work list. Work already done on them (or a
  manual skip) is never overwritten by a re-import.
- When a vehicle is replaced, its completed tasks and timestamps are carried
  forward onto the replacement entry; the original entry and its completed work
  are preserved as historical records, and a `Replacement` row is written with
  original, replacement, timestamp, reason, and employee.
- Free-text substitutions parsed from a report (e.g. "190 Van Replace 155")
  are surfaced in the import preview for manual confirmation, because their
  direction is ambiguous — avoiding silent mistakes.
- **Dump tracking stays consistent with the checklist.** Ticking **Sweep**
  increments `cleanings_since_dump`; un-ticking it decrements it again; ticking
  **Dump** records a `dump` service record and resets the counter, so the
  **Needs Dump** badge reflects the work actually recorded.
- **Deleting a person never deletes their work.** A removed staff record is
  detached from the tasks, timers, notes, reports and incidents that person
  touched; those records stay and simply stop being credited to a name, so the
  history still adds up.
