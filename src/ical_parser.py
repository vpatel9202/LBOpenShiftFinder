"""Parse the user's Lightning Bolt iCal subscription feed to extract scheduled shifts."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import requests
from icalendar import Calendar
import recurring_ical_events

from src.models import Shift

logger = logging.getLogger(__name__)


def fetch_my_shifts(ical_url: str, lookahead_days: int = 60) -> list[Shift]:
    """Fetch and parse the user's iCal feed, returning their scheduled shifts.

    Args:
        ical_url: The iCal subscription URL from Lightning Bolt.
        lookahead_days: How many days ahead to look for shifts.

    Returns:
        List of Shift objects for the user's scheduled shifts.
    """
    logger.info("Fetching iCal feed...")
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
