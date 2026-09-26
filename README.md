# Detailing Operations Dashboard

A production-ready, responsive web app that replaces the paper vehicle
**prep report** for a detailing operation. It manages the daily prep report,
tracks cleaning history, handles vehicle substitutions, and generates an
end-of-day printable summary.

Built as a real working application with a persistent database, PDF parsing &
OCR, an import preview/approval flow, a daily checklist board, replacement
logic, history views, a searchable dashboard, and a printable **End My Day**
report. It is not a static mockup.

---

## Features (mapped to the requirements)

1. **Vehicle database** — unit number, type, route, status/location, last
   washed, last detailed, cleaning frequency, notes, active/inactive. Click a
   vehicle to see its complete service history and replacements.
2. **PDF Prep Report Import** — upload the company's PDF prep report. Auto
   extracts unit numbers, types, routes, and substitutions. Supports text
   PDFs, tabular reports, and scanned PDFs via OCR (best-effort). Unit numbers
   are normalized so `BUS 142`, `Unit 142`, and `142` all match vehicle 142.
   Both the **ECHO prep report** (`sample_prep_report.pdf`) and the
   **Vehicle Wash Report** (`sample_vehicle_wash_report.pdf`) templates are
   supported — either one can be uploaded. Reports imported from the wash
   template carry the report (prep) time, pickup time and driver code onto the
   board. Shows an **Import Preview** (new / updated / removed / route
   changes / replacements / uncertain) and requires clicking **Apply
   Updates** before anything touches the database. Multiple imports per day
   are allowed and historical data is never deleted. Vehicles whose type is
   **TRANSITB** (transit buses) are imported and shown, but skipped
   automatically — they are washed by another crew — and are collected in
   their own dropdown at the bottom of the board.
3. **Daily Detailing Board** — after import, today's work list is created
   automatically. Each vehicle shows number, type, route, status, last washed,
   and progress. The task list is broken into two categories — **Inside**
   (Sweep, Mop, Windows, Seats, Bathroom) and **Outside** (Dump, Bay Checked,
   Final Inspection) — both configurable in Settings. Every checkbox saves a
   completion timestamp and the employee. Progress shows `6/8 — 75%`.
