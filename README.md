# Detailing Operations Dashboard (static rebuild)

A responsive, client-side **static app** that replaces the paper vehicle prep
report for a detailing operation. It manages the daily prep checklist, tracks
cleaning history, handles vehicle substitutions, and generates an
end-of-day printable summary.

Built as a single-page static app — **no server, no database, no build step**.
It runs from plain HTML/CSS/JS and can be hosted on any static host (GitHub
Pages, Netlify, S3, or opened directly from disk). All data lives in the
browser's `localStorage` on whatever device you use it from.

---

## Running it

Just serve the folder statically. For example:

```bash
# any static file server works
python3 -m http.server 8000
# then open http://localhost:8000
```

Or open `index.html` directly in a browser. Data is stored per-browser in
`localStorage` — the app seeds a small demo board on first launch so it is
immediately usable.

**Export a backup** on the Settings page to download your data as JSON.
**Reset** clears local data and reloads the demo data.

---

## Features

1. **Today's Board** — daily work list with totals: total, completed, in
   progress, remaining, overdue, replacements, and overall completion %.
   Search & filter by unit number, type, route, and status.
2. **Daily Detailing Checklist** — each vehicle shows number, type, route,
   status, last washed, and progress (`6/8 — 75%`). Large tap-friendly
   checkboxes: Sweep, Mop, Windows, Seats, Bathroom, Dump, Bay Checked,
   Final Inspection. Completed tasks are timestamped and attributed to the
   selected employee.
3. **Smart Status / Last Washed** — Last Washed auto-updates when Sweep
   completes; Last Detailed updates on Final Inspection. Configurable
   Recently Washed / Due Soon / Overdue indicators.
4. **Vehicle Replacements** — "Replace Vehicle" moves completed work forward
   onto the replacement while preserving the original as a historical record.
   Replacements are shown on the board and in History.
5. **Vehicle database** — add/edit vehicles from the Vehicles page. Each
   vehicle has type, route, status, cleaning frequency, notes, and a detail
   page with its service history and replacements.
6. **End My Day** — prominent button with confirmation. Calculates completion,
   shows unfinished checklist items, replacements, and notes, and generates a
   printable daily summary (Print / Save as PDF) that is also saved to history.
7. **History** — previous finalized days, vehicle cleaning history, and
   replacements.
8. **Staff & Settings** — add employees (checked tasks are attributed to the
   selected "I am" employee), configure the checklist and the wash thresholds,
   and export/reset data.

> Note: the former Python/Flask app imported the company's prep report PDF.
> In this static rebuild there is no server-side file parsing; today's work
> list is built by adding vehicles on the **Add Vehicle** page instead.

---

## Project layout

```
index.html          # single-page app shell
css/style.css       # styles (reused from the original design)
js/app.js           # data store (localStorage) + all views + actions
```

---

## Mobile / touch

The board is mobile-friendly with large checkboxes and buttons, a
horizontally scrollable nav, and responsive stat cards — same look as the
original.