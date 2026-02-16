"""Parse the user's Lightning Bolt iCal subscription feed to extract scheduled shifts."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar
import recurring_ical_events

from src.models import Shift

logger = logging.getLogger(__name__)

# All times are converted to this timezone for consistent comparison
# with the scraper output (which produces naive local times)
LOCAL_TZ = ZoneInfo("America/Chicago")

# Configuration from environment variables
MIN_REST_HOURS = int(os.getenv("MIN_REST_HOURS", "8"))
ICAL_LOOKAHEAD_DAYS = int(os.getenv("ICAL_LOOKAHEAD_DAYS", "180"))


def fetch_my_shifts(ical_url: str, lookahead_days: int | None = None) -> list[Shift]:
    """Fetch and parse the user's iCal feed, returning their scheduled shifts.

    Args:
        ical_url: The iCal subscription URL from Lightning Bolt.
        lookahead_days: How many days ahead to look for shifts (defaults to ICAL_LOOKAHEAD_DAYS env var).

    Returns:
        List of Shift objects for the user's scheduled shifts.
    """
    if lookahead_days is None:
        lookahead_days = ICAL_LOOKAHEAD_DAYS

    logger.info(f"Fetching iCal feed (lookahead: {lookahead_days} days)...")
    response = requests.get(ical_url, timeout=30)
    response.raise_for_status()

    cal = Calendar.from_ical(response.content)

    start_date = date.today()
    end_date = start_date + timedelta(days=lookahead_days)

    events = recurring_ical_events.of(cal).between(start_date, end_date)

    shifts: list[Shift] = []
    for event in events:
        dtstart = event.get("DTSTART")
        dtend = event.get("DTEND")
        summary = event.get("SUMMARY", "")

        if not dtstart or not dtend:
            continue

        start_dt = dtstart.dt
        end_dt = dtend.dt

        # Convert date objects to datetime if needed
        if isinstance(start_dt, date) and not isinstance(start_dt, datetime):
            shift_date = start_dt.isoformat()
            start_iso = datetime.combine(start_dt, datetime.min.time()).isoformat()
            end_iso = datetime.combine(end_dt, datetime.min.time()).isoformat()
        else:
            # Convert tz-aware datetimes to local (Central) time, then strip tz
            # so all times in the system are naive local datetimes — matching
            # what the scraper produces from the LB grid
            if start_dt.tzinfo is not None:
                start_dt = start_dt.astimezone(LOCAL_TZ).replace(tzinfo=None)
            if end_dt.tzinfo is not None:
                end_dt = end_dt.astimezone(LOCAL_TZ).replace(tzinfo=None)
            shift_date = start_dt.date().isoformat()
            start_iso = start_dt.isoformat()
            end_iso = end_dt.isoformat()

        shifts.append(Shift(
            date=shift_date,
            start_time=start_iso,
            end_time=end_iso,
            assignment=str(summary),
        ))

    logger.info(f"Found {len(shifts)} scheduled shifts in iCal feed")
    return shifts


def get_my_working_dates(shifts: list[Shift]) -> set[str]:
    """Extract the set of dates (YYYY-MM-DD) on which the user is working."""
    return {shift.date for shift in shifts}


def _parse_dt(iso_str: str) -> datetime:
    """Parse an ISO 8601 datetime string into a naive local datetime.

    All times should already be naive local (Central) time at this point,
    but as a safety net, any remaining tz-aware times are converted to
    Central before stripping.
    """
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is not None:
        dt = dt.astimezone(LOCAL_TZ).replace(tzinfo=None)
    return dt


def conflicts_with_my_shifts(
    open_start: str,
    open_end: str,
    my_shifts: list[Shift],
) -> bool:
    """Check if an open shift conflicts with any of the user's scheduled shifts.

    A conflict exists when:
      1. The open shift overlaps in time with a scheduled shift, OR
      2. There is less than MIN_REST_HOURS between the end of one shift
         and the start of the other (in either direction).

    Args:
        open_start: ISO datetime string for the open shift start.
        open_end: ISO datetime string for the open shift end.
        my_shifts: The user's scheduled shifts.

    Returns:
        True if the open shift cannot be worked due to a conflict.
    """
    o_start = _parse_dt(open_start)
    o_end = _parse_dt(open_end)
    rest = timedelta(hours=MIN_REST_HOURS)

    for shift in my_shifts:
        s_start = _parse_dt(shift.start_time)
        s_end = _parse_dt(shift.end_time)

        # Check overlap: two intervals overlap if one starts before the other ends
        if o_start < s_end and s_start < o_end:
            return True

        # Check rest period: gap between open shift end → my shift start
        if o_end <= s_start and (s_start - o_end) < rest:
            return True

        # Check rest period: gap between my shift end → open shift start
        if s_end <= o_start and (o_start - s_end) < rest:
            return True

    return False