4. **Per-vehicle prep timer (Start / Pause / Resume / Done)** — each vehicle on
   the board gets its own clock, recorded against the employee doing the work.
   **Start** begins the clock and puts the vehicle in progress; **Pause** and
   **Resume** stop and restart it without ever losing the time already worked;
   **Done** stops the clock, freezes that vehicle's total active prep time, and
   finishes the vehicle. Only the buttons valid for the current state are shown,
   and an out-of-order action (starting a vehicle that is already running,
   finishing one that was never started, resuming a running timer) is refused
   with a clear reason instead of corrupting the record. Time is tracked while
   the page is closed — the server owns the clock, so a refresh, a backgrounded
   tab, or a device with a wrong clock never loses or invents time. Every
   Start/Pause/Resume/Done is kept as permanent event history for the vehicle
   (shown on the board, the vehicle's history page, and the report), and
   completing a vehicle any other way (full checklist, End My Day) stops its
   clock too. All times are recorded and displayed in **Eastern Time**
   (America/New_York), including the report's 24-hour prep/pickup times, which
   are displayed as 12-hour AM/PM without altering the stored values.
5. **Vehicle Replacements** — "Replace Vehicle" moves remaining applicable
   daily prep requirements to the replacement while preserving completed work
   and historical records. Replacements are clearly displayed on the board and
   recorded in history.
6. **Smart Status / Last Washed** — Last Washed auto-updates when all Outside
   tasks for the vehicle are complete; Inside tasks do not count as a wash.
   Last Detailed updates on the final inspection. Configurable visual
   indicators: Recently Washed / Due Soon / Overdue.
7. **Dashboard** — today's totals: total, completed, in progress, remaining,
   overdue, replacements, worst overall completion %, and the day's total
   **active prep time**. A "Now Working" strip shows every employee currently
   on the floor with their vehicle and a live clock. Search & filter by unit
   number, type, route, and status. Each vehicle row shows its report (prep)
   time, pickup time, and driver code when the wash report supplied them
   (displayed as 12-hour AM/PM Eastern time).
   **Transit buses** (TRANSITB) are pulled out of the main work list into a
   **Transit Buses** dropdown at the bottom of the board, so they stay visible
   without crowding the list; un-skip one to work it here.
8. **End My Day** — prominent button. Requires confirmation. Finalizes the
   day, stops any timer still running, calculates completed/incomplete, shows
   unfinished checklist items, replacements, and notes, computes completion %,
   and generates a clean printable daily summary (Print / Save as PDF) and
   saves the day to history. The printable report includes a **Prep Time Log**
   with each vehicle's Start/Pause/Resume/Done history, a **Prep Event
   Detail** section, and the day's **total active prep time**.
   If no employee ends the day by **11:50 PM** local time, an automatic
   end-of-day job finalizes it with the exact same summary (no work is ever
   lost; the cutoff is configurable via `AUTO_END_DAY_TIME`).
9. **History** — previous days, vehicle cleaning history, prep report imports,
   replacements, and (per-vehicle) completed checklists. Every vehicle's page
   also keeps its permanent **Prep Time History**: one row per run with the
   employee, status, start, finish, total active prep time, and the full
   Start/Pause/Resume/Done event log.
10. **Data architecture** — real persistent database (SQLite via SQLAlchemy).
    Models: Vehicles, Employees, Daily Prep Schedules, Checklist Tasks,
    Prep Sessions & Prep Session Events, Cleaning/Service History, Vehicle
    Replacements, Prep Report Imports,
    Activity/Notes, Locations, Vehicle Types, Settings, and **Incident
    Reports** (with notes & photos). Designed to support multiple employees
    and locations later (Location is a first-class model).
11. **UI** — mobile/tablet/desktop responsive, large checkboxes & buttons,
    minimal typing, clean professional interface, color-coded statuses, fast
    search, clear daily workflow. Two site layouts are available and can be
    switched per user in **Settings**: **Classic** (top navigation bar, the
    default design) and **Side Panel** (a new design with a fixed sidebar
    navigation, sticky toolbar and wider content column). Layouts are
independent of the color themes (Light / Dark / System / Futuristic /
     Halloween / Bloomberg Terminal / Retro 90s / Holographic).
12. **Incident Reports** — a dedicated tab where anyone can report an issue
    for any vehicle: type (Mechanical / Interior / Exterior / Damage /
    Safety / Other), severity, location, description, date/time, and employee,
    with one or more photo uploads. Managers can review, edit, assign, add
    notes, attach extra photos, and resolve issues (Open → In Progress →
    Resolved). Every incident is permanently linked to the vehicle's history
    page, and the list supports filters by vehicle, issue type, severity,
    employee, and status. Incident reports follow the official **ECHO East
    Coast Accident/Incident Report** form: the app's form captures every field
    on that template, and a **Download PDF** button renders the completed
    report onto the blank form for managers to save, print, or email.

---

## Quick start

Requires Python 3.9+.

```bash
pip install -r requirements.txt
python run.py
```

Open http://127.0.0.1:5000

A default **Main Depot** location and a **User** employee are created the
first time the app runs.

### Generate a sample prep report (to try Import)

```bash
pip install -r requirements.txt     # needs PyMuPDF
python scripts/make_sample_report.py sample_prep_report.pdf          # ECHO prep report
python scripts/make_sample_report.py --wash sample_vehicle_wash_report.pdf  # Vehicle Wash Report
```

The repository also ships ready-made copies of both templates
(`sample_prep_report.pdf` and `sample_vehicle_wash_report.pdf`). Either one
can be uploaded via **Import** → choose the PDF → review the preview →
**Apply Updates**.

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

## Configuration

Database and secret are configured via environment variables (see
`.env.example`):

| Variable | Default |
|----------|---------|
| `SECRET_KEY` | `dev-secret-change-me` |
| `DATABASE_URL` | `sqlite:///data/detail.db` |
| `PORT` | `5000` |
| `AUTO_END_DAY_TIME` | `23:50` (local time the auto end-of-day job runs) |

Thresholds (Recently Washed / Due Soon), the task list — split into
**Inside** and **Outside** categories — the color theme, and the site layout
(**Classic** top-bar design or the newer **Side Panel** sidebar design) are all
configurable in the **Settings** page at runtime. Themes and layouts are saved
per account, so each user keeps their own appearance. Per-vehicle-type
checklists can override the global default.

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
output.

---

## Project layout

```
app/
  app.py                 # Flask app factory + routes
  models.py              # SQLAlchemy models (schema)
  static/                # CSS + JS
  templates/             # Jinja2 templates
  services/
    pdf_parser.py        # PDF text/table/OCR extraction + unit normalization
    schedule.py          # daily board, checklist, replacements, preview/apply
    prep_timer.py        # per-vehicle Start/Pause/Resume/Done timer + history
    settings.py          # configurable thresholds/checklist
    timeutils.py         # Eastern Time storage, parsing and display helpers
    vehicles.py          # entity helpers, import/journal records
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

- **One session per vehicle per day.** A `PrepSession` stores the running total
  in seconds, the status (`running` / `paused` / `finished`), the start and
  finish timestamps, and the employee. Each Start/Pause/Resume/Done is also
  appended to a `PrepSessionEvent` with its own Eastern timestamp, the employee
  who pressed it, and the running total at that moment.
- **Active time is the sum of the active segments only.** Pausing banks the
  seconds worked so far; the paused stretch is never billed. Resuming starts a
  new segment on top of the banked total, so previous work is never lost.
- **The browser only displays.** Each running timer is rendered with the
  seconds already banked plus the exact moment its current segment began, and
  the page carries the server's clock. The browser adds the two together each
  second. A refresh re-syncs from the server, and a tab coming back to the
  foreground re-syncs too, so time spent with the page closed or hidden is
  added — never lost — and a device with a wrong clock is corrected.
- **Only valid actions are offered or accepted.** Starting a running vehicle,
  resuming a running timer, pausing a finished one, or finishing a vehicle that
  was never started are all rejected with an explanation and leave the record
  untouched. Managers get a read-only board.
- **Nothing runs forever.** Finishing a timer is not the only way a clock stops:
  completing a vehicle through its checklist, or ending the day, stops any
  timer still running for that vehicle.
- **Eastern Time.** Every recorded timestamp is stored with its Eastern UTC
  offset (`-05:00` in winter, `-04:00` in summer) and displayed as 12-hour
  AM/PM, so a report can always be read unambiguously. The report's original
  24-hour prep and pickup times are converted for display only — what was
  imported is what is stored.

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
