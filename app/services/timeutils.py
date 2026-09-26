"""Eastern Time helpers for every timestamp the app records or displays.

Rules used across the app:
- All operational timestamps are Eastern Time (``America/New_York``), including
  daylight-saving shifts, so the prep timers, event log and reports all agree
  with the clock the crew actually works by.
- Timestamps are *stored* as ISO-8601 strings that carry their UTC offset
  (e.g. ``2026-09-25T16:05:00-04:00``). Keeping the offset means a value read
  back months later is unambiguous instead of depending on the server's local
  timezone or on whether daylight saving was in effect.
- Timestamps are *displayed* to people as normal 12-hour AM/PM Eastern time.
- Naive datetimes handed to these helpers are assumed to already be Eastern
  wall-clock time (that is how the rest of the app records "when").
"""

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

EASTERN_TZ = "America/New_York"
EASTERN = ZoneInfo(EASTERN_TZ)

# 24-hour ("military") and 12-hour clock values as they appear on prep reports,
# e.g. "04:30", "4:30", "0430", "16:05", "4:05 PM", "4:05p".
_CLOCK_RE = re.compile(
    r"^\s*(?P<hour>\d{1,2})[:.]?(?P<minute>\d{2})\s*(?P<meridiem>[ap]\.?m\.?)?\s*$",
    re.IGNORECASE)


def now_eastern():
    """The current time as an aware datetime in Eastern Time."""
    return datetime.now(EASTERN)


def parse_iso(value):
    """Parse an ISO-8601 timestamp (or datetime/date) into a datetime.

    Returns ``None`` when the value cannot be understood. Aware values keep
    their own offset; naive values are returned as-is so the caller decides
    how to interpret them (see :func:`to_eastern`).
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def to_eastern(value):
    """Return an aware Eastern datetime for a timestamp, string or datetime.

    Naive values are treated as Eastern wall-clock time; aware values are
    converted (so a UTC or ``-05:00`` timestamp shows as Eastern time).
    """
    parsed = parse_iso(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(EASTERN)


def store_ts(value=None):
    """Serialize a timestamp for storage as ISO-8601 **with** its offset."""
    moment = to_eastern(value) if value is not None else now_eastern()
    if moment is None:
        return None
    return moment.isoformat(timespec="seconds")


def load_ts(value):
    """Read a stored timestamp back as an aware Eastern datetime."""
    return to_eastern(value)


def epoch_ms(value=None):
    """Milliseconds since the epoch, for hand-off to the browser clock."""
    moment = to_eastern(value) if value is not None else now_eastern()
    if moment is None:
        return None
    return int(moment.timestamp() * 1000)


def fmt_ampm(moment, seconds=False):
    """``4:05 PM`` (or ``4:05:30 PM``) for an aware datetime."""
    suffix = "AM" if moment.hour < 12 else "PM"
    hour12 = moment.hour % 12 or 12
    text = f"{hour12}:{moment.minute:02d}"
    if seconds:
        text += f":{moment.second:02d}"
    return f"{text} {suffix}"


def fmt_time(value, seconds=False):
    """Format a timestamp as 12-hour Eastern time (``4:05 PM``)."""
    moment = to_eastern(value)
    if moment is None:
        return "—"
    return fmt_ampm(moment, seconds=seconds)


def fmt_datetime(value):
    """Format a timestamp as ``Sep 25, 4:05 PM`` Eastern time."""
    moment = to_eastern(value)
    if moment is None:
        return "—"
    return f"{moment.strftime('%b %d')}, {fmt_ampm(moment)}"


def fmt_duration(seconds):
    """Human readable duration: ``2h 05m``, ``5m 30s``, ``45s``, ``0s``."""
    try:
        total = int(round(float(seconds or 0)))
    except (TypeError, ValueError):
        return "0s"
    if total <= 0:
        return "0s"
    total = max(total, 1)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def fmt_clock(seconds):
    """Ticking timer format: ``1:05:12`` (or ``05:12`` under an hour)."""
    try:
        total = int(float(seconds or 0))
    except (TypeError, ValueError):
        total = 0
    total = max(total, 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def prep_time_label(raw):
    """Display a prep/pickup report time as 12-hour Eastern time.

    Prep reports list times in 24-hour ("military") form (``04:30``). The
    stored value is never rewritten -- this only converts what people see.
    Anything that is not a clock time (or is already 12-hour) is passed
    through untouched so nothing is ever lost.
    """
    if raw is None:
        return "—"
    text = str(raw).strip()
    if not text:
        return "—"
    if "-" in text or "T" in text:  # a full timestamp: convert to Eastern
        moment = to_eastern(text)
        if moment is not None:
            return fmt_time(moment)
    match = _CLOCK_RE.match(text)
    if not match:
        return text
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    meridiem = (match.group("meridiem") or "").replace(".", "").upper()
    if meridiem:
        if meridiem == "AM" and hour == 12:
            hour = 0
        elif meridiem == "PM" and hour != 12:
            hour += 12
        if not 0 <= hour <= 23:
            return text
    elif hour > 23 or minute > 59:
        return text
    moment = datetime(2000, 1, 1, hour % 24, minute, tzinfo=EASTERN)
    return fmt_time(moment)
