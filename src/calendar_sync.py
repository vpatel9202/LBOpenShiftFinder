"""Google Calendar integration for adding/removing open shift events."""

from __future__ import annotations

import logging
import os
from datetime import datetime

import json

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from src.models import OpenShift, SyncedShift

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
MANAGED_EVENT_TAG = "lbOpenShiftFinder"

# Google Calendar event color IDs (configurable via environment variables):
# 1=Lavender, 2=Sage, 3=Grape, 4=Flamingo, 5=Banana,
# 6=Tangerine, 7=Peacock, 8=Graphite, 9=Blueberry, 10=Basil, 11=Tomato
OPEN_SHIFT_COLOR = os.getenv("OPEN_SHIFT_COLOR", "2")       # Default: Sage (green)
PICKED_SHIFT_COLOR = os.getenv("PICKED_SHIFT_COLOR", "9")   # Default: Blueberry (blue)
SCHEDULED_SHIFT_COLOR = os.getenv("SCHEDULED_SHIFT_COLOR", "3")  # Default: Grape (purple)


def _get_calendar_service(service_account_info: dict):
    """Build an authenticated Google Calendar API service using a service account."""
    creds = Credentials.from_service_account_info(
        service_account_info,
        scopes=SCOPES,
    )
    return build("calendar", "v3", credentials=creds)


def add_open_shift(
    service,
    calendar_id: str,
    shift: OpenShift,
    color_id: str = OPEN_SHIFT_COLOR,
    is_picked: bool = False,
    is_scheduled: bool = False,
) -> str:
    """Add an open shift as a Google Calendar event.

    Args:
        service: Authenticated Google Calendar API service.
        calendar_id: Google Calendar ID to add the event to.
        shift: The shift to add.
        color_id: Google Calendar color ID (defaults to sage green for open shifts).
        is_picked: Whether this is a picked-up shift (affects summary prefix).
        is_scheduled: Whether this is a regular scheduled shift from iCal.

    Returns:
        The Google Calendar event ID.
    """
    if is_scheduled:
        prefix = shift.assignment
        desc_status = "your regular scheduled shift"
        shift_type = "scheduled"
    elif is_picked:
        prefix = "PICKED"
        desc_status = "picked up by you"
        shift_type = "picked"
    else:
        prefix = "OPEN"
        desc_status = "available on Lightning Bolt"
        shift_type = "open"

    event_body = {
        "summary": f"{prefix}" if is_scheduled else f"{prefix}: {shift.assignment} ({shift.label})",
        "description": (
            f"Shift {desc_status}\n"
            f"Assignment: {shift.assignment}\n"
            + (f"Label: {shift.label}\n" if not is_scheduled else "") +
            f"\nAuto-managed by LBOpenShiftFinder"
        ),
        "start": {
            "dateTime": shift.start_time,
            "timeZone": "America/Chicago",
        },
        "end": {
            "dateTime": shift.end_time,
            "timeZone": "America/Chicago",
        },
        "colorId": color_id,
        "extendedProperties": {
            "private": {
                MANAGED_EVENT_TAG: "true",
                "openShiftKey": shift.unique_key,
                "shiftType": shift_type,
            },
        },
    }

    event = service.events().insert(
        calendarId=calendar_id,
        body=event_body,
    ).execute()

    event_id = event["id"]
    logger.info(f"Added calendar event: {shift.label} {shift.assignment} on {shift.date} (id={event_id})")
    return event_id


def remove_open_shift(service, calendar_id: str, event_id: str) -> None:
    """Remove a previously synced open shift from Google Calendar."""
    try:
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id,
        ).execute()
        logger.info(f"Removed calendar event: {event_id}")
    except Exception as e:
        # Event may have been manually deleted — that's fine
        logger.warning(f"Could not delete event {event_id}: {e}")


def list_managed_events(service, calendar_id: str) -> list[dict]:
    """List all events created by this tool on the calendar.

    Uses the extended property tag to identify our events.
    """
    events = []
    page_token = None

    while True:
        result = service.events().list(
            calendarId=calendar_id,
            privateExtendedProperty=f"{MANAGED_EVENT_TAG}=true",
            maxResults=250,
            pageToken=page_token,
        ).execute()

        events.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    logger.info(f"Found {len(events)} managed events on Google Calendar")
    return events


def sync_to_calendar(
    service_account_json: str,
    calendar_id: str,
    open_to_add: list[OpenShift],
    open_to_remove: list[SyncedShift],
    picked_to_add: list[OpenShift],
    picked_to_remove: list[SyncedShift],
    scheduled_to_add: list[OpenShift],
    scheduled_to_remove: list[SyncedShift],
) -> tuple[list[SyncedShift], list[SyncedShift], list[SyncedShift]]:
    """Perform the actual sync: add new shifts, remove stale ones.

    Args:
        service_account_json: JSON string of the service account key.
        calendar_id: Google Calendar ID to sync to.
        open_to_add: Open shifts to add to the calendar.
        open_to_remove: Previously synced open shifts to remove.
        picked_to_add: Picked-up shifts to add to the calendar.
        picked_to_remove: Previously synced picked-up shifts to remove.
        scheduled_to_add: Scheduled shifts to add to the calendar.
        scheduled_to_remove: Previously synced scheduled shifts to remove.

    Returns:
        Tuple of (newly_synced_open, newly_synced_picked, newly_synced_scheduled) with Google event IDs.
    """
    service_account_info = json.loads(service_account_json)
    service = _get_calendar_service(service_account_info)

    # Remove stale shifts
    for shift in open_to_remove:
        remove_open_shift(service, calendar_id, shift.google_event_id)
    for shift in picked_to_remove:
        remove_open_shift(service, calendar_id, shift.google_event_id)
    for shift in scheduled_to_remove:
        remove_open_shift(service, calendar_id, shift.google_event_id)

    # Add new open shifts (sage green)
    newly_synced_open: list[SyncedShift] = []
    for shift in open_to_add:
        event_id = add_open_shift(service, calendar_id, shift, color_id=OPEN_SHIFT_COLOR, is_picked=False)
        newly_synced_open.append(SyncedShift.from_open_shift(shift, event_id))

    # Add new picked shifts (blueberry blue)
    newly_synced_picked: list[SyncedShift] = []
    for shift in picked_to_add:
        event_id = add_open_shift(service, calendar_id, shift, color_id=PICKED_SHIFT_COLOR, is_picked=True)
        newly_synced_picked.append(SyncedShift.from_open_shift(shift, event_id))

    # Add new scheduled shifts (grape purple)
    newly_synced_scheduled: list[SyncedShift] = []
    for shift in scheduled_to_add:
        event_id = add_open_shift(service, calendar_id, shift, color_id=SCHEDULED_SHIFT_COLOR, is_picked=False, is_scheduled=True)
        newly_synced_scheduled.append(SyncedShift.from_open_shift(shift, event_id))

    return newly_synced_open, newly_synced_picked, newly_synced_scheduled
