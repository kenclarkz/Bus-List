"""Render a completed incident onto the ECHO Accident/Incident Report form.

The paper template (incident_report_template.pdf) is a plain, non-fillable
PDF. When staff record an incident in the app we collect all of the template's
fields; this module stamps those values onto a copy of the blank template at
the measured field locations so managers can download a filled-in copy that
matches the form exactly.

Coordinates were measured from the template with PyMuPDF. Each text field is
``(page_index, x, baseline_y, max_width)`` where the point is the text origin
(baseline) in PDF points. Boolean flag fields point at where an "X" should be
placed next to the YES / NO labels.
"""
import os

try:
    import pymupdf
except ImportError:  # PyMuPDF < 1.24.3 exposes the legacy `fitz` module
    import fitz as pymupdf

__all__ = ["render_incident_pdf", "TEMPLATE_PATH"]

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "incident_report_template.pdf")

FONT = "helv"          # Helvetica (equivalent to the template's Arial)
FONT_SIZE = 10



# line fields: page_index, x, baseline_y, max_width
TEXT_FIELDS = {
    "driver_name": (0, 204, 173, 300),
    "accident_date": (0, 198, 331, 95),
    "accident_time": (0, 433, 331, 105),
    "police_report_number": (0, 425, 356, 115),
    "road_name": (0, 313, 381, 225),
    "intersection_with": (0, 167, 406, 373),
    "county_parish": (0, 335, 432, 80),
    "city_town": (0, 483, 432, 55),
    "roadway_conditions": (0, 223, 458, 145),
    "violation_reason": (0, 412, 527, 130),
    "investigating_supervisor": (0, 188, 552, 128),
    "employee_supervisor": (0, 438, 552, 105),
    "other_driver_name": (0, 143, 640, 220),
    "other_driver_address": (0, 152, 665, 210),
    "other_driver_city": (0, 97, 695, 200),
    "other_driver_state": (0, 334, 695, 85),
    "other_driver_zip": (0, 453, 695, 85),
    "other_driver_phone": (0, 118, 720, 115),
    "other_driver_license": (0, 320, 720, 105),
    "other_driver_license_state": (0, 470, 720, 70),
    "owner_name": (1, 146, 167, 130),
    "owner_address": (1, 118, 193, 160),
    "owner_city": (1, 284, 193, 120),
    "owner_state": (1, 412, 193, 60),
    "owner_zip": (1, 482, 193, 55),
    "other_make": (1, 106, 258, 95),
    "other_model": (1, 207, 258, 145),
    "other_year": (1, 361, 258, 75),
    "other_color": (1, 445, 258, 95),
    "other_plate": (1, 141, 283, 110),
    "other_passengers": (1, 368, 283, 50),
    "insurance_company": (1, 140, 364, 190),
    "insurance_policy": (1, 397, 364, 142),
    "insurance_address": (1, 118, 389, 155),
    "insurance_city": (1, 279, 389, 105),
    "insurance_state": (1, 394, 389, 65),
    "insurance_zip": (1, 470, 389, 65),
    "insurance_phone": (1, 124, 415, 410),
    "owner_object_struck": (1, 283, 630, 255),
}

# Derived from the vehicle / timestamp rather than a stored column.
AUTO_TEXT_FIELDS = ("unit_number", "vehicle_type", "accident_date",
                    "accident_time")

# boolean checkbox fields: (page_index, (x, baseline_y) for YES, for NO)
CHECK_FIELDS = {
    "police_notified": (0, (187, 356), (237, 356)),
    "injuries_other_party": (0, (222, 504), (267, 504)),
    "employee_injured": (0, (412, 504), (459, 504)),
    "employee_citation": (0, (253, 527), (295, 527)),
    "other_driver_is_owner": (0, (395, 617), (459, 617)),
    "other_injuries": (1, (492, 283), (534, 283)),
    "other_driver_ticketed": (1, (334, 306), (381, 306)),
    "property_damage": (1, (374, 607), (429, 607)),
    "hazmat_spill": (1, (486, 656), (536, 656)),
}

# accident_type radio-style field: value -> (page_index, x, baseline_y)
ACCIDENT_TYPE_MARKS = {
    "Collision": (0, 222, 480),
    "Passengers involved": (0, 326, 480),
    "Incident": (0, 368, 480),
}

# block text fields: page_index, rectangle bounding box
TEXT_AREAS = {
    "witnesses": (1, pymupdf.Rect(72, 438, 540, 590)),
    "description": (2, pymupdf.Rect(72, 148, 540, 680)),
}

