"""Fill the ECHO East Coast ACCIDENT / INCIDENT REPORT PDF template.

The company uses a 4-page paper form (the template shipped in
app/pdf_templates/ECHO_EAST_COAST_INCIDENT_REPORT.pdf) for vehicle accident /
incident reports. When a driver, detailer or manager files an incident in the
app, we stamp the recorded values onto the blank template with PyMuPDF and
save a filled copy that managers can download.

Layout notes
------------
The template has no AcroForm fields; each label is plain text followed by a
drawn underline. We place each answer just after its label baseline (the y
values below are text baselines from the template). Yes/No questions are
"circled" by drawing an ellipse around the matching YES or NO word.
"""
import os

_FITZ = None


def _get_fitz():
    """Import PyMuPDF lazily (matching the rest of the codebase)."""
    global _FITZ
    if _FITZ is None:
        try:
            import fitz
            _FITZ = fitz
        except ImportError:
            try:
                import pymupdf as fitz
                _FITZ = fitz
            except ImportError:
                _FITZ = False
    return _FITZ or None


FONT = "helv"
FONT_SIZE = 10

# --- Filled lines: field name -> (page_index, x, baseline y) -----------------
# x is where the value's baseline starts, immediately after the label text.
ONE_LINE_FILLS = {
    "driver_name": (0, 200.0, 162.9),
    "road_name": (0, 311.0, 371.9),
    "intersection": (0, 164.0, 397.4),
    "county": (0, 333.0, 422.8),
    "city": (0, 482.0, 422.8),
    "roadway_conditions": (0, 221.0, 448.4),
    "police_report_number": (0, 424.0, 346.5),
    "violation_reason": (0, 409.0, 517.5),
    "investigating_supervisor": (0, 186.0, 542.8),
    "employee_supervisor": (0, 435.0, 542.8),
    "other_driver_name": (0, 141.0, 630.8),
    "other_driver_address": (0, 149.0, 656.2),
    "other_driver_city": (0, 96.0, 685.6),
    "other_driver_state": (0, 332.0, 685.6),
    "other_driver_zip": (0, 451.0, 685.6),
    "other_driver_phone": (0, 116.0, 711.1),
    "other_driver_license": (0, 318.0, 711.1),
    "other_driver_license_state": (0, 467.0, 711.1),
    "owner_name": (1, 143.0, 158.1),
    "owner_address": (1, 116.0, 183.5),
    "owner_city": (1, 305.0, 183.5),
    "owner_state": (1, 439.0, 183.5),
    "owner_zip": (1, 504.0, 183.5),
    "vehicle_make": (1, 104.0, 248.6),
    "vehicle_model": (1, 238.0, 248.6),
    "vehicle_year": (1, 386.0, 248.6),
    "vehicle_color": (1, 478.0, 248.6),
    "license_plate": (1, 139.0, 274.1),
    "number_of_passengers": (1, 366.0, 274.1),
    "insurance_co": (1, 138.0, 354.8),
    "insurance_policy": (1, 340.0, 354.8),
    "insurance_address": (1, 116.0, 380.2),
    "insurance_city": (1, 299.0, 380.2),
    "insurance_state": (1, 421.0, 380.2),
    "insurance_zip": (1, 492.0, 380.2),
    "insurance_phone": (1, 122.0, 405.7),
    "property_owner": (1, 280.0, 620.8),
}

# --- Yes/No circles: field -> (page_index, {'Yes': rect, 'No': rect}) --------
# Rects are stored as plain tuples (x0, y0, x1, y1) and turned into fitz.Rect
# when drawing so this module imports without PyMuPDF.
YES_NO_CIRCLES = {
    "police_notified": (0, {
        "Yes": (161.1, 346.4, 181.2, 356.6),
        "No": (216.1, 346.4, 231.2, 356.6),
    }),
    "injuries_other_party": (0, {
        "Yes": (197.4, 494.2, 217.4, 504.4),
        "No": (246.7, 494.2, 261.8, 504.4),
    }),
    "employee_injured": (0, {
        "Yes": (386.6, 494.2, 406.6, 504.4),
        "No": (438.8, 494.2, 453.9, 504.4),
    }),
    "citation_received": (0, {
        "Yes": (227.5, 517.4, 247.6, 527.6),
        "No": (274.2, 517.4, 289.3, 527.6),
    }),
    "other_vehicle_driver_owned": (0, {
        "Yes": (369.1, 607.6, 389.2, 617.8),
        "No": (438.6, 607.6, 453.3, 617.8),
    }),
    "other_vehicle_injuries": (1, {
        "Yes": (467.0, 274.0, 487.1, 284.2),
        "No": (513.1, 274.0, 528.2, 284.2),
    }),
    "other_driver_ticketed": (1, {
        "Yes": (308.7, 296.9, 328.8, 307.1),
        "No": (360.7, 296.9, 375.8, 307.1),
    }),
    "property_damage": (1, {
        "Yes": (348.6, 597.8, 368.6, 608.0),
        "No": (408.8, 597.8, 423.9, 608.0),
    }),
    "hazardous_spill": (1, {
        "Yes": (460.4, 646.1, 480.4, 656.4),
        "No": (515.1, 646.1, 530.2, 656.4),
    }),
}

