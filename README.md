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
   Shows an **Import Preview** (new / updated / removed / route changes /
   replacements / uncertain) and requires clicking **Apply Updates** before
   anything touches the database. Multiple imports per day are allowed and
   historical data is never deleted.
3. **Daily Detailing Board** — after import, today's work list is created
   automatically. Each vehicle shows number, type, route, status, last washed,
   and progress. The task list is broken into two categories — **Inside**
   (Sweep, Mop, Windows, Seats, Bathroom) and **Outside** (Dump, Bay Checked,
   Final Inspection) — both configurable in Settings. Every checkbox saves a
   completion timestamp and the employee. Progress shows `6/8 — 75%`.
4. **Vehicle Replacements** — "Replace Vehicle" moves remaining applicable
   daily prep requirements to the replacement while preserving completed work
   and historical records. Replacements are clearly displayed on the board and
   recorded in history.
5. **Smart Status / Last Washed** — Last Washed auto-updates when the sweep
   (wash) task completes; Last Detailed updates on the final inspection.
   Configurable visual indicators: Recently Washed / Due Soon / Overdue.
6. **Dashboard** — today's totals: total, completed, in progress, remaining,
   overdue, replacements, worst overall completion %. Search & filter by unit
   number, type, route, and status.
7. **End My Day** — prominent button. Requires confirmation. Finalizes the
   day, calculates completed/incomplete, shows unfinished checklist items,
   replacements, and notes, computes completion %, and generates a clean
   printable daily summary (Print / Save as PDF) and saves the day to history.
8. **History** — previous days, vehicle cleaning history, prep report imports,
   replacements, and (per-vehicle) completed checklists.
9. **Data architecture** — real persistent database (SQLite via SQLAlchemy).
   Models: Vehicles, Employees, Daily Prep Schedules, Checklist Tasks,
   Cleaning/Service History, Vehicle Replacements, Prep Report Imports,
   Activity/Notes, Locations, Vehicle Types, Settings. Designed to support
   multiple employees and locations later (Location is a first-class model).
10. **UI** — mobile/tablet/desktop responsive, large checkboxes & buttons,
    minimal typing, clean professional interface, color-coded statuses, fast
    search, clear daily workflow.

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
python scripts/make_sample_report.py sample_prep_report.pdf
```

Then go to **Import** → choose the PDF → review the preview → **Apply Updates**.

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

Thresholds (Recently Washed / Due Soon) and the task list — split into
**Inside** and **Outside** categories — are configurable in the **Settings**
page at runtime. Per-vehicle-type checklists can override the global default.

---

## Testing

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

The test suite covers unit normalization, text-PDF parsing, import
preview/apply, the checklist & progress, replacements, end-of-day finalization,
settings, and vehicle CRUD.

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
    settings.py          # configurable thresholds/checklist
    vehicles.py          # entity helpers, import/journal records
scripts/
  make_sample_report.py  # generates a sample prep report PDF
tests/                   # pytest suite
run.py                   # local dev server
data/                    # SQLite database (created at runtime)
```

---

## Prep Report Import System

The import system lets operators upload a daily prep report PDF and have the
application automatically extract vehicle assignments, compare them against
the existing database, and build the day's work list. Nothing touches the
database until the operator explicitly confirms.

### End-to-end flow

```
Upload PDF ──► Parse & Extract ──► Preview Diff ──► Apply Updates ──► Dashboard
   /import        pdf_parser.py     build_preview()  apply_import()   /today
```

**1. Upload (`/import`)**
The operator picks a PDF file and a target schedule date (today, tomorrow,
or +2 days). The file is validated (must be a PDF, max 20 MB).

**2. Parse & Extract (`pdf_parser.py`)**
The PDF is processed by a three-layer extraction engine:

| Layer | Trigger | How it works |
|-------|---------|-------------|
| Text + table extraction | Default | PyMuPDF extracts selectable text and detects tables. ECHO-format rows
(Prep Time / Vehicle / Vehicle Type / Type) are parsed by column index.
Generic tables are parsed by scanning tokens for unit numbers. |
| Word-based scan | Non-ECHO pages | Words on the same baseline are grouped into pseudo-rows and scanned for
unit numbers, vehicle types, and routes. |
| OCR fallback | No selectable text (`total_text_chars == 0`) | Each page is rendered to a 200 DPI image via PyMuPDF and read by
Tesseract. All OCR results are flagged `uncertain=True`. |