# Model columns that map onto the form (in display order).
MODEL_FIELD_ORDER = (
    "driver_name", "police_report_number", "road_name", "intersection_with",
    "county_parish", "city_town", "roadway_conditions", "violation_reason",
    "investigating_supervisor", "employee_supervisor", "other_driver_name",
    "other_driver_address", "other_driver_city", "other_driver_state",
    "other_driver_zip", "other_driver_phone", "other_driver_license",
    "other_driver_license_state", "owner_name", "owner_address", "owner_city",
    "owner_state", "owner_zip", "other_make", "other_model", "other_year",
    "other_color", "other_plate", "other_passengers", "insurance_company",
    "insurance_policy", "insurance_address", "insurance_city",
    "insurance_state", "insurance_zip", "insurance_phone",
    "owner_object_struck", "witnesses",
)


def render_incident_pdf(incident, out_path=None):
    """Create a filled copy of the ECHO incident report template.

    ``incident`` is an IncidentReport row. Returns bytes unless ``out_path``
    is given, in which case the PDF is written there and the path returned.
    The template is always left untouched.
    """
    doc = pymupdf.open(TEMPLATE_PATH)
    values = _collect_values(incident)

    for field, (page_idx, x, base_y, max_width) in TEXT_FIELDS.items():
        text = values.get(field)
        if text:
            _insert_line(doc[page_idx], x, base_y, text, max_width)

    for field, (page_idx, yes_pos, no_pos) in CHECK_FIELDS.items():
        if values.get(field) is True:
            _insert_mark(doc[page_idx], *yes_pos)
        elif values.get(field) is False:
            _insert_mark(doc[page_idx], *no_pos)

    atype = values.get("accident_type")
    if atype in ACCIDENT_TYPE_MARKS:
        _insert_mark(doc[ACCIDENT_TYPE_MARKS[atype][0]],
                     ACCIDENT_TYPE_MARKS[atype][1], ACCIDENT_TYPE_MARKS[atype][2])

    for field, (page_idx, rect) in TEXT_AREAS.items():
        text = values.get(field)
        if text:
            _insert_block(doc[page_idx], rect, text)

    if out_path:
        doc.save(out_path, deflate=True)
        doc.close()
        return out_path
    data = doc.tobytes(deflate=True, garbage=3)
    doc.close()
    return data


def _collect_values(incident):
    """Resolve the incident's record into the field values the form wants."""
    vehicle = getattr(incident, "vehicle", None)

    def text(attr):
        value = getattr(incident, attr, None)
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        value = str(value).strip()
        return value or None

    values = {
        attr: text(attr) for attr in MODEL_FIELD_ORDER
        if hasattr(incident, attr)
    }
    # Boolean columns shouldn't appear as dotted "True/False" strings.
    for attr in CHECK_FIELDS:
        values[attr] = getattr(incident, attr, None)
    # Handled by their own marks / blocks rather than the line fields.
    values["accident_type"] = text("accident_type")
    values["description"] = text("description")

    values["unit_number"] = vehicle.unit_number.strip() if vehicle else None
    values["vehicle_type"] = (
        vehicle.vehicle_type.name.strip() if vehicle and vehicle.vehicle_type
        else None)

    if incident.occurred_at:
        values["accident_date"] = incident.occurred_at.strftime("%m/%d/%Y")
        values["accident_time"] = incident.occurred_at.strftime("%I:%M %p")
    return values


def _insert_line(page, x, base_y, text, max_width):
    """Insert a single line of text, shrinking to fit its available width."""
    size = FONT_SIZE
    font = pymupdf.Font("helv")
    needed = font.text_length(text, fontsize=size) or 1
    if needed > max_width and size > 6.0:
        size = max(6.0, size * max_width / needed)
    page.insert_text((x, base_y), text, fontsize=size, fontname=FONT,
                     color=(0, 0, 0))


def _insert_mark(page, x, base_y):
    """Stamp an 'X' next to a circled YES / NO choice."""
    page.insert_text((x, base_y), "X", fontsize=12, fontname="helv",
                     color=(0, 0, 0))


def _insert_block(page, rect, text):
    """Write a paragraph into a bounded block (wrapped, top-aligned)."""
    text = text.replace("\r\n", "\n")
    page.insert_textbox(rect, text, fontsize=FONT_SIZE, fontname=FONT,
                        align=pymupdf.TEXT_ALIGN_LEFT, lineheight=1.25,
                        color=(0, 0, 0))