# --- Circle-the-answer for the "Type of Accident" line -----------------------
ACCIDENT_TYPE_CIRCLES = {
    "Collision": (179.5, 471.3, 217.5, 481.5),
    "Passengers involved": (225.5, 471.3, 281.1, 481.5),
    "Incident (no other vehicle or passengers)": (328.1, 471.3, 384.1, 481.5),
}


def template_path():
    """Absolute path of the shipped ECHO incident report template."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "pdf_templates",
                        "ECHO_EAST_COAST_INCIDENT_REPORT.pdf")


def _clean(text, max_len=60):
    """Coerce user text to a latin-1 safe string and cap its width."""
    text = _latin1(text)
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "..."
    return text


def _latin1(text):
    """Return text stripped to characters Helvetica (WinAnsi) can encode."""
    if not text:
        return ""
    if isinstance(text, bytes):
        text = text.decode("latin-1", "ignore")
    text = str(text).replace("\r", " ").replace("\n", " ")
    return "".join(c if ord(c) < 256 else " " for c in text)


def _insert_text(page, x, y, text, size=FONT_SIZE):
    if not text:
        return
    page.insert_text((x, y), text, fontsize=size, fontname=FONT,
                     color=(0, 0, 0))


def _circle(page, coords):
    # draw_ellipse was renamed draw_oval in newer PyMuPDF releases; draw_oval
    # exists across all supported versions.
    fitz = _get_fitz()
    page.draw_oval(fitz.Rect(*coords), color=(0, 0, 0), width=1.2, fill=None)


def _fill_data(incident):
    """Assemble the flat dict of values to stamp onto the template."""
    from app.services.incidents import load_echo_fields
    echo = load_echo_fields(incident)
    data = dict(echo)

    vehicle = incident.vehicle
    data["unit_number"] = vehicle.unit_number if vehicle else ""
    if vehicle and vehicle.vehicle_type and vehicle.vehicle_type.name:
        data["vehicle_type"] = vehicle.vehicle_type.name

    driver_name = data.get("driver_name") or (
        incident.reporter.name if incident.reporter else "")
    data["driver_name"] = driver_name

    if incident.occurred_at:
        data["incident_date"] = incident.occurred_at.strftime("%m/%d/%Y")
        data["incident_time"] = incident.occurred_at.strftime("%I:%M %p")
    return data


def build_incident_pdf(incident):
    """Stamp the incident's data onto the template and return the PDF bytes."""
    fitz = _get_fitz()
    if fitz is None:  # pragma: no cover
        raise RuntimeError("PyMuPDF is required to generate incident PDFs")

    doc = fitz.open(template_path())
    data = _fill_data(incident)

    # Vehicle unit # and type, date and time share row coordinates.
    _insert_text(doc[0], 141.0, 188.3, _clean(data.get("unit_number"), 20))
    _insert_text(doc[0], 332.4, 188.3, _clean(data.get("vehicle_type"), 20))
    _insert_text(doc[0], 196.0, 321.1, _clean(data.get("incident_date"), 15))
    _insert_text(doc[0], 431.0, 321.1, _clean(data.get("incident_time"), 15))

    for field, (page_idx, x, y) in ONE_LINE_FILLS.items():
        _insert_text(doc[page_idx], x, y, _clean(data.get(field), 45))

    for field, (page_idx, options) in YES_NO_CIRCLES.items():
        answer = _clean(data.get(field, ""), 6).capitalize()
        coords = options.get(answer)
        if coords:
            _circle(doc[page_idx], coords)

    acc_type = data.get("accident_type", "")
    if acc_type:
        for option, coords in ACCIDENT_TYPE_CIRCLES.items():
            if acc_type.strip().lower().startswith(option.lower()[:10]):
                _circle(doc[0], coords)
                break

    # Free-form witnesses / injured parties box (page 2 index 1).
    witnesses = data.get("witnesses")
    if witnesses:
        doc[1].insert_textbox(
            fitz.Rect(72, 442, 530, 592), _latin1(witnesses), fontsize=10,
            fontname=FONT, lineheight=1.35, color=(0, 0, 0))

    # Full description box (page index 2).
    if incident.description:
        doc[2].insert_textbox(
            fitz.Rect(72, 142, 540, 700), _latin1(incident.description),
            fontsize=11, fontname=FONT, lineheight=1.4, color=(0, 0, 0))

    out = fitz.open()
    out.insert_pdf(doc)
    doc.close()
    pdf_bytes = out.tobytes()
    out.close()
    return pdf_bytes


def save_incident_pdf(incident):
    """Generate and persist a filled incident PDF; return the file path."""
    from flask import current_app

    data = build_incident_pdf(incident)
    base = (current_app.config.get("UPLOAD_FOLDER") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "uploads"))
    folder = os.path.join(base, "incidents", "pdfs")
    os.makedirs(folder, exist_ok=True)

    unit = incident.vehicle.unit_number if incident.vehicle else "vehicle"
    safe_unit = "".join(c for c in str(unit) if c.isalnum()) or "incident"
    filename = f"ECHO_Incident_Report_{safe_unit}_{incident.id}.pdf"
    path = os.path.join(folder, filename)
    with open(path, "wb") as f:
        f.write(data)
    # Drop any stale copy under an older name.
    if incident.pdf_path and os.path.isfile(incident.pdf_path) \
            and os.path.abspath(incident.pdf_path) != os.path.abspath(path):
        try:
            os.remove(incident.pdf_path)
        except OSError:
            pass
    return path