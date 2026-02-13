"""Verify that the Google Calendar service account is set up correctly.

Run this after:
  1. Creating a GCP project and enabling the Google Calendar API
  2. Creating a service account and downloading the JSON key
  3. Sharing your Google Calendar with the service account email
  4. Setting GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_CALENDAR_ID in your .env

Usage:
  python scripts/verify_google_setup.py
"""

import json
import os
import sys

from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main():
    load_dotenv()

    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID")

    if not sa_json:
        print("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON not set in .env")
        sys.exit(1)
    if not calendar_id:
        print("ERROR: GOOGLE_CALENDAR_ID not set in .env")
        sys.exit(1)

    try:
        sa_info = json.loads(sa_json)
    except json.JSONDecodeError:
        print("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON")
        sys.exit(1)

    print(f"Service account email: {sa_info.get('client_email')}")
    print(f"Target calendar ID:    {calendar_id}")
    print()

    creds = Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    service = build("calendar", "v3", credentials=creds)

    try:
        calendar = service.calendars().get(calendarId=calendar_id).execute()
        print(f"Successfully accessed calendar: {calendar['summary']}")
        print("\nSetup is correct! The service account can read/write this calendar.")
    except Exception as e:
        print(f"ERROR: Could not access calendar '{calendar_id}'")
        print(f"  {e}")
        print()
        print("Make sure you shared the calendar with the service account email")
        print("and gave it 'Make changes to events' permission.")
        sys.exit(1)


if __name__ == "__main__":
    main()