The output is a dict of `ParsedVehicle` records keyed by normalized unit
number, plus the extraction method (`text`, `table`, or `ocr`) and any
warnings.

**3. Unit number normalization**
`normalize_unit()` strips prefixes like `BUS`, `Unit`, `veh`, `vehicle`,
`no`, `#` and trailing dashes, then extracts the numeric portion. This means
`BUS 142`, `Unit 142`, and `142` all resolve to vehicle 142.

**4. Preview diff (`schedule.py:build_preview()`)**
The parsed vehicles are compared against the database to produce a preview
with six categories:

| Category | Meaning |
|----------|---------|
| **new** | Vehicles in the PDF but not in the database |
| **updated** | Vehicles whose type or route has changed |
| **unchanged** | Vehicles that already match the database |
| **removed** | Vehicles in the database but absent from the PDF (will be deactivated) |
| **replacements** | Parsed substitution entries (e.g. "Replace 155") — surfaced for manual confirmation |
| **uncertain** | Low-confidence OCR entries flagged for manual review |

A `PrepReportImport` record is created with `applied=False`, storing the full
preview as JSON.

**5. Apply (`/import/<id>/apply`)**
When the operator clicks "Apply Updates":

- New vehicles are created via `find_or_create_vehicle()`.
- A `DailySchedule` is created for the target date + location if one doesn't
  exist.
- `ScheduleEntry` rows are created for each vehicle with prep time and display
  order.
- `TaskCompletion` checklist rows are generated per entry using the current
  task configuration (Inside + Outside categories).
- Vehicles missing from the report are deactivated (not deleted).
- The `PrepReportImport` is marked `applied=True`.

**6. Dashboard (`/today`)**
The dashboard now shows today's work list ordered by prep time (earliest
first, untimed entries last).

### Import history

All past imports are visible at `/history`. Each shows filename, date,
extraction method, and whether it was applied. Imports can be deleted — this
removes any vehicles that were created by that specific import and
re-activates any vehicles that were deactivated by it.

### Key design decisions

- **Two-step confirmation**: Nothing is written to the database until the
  operator reviews the preview and clicks Apply. This prevents bad data from
  silently entering the system.
- **Historical data is never deleted**: Service records, past checklists,
  replacements, and finalized days survive across imports.
- **Multiple imports per day are allowed**: The system merges data from
  successive imports rather than replacing the day's schedule.
- **Seeded fleet vehicles are restored on startup**: `_restore_seed_vehicles()`
  re-activates base fleet vehicles so imports can't permanently hide them.
- **Max upload size**: 20 MB (`MAX_CONTENT_LENGTH` in `app.py`).

### Sample prep report

A sample PDF can be generated for testing:

```bash
python scripts/make_sample_report.py sample_prep_report.pdf
```

Then upload it at Import → review the preview → Apply Updates.

### Source files

| File | Purpose |
|------|---------|
| `app/services/pdf_parser.py` | PDF parsing engine (text, table, OCR) |
| `app/services/schedule.py:build_preview()` | Compares parsed data against the database |
| `app/services/schedule.py:apply_import()` | Creates vehicles, schedule entries, and tasks |
| `app/services/vehicles.py` | Vehicle CRUD, import record management |
| `app/app.py` (routes `/import`, `/import/<id>/apply`, `/import/<id>/delete`) | Upload, preview, apply, and delete endpoints |
| `app/templates/import.html` | Upload form UI |
| `app/templates/import_preview.html` | Preview diff UI |

---

## How replacements & history integrity work

- **The PDF controls the daily schedule; the database controls permanent
  history.** Importing a new prep report never overwrites or deletes cleaning
  history (`service_records`), past checklists, replacements, or finalized
  days.
- Vehicles that disappear from a report are **deactivated**, not deleted, so
  their history remains.
- When a vehicle is replaced, its completed tasks and timestamps are carried
  forward onto the replacement entry; the original entry and its completed work
  are preserved as historical records, and a `Replacement` row is written with
  original, replacement, timestamp, reason, and employee.
- Free-text substitutions parsed from a report (e.g. "190 Van Replace 155")
  are surfaced in the import preview for manual confirmation, because their
  direction is ambiguous — avoiding silent mistakes.
