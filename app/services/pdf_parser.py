"""Prep report PDF parsing and extraction.

Layered extraction strategy:
1. Text extraction with PyMuPDF (handles text-based PDFs and basic tables).
2. Table extraction using PyMuPDF's built-in table detection.
3. OCR fallback using pdf2image + pytesseract when the PDF is a scan
   (images instead of selectable text). OCR is best-effort: if the OCR
   binaries are not available it degrades gracefully and the import preview
   flags that OCR could not run so the operator can review manually.

Everything that cannot be confidently resolved is returned with an
"uncertain" flag so the preview can prompt for manual review instead of
guessing.
"""

import io
import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unit number normalization
# ---------------------------------------------------------------------------

UNIT_RE = re.compile(r"(?i)(?:unit|bus|veh|vehicle|no|#)?\s*[\s.#-]{0,3}(\d{2,6})(?:\D|$)")


def normalize_unit(raw):
    """Normalize 'BUS 142', 'Unit 142', '142', '9205-' to '142' / '9205'."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Strip trailing dashes that some report formats add to unit numbers
    text = text.rstrip("-").strip()
    # Strong match for a bus/unit number token
    m = UNIT_RE.search(text)
    if m:
        return m.group(1).lstrip("0") or "0"
    return None


def normalize_route(raw):
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def normalize_type(raw):
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.isupper() and len(text) > 1:
        return text
    return text.title()


# ---------------------------------------------------------------------------
# Parsed record dataclass
# ---------------------------------------------------------------------------


class ParsedVehicle:
    __slots__ = ("unit", "type", "route", "raw", "uncertain", "prep_time")

    def __init__(self, unit, type=None, route=None, raw=None, uncertain=False,
                 prep_time=None):
        self.unit = unit
        self.type = type
        self.route = route
        self.raw = raw
        self.uncertain = uncertain
        self.prep_time = prep_time

    def to_dict(self):
        return {
            "unit": self.unit,
            "type": self.type,
            "route": self.route,
            "raw": self.raw,
            "uncertain": self.uncertain,
            "prep_time": self.prep_time,
        }


# ---------------------------------------------------------------------------
# Table / text helpers
# ---------------------------------------------------------------------------

def _find_table_cells(page):
    """Return dict: {unit: ParsedVehicle}. Uses PyMuPDF tables + word scan."""
    found = {}

    # 1. Built-in table detection
    is_echo = False
    try:
        tabs = page.find_tables()
        for tab in tabs:
            data = tab.extract()
            for row in data:
                if _is_echo_header(row):
                    is_echo = True
                    continue
                if _is_echo_date_row(row):
                    continue
                _cells_to_vehicle(row, found, echo=is_echo)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Table detection failed: %s", exc)

    # 2. Word-based scan for non-ECHO pages (captures plain-text lists too)
    # Skip on ECHO pages: the table extraction already handles all rows,
    # and the word scan would pick up headers/page numbers as false positives.
    if not is_echo:
        words = page.get_text("words")
        _words_to_vehicle(words, found)

    return found


def _is_echo_header(row):
    """Detect ECHO prep report header: ['Prep Time', 'Vehicle', 'Vehicle Type', ...]"""
    if not row or len(row) < 3:
        return False
    vals = [str(c).strip().replace("\n", " ") if c else "" for c in row]
    return vals[0] == "Prep Time" and vals[1] == "Vehicle" and vals[2] == "Vehicle Type"


def _is_echo_date_row(row):
    """Detect ECHO date/day header row: ['08/31/2026', None, 'MONDAY', ...]"""
    if not row:
        return False
    first = str(row[0]).strip() if row[0] else ""
    return bool(re.match(r"\d{2}/\d{2}/\d{4}", first))


def _row_tokens_to_vehicle(tokens, found):
    """Given an ordered list of string tokens from one table row / text line,
    extract unit, type and route (best-effort). Ties each token to a unit."""
    if not tokens:
        return
    tokens = [t.strip() for t in tokens if t and t.strip()]
    unit = None
    unit_idx = None
    for idx, tok in enumerate(tokens):
        u = normalize_unit(tok)
        if u and re.search(r"\d{2,6}", tok):
            unit = u
            unit_idx = idx
            break
    if unit is None:
        return
    # type = the token right after the unit, when it looks like a category word
    vt = None
    route_tokens = []
    if unit_idx + 1 < len(tokens):
        nxt = tokens[unit_idx + 1]
        if nxt.lower() not in ("replace", "replacement", "bus", "unit", "with"):
            vt = normalize_type(nxt)
            route_tokens = tokens[unit_idx + 2:]
        else:
            route_tokens = tokens[unit_idx + 1:]
    else:
        route_tokens = tokens[unit_idx + 1:]

    route = normalize_route(" ".join(route_tokens))
    if route and re.fullmatch(r"(?i)route|assignment|location|status", route):
        route = None

    raw = " ".join(tokens)
    existing = found.get(unit)
    if existing is None:
        found[unit] = ParsedVehicle(unit, type=vt, route=route, raw=raw)
    else:
        # keep richer data if we already have it
        if not existing.type and vt:
            existing.type = vt
        if not existing.route and route:
            existing.route = route


def _cells_to_vehicle(row, found, echo=False):
    cols = []
    for cell in row:
        value = cell
        if isinstance(cell, (tuple, list)):
            value = " ".join(str(x) for x in cell if x)
        cols.append(str(value).strip() if value else "")

    if echo and len(cols) >= 4:
        _echo_row_to_vehicle(cols, found)
        return

    # flatten cell strings into tokens in order
    tokens = []
    for c in cols:
        tokens.extend(c.split())
    _row_tokens_to_vehicle(tokens, found)


def _echo_row_to_vehicle(cols, found):
    """Parse an ECHO prep report data row by column index.

    Columns: 0=Prep Time, 1=Vehicle, 2=Vehicle Type, 3=Type, 4=Trips#, ...
    Vehicle cell format: '9205-\\nJAXSUV' (unit on first line, location code below).
    """
    raw = " | ".join(c for c in cols if c)

    # Column 0: Prep Time — the time the vehicle needs to be detailed
    prep_time = normalize_route(cols[0]) if len(cols) > 0 and cols[0] else None

    # Column 1: Vehicle — unit number is first line before the newline
    vehicle_cell = cols[1]
    unit_lines = [l.strip() for l in vehicle_cell.split("\n") if l.strip()]
    unit_line = unit_lines[0] if unit_lines else vehicle_cell
    unit = normalize_unit(unit_line)
    if not unit:
        return

    # Column 2: Vehicle Type (e.g. SUVSUB, TRANSITB, MINIBUS)
    vt = normalize_type(cols[2]) if cols[2] else None
    if vt and re.fullmatch(r"(?i)route|assignment|location|status", vt):
        vt = None

    # Column 3: Type / service assignment (e.g. Shuttle, Hourly, Airport Arrival)
    route = normalize_route(cols[3]) if cols[3] else None
    if route and re.fullmatch(r"(?i)route|assignment|location|status", route):
        route = None

    existing = found.get(unit)
    if existing is None:
        found[unit] = ParsedVehicle(unit, type=vt, route=route, raw=raw,
                                    prep_time=prep_time)
    else:
        if not existing.type and vt:
            existing.type = vt
        if not existing.route and route:
            existing.route = route
        if not existing.prep_time and prep_time:
            existing.prep_time = prep_time


def _words_to_vehicle(words, found):
    # group words on the same baseline (y) into rows, ordered by x
    lines = {}
    for x0, y0, x1, y1, word, block, ln, wno in words:
        key = round(y0, 1)
        lines.setdefault(key, []).append((x0, word))
    for y, items in lines.items():
        items.sort()
        toks = [w for _, w in items]
        _row_tokens_to_vehicle(toks, found)


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def extract_vehicles_from_pdf(file_bytes, filename=""):
    """Extract vehicles from a PDF.

    Returns (vehicles, method, warnings) where vehicles is an ordered dict keyed
    by normalized unit number.
    """
    warnings = []
    method = "text"
    found = {}

    try:
        import fitz  # PyMuPDF
    except ImportError:
        warnings.append("PDF text engine unavailable.")
        return found, method, warnings

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        warnings.append(f"Could not open PDF: {exc}")
        return found, method, warnings

    for page in doc:
        try:
            page_found = _find_table_cells(page)
            for u, v in page_found.items():
                found.setdefault(u, v)
        except Exception as exc:  # pragma: no cover
            logger.warning("page parse failed: %s", exc)

    total_text_chars = sum(len(p.get_text()) for p in doc)

    # If no selectable text, fall back to OCR on rendered images.
    if not found and total_text_chars == 0:
        method = "ocr"
        ocr_result, ocr_warnings = _ocr_document(doc)
        found = ocr_result
        warnings.extend(ocr_warnings)
    elif not found:
        warnings.append(
            "Vehicles could not be parsed from the PDF. Review manually."
        )

    return found, method, warnings


def _ocr_document(doc):
    """Best-effort OCR. Requires poppler-utils + tesseract installed."""
    found = {}
    warnings = []
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
        from PIL import Image
    except ImportError:
        warnings.append(
            "Scanned PDF (no selectable text), but OCR libraries are not "
            "installed. Please review manually."
        )
        return found, warnings

    try:
        import fitz
        images = []
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=200)
            images.append(
                Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            )
        text = pytesseract.image_to_string(
            images[0] if len(images) == 1 else Image.new("RGB", (1, 1))
        ) if len(images) == 1 else ""

        if len(images) > 1:
            full_text = "\n".join(
                pytesseract.image_to_string(img) for img in images
            )
        else:
            full_text = text

        for line in full_text.splitlines():
            cols = [c for c in re.split(r"\s{2,}|\t|  +", line) if c]
            unit = None
            for idx, col in enumerate(cols):
                u = normalize_unit(col)
                if u and re.search(r"\d{2,6}", col):
                    unit = u
                    vt = normalize_type(cols[idx + 1]) if idx + 1 < len(cols) else None
                    rt = normalize_route(cols[idx + 2]) if idx + 2 < len(cols) else None
                    if rt and re.fullmatch(r"(?i)route|assignment|location|status", rt):
                        rt = None
                    found[unit] = ParsedVehicle(unit, type=vt, route=rt, raw=line,
                                                uncertain=True)
                    break
    except Exception as exc:  # pragma: no cover
        logger.warning("OCR failed: %s", exc)
        warnings.append(f"OCR failed: {exc}")

    if not found:
        warnings.append("OCR produced no vehicle matches. Review manually.")
    return found, warnings


def parse_prep_report(file_bytes, filename=""):
    """Public API: returns (vehicles_dict, method, warnings)."""
    return extract_vehicles_from_pdf(file_bytes, filename)
