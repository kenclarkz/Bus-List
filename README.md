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
   and progress. Large tap-friendly checklist: Sweep, Mop, Windows, Seats,
   Bathroom, Dump, Bay Checked, Final Inspection. Every checkbox saves a
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

## OCR / scanned PDFs (optional)

The importer extracts selectable text and tables with **PyMuPDF** out of the
box. For scanned (image-only) PDFs it falls back to OCR. To enable OCR,
install the extra dependencies and the system tools:

```bash
# Python packages
pip install pdf2image pytesseract Pillow

# System tools
# Ubuntu/Debian:
sudo apt-get install -y poppler-utils tesseract-ocr
# macOS: brew install poppler tesseract
```

If OCR is unavailable, the app degrades gracefully: it warns that a scanned
PDF could not be read and asks for manual review rather than guessing.

---

## Configuration

Database and secret are configured via environment variables (see
`.env.example`):

| Variable | Default |
|----------|---------|
| `SECRET_KEY` | `dev-secret-change-me` |
| `DATABASE_URL` | `sqlite:///data/detail.db` |
| `PORT` | `5000` |

Thresholds (Recently Washed / Due Soon) and the checklist are configurable in
the **Settings** page at runtime.

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
