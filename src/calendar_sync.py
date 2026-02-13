"""Google Calendar integration for adding/removing open shift events."""

from __future__ import annotations

import logging
from datetime import datetime

import json

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from src.models import OpenShift, SyncedShift

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
MANAGED_EVENT_TAG = "lbOpenShiftFinder"

# Google Calendar event color IDs:
# 1=Lavender, 2=Sage, 3=Grape, 4=Flamingo, 5=Banana,
# 6=Tangerine, 7=Peacock, 8=Graphite, 9=Blueberry, 10=Basil, 11=Tomato
EVENT_COLOR_ID = "2"  # Sage (green-ish) to stand out


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
) -> str:
    """Add an open shift as a Google Calendar event.

    Returns:
        The Google Calendar event ID.
    """
    event_body = {
        "summary": f"OPEN: {shift.assignment} ({shift.label})",
        "description": (
            f"Open shift available on Lightning Bolt\n"
            f"Assignment: {shift.assignment}\n"
            f"Label: {shift.label}\n\n"
            f"Auto-managed by LBOpenShiftFinder"
        ),
        "start": {
            "dateTime": shift.start_time,
            "timeZone": "America/Chicago",
        },
        "end": {
            "dateTime": shift.end_time,
            "timeZone": "America/Chicago",
        },
        "colorId": EVENT_COLOR_ID,
        "extendedProperties": {
            "private": {
                MANAGED_EVENT_TAG: "true",
                "openShiftKey": shift.unique_key,
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
    to_add: list[OpenShift],
    to_remove: list[SyncedShift],
) -> list[SyncedShift]:
    """Perform the actual sync: add new shifts, remove stale ones.

    Args:
        service_account_json: JSON string of the service account key.
        calendar_id: Google Calendar ID to sync to.
        to_add: Open shifts to add to the calendar.
        to_remove: Previously synced shifts to remove.

    Returns:
        List of newly synced shifts (with Google event IDs).
    """
    service_account_info = json.loads(service_account_json)
    service = _get_calendar_service(service_account_info)

    # Remove stale shifts
    for shift in to_remove:
        remove_open_shift(service, calendar_id, shift.google_event_id)

    # Add new shifts
    newly_synced: list[SyncedShift] = []
    for shift in to_add:
        event_id = add_open_shift(service, calendar_id, shift)
        newly_synced.append(SyncedShift.from_open_shift(shift, event_id))

    return newly_synced
