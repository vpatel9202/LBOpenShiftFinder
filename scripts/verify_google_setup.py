"""Verify that the Google Calendar service account is set up correctly.

Performs a full end-to-end write test:
  1. Reads the target calendar (verifies read access and calendar ID)
  2. Creates a temporary 1-minute test event with the lbOpenShiftFinder
     extended property (verifies write access and extended property support)
  3. Reads the event back to confirm it was created with the correct metadata
  4. Deletes the event (verifies delete access and cleans up)

Run this after:
  1. Creating a GCP project and enabling the Google Calendar API
  2. Creating a service account and downloading the JSON key
  3. Sharing your Google Calendar with the service account email
     (with "Make changes to events" permission)
  4. Setting GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_CALENDAR_ID in your .env

Usage:
  python scripts/verify_google_setup.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]
MANAGED_EVENT_TAG = "lbOpenShiftFinder"


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "OK " if ok else "ERR"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return ok


def main() -> int:
    load_dotenv()

    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID")

    if not sa_json:
        print("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON not set in .env")
        return 1
    if not calendar_id:
        print("ERROR: GOOGLE_CALENDAR_ID not set in .env")
        return 1

    try:
        sa_info = json.loads(sa_json)
    except json.JSONDecodeError:
        print("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON")
        return 1

    print(f"Service account: {sa_info.get('client_email')}")
    print(f"Calendar ID:     {calendar_id}")
    print()
    print("Running verification...")
    print()

    creds = Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    service = build("calendar", "v3", credentials=creds)

    all_ok = True

    # ---- Step 1: Read calendar ----
    try:
        calendar = service.calendars().get(calendarId=calendar_id).execute()
        ok = _check("Read calendar", True, calendar.get("summary", ""))
    except Exception as e:
        ok = _check("Read calendar", False, str(e))
        print()
        print("  Make sure you shared the calendar with the service account email")
        print("  and gave it 'Make changes to events' permission.")
        return 1
    all_ok = all_ok and ok

    # ---- Step 2: Create test event ----
    now = datetime.now(timezone.utc)
    start = now + timedelta(hours=1)
    end = start + timedelta(minutes=1)

    test_event = {
        "summary": "[LBOpenShiftFinder verify — safe to delete]",
        "description": "Temporary test event created by verify_google_setup.py. Will be deleted immediately.",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "extendedProperties": {
            "private": {
                MANAGED_EVENT_TAG: "verify",
                "createdBy": "verify_google_setup.py",
            }
        },
    }

    event_id = None
    try:
        created = service.events().insert(
            calendarId=calendar_id,
            body=test_event,
        ).execute()
        event_id = created.get("id")
        ok = _check("Create test event", bool(event_id), f"id={event_id}")
    except Exception as e:
        ok = _check("Create test event", False, str(e))
        print()
        print("  The service account can read the calendar but not write to it.")
        print("  Verify the permission is set to 'Make changes to events'")
        print("  (not just 'See all event details').")
        return 1
    all_ok = all_ok and ok

    # ---- Step 3: Read back and verify extended property ----
    try:
        fetched = service.events().get(
            calendarId=calendar_id,
            eventId=event_id,
        ).execute()
        props = fetched.get("extendedProperties", {}).get("private", {})
        tag_ok = props.get(MANAGED_EVENT_TAG) == "verify"
        ok = _check(
            "Read back extended property",
            tag_ok,
            f"{MANAGED_EVENT_TAG}={props.get(MANAGED_EVENT_TAG)!r}",
        )
    except Exception as e:
        ok = _check("Read back extended property", False, str(e))
    all_ok = all_ok and ok

    # ---- Step 4: Delete test event ----
    try:
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id,
        ).execute()
        ok = _check("Delete test event", True)
    except Exception as e:
        ok = _check("Delete test event", False, str(e))
        print(f"  WARNING: Test event {event_id!r} was not deleted — remove it manually.")
    all_ok = all_ok and ok

    print()
    if all_ok:
        print("All checks passed — Google Calendar integration is working correctly.")
        return 0
    else:
        print("Some checks failed — see details above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
