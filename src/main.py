"""Main orchestrator: fetch shifts, diff, sync to Google Calendar."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from src.ical_parser import fetch_my_shifts, conflicts_with_my_shifts
from src.scraper import scrape_open_shifts
from src.calendar_sync import sync_to_calendar
from src.state import load_state, save_state
from src.models import SyncState, SyncedShift

logger = logging.getLogger(__name__)


def main() -> None:
    load_dotenv()

    # Load required environment variables
    lb_username = os.environ["LB_USERNAME"]
    lb_password = os.environ["LB_PASSWORD"]
    ical_url = os.environ["LB_ICAL_URL"]
    google_sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    google_calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

    # Step 1: Load previous sync state
    state = load_state()
    previous_keys = {s.unique_key: s for s in state.synced_shifts}

    # Step 2: Fetch my shifts from iCal feed
    logger.info("=" * 50)
    logger.info("Fetching personal shifts from iCal feed...")
    my_shifts = fetch_my_shifts(ical_url)
    logger.info(f"Found {len(my_shifts)} scheduled shifts in lookahead window")
    for s in my_shifts:
        logger.info(f"  MY SHIFT: {s.date} | {s.start_time} - {s.end_time} | {s.assignment}")

    # Step 3: Scrape open shifts from Lightning Bolt
    logger.info("=" * 50)
    logger.info("Scraping open shifts from Lightning Bolt...")
    open_shifts = scrape_open_shifts(lb_username, lb_password)

    # Step 4: Filter — exclude open shifts that overlap with my shifts
    # or that don't have at least 8 hours of rest between shifts
    available_shifts = []
    for s in open_shifts:
        if not s.start_time or not s.end_time:
            logger.debug(f"  SKIP (no times): {s.label} {s.assignment} on {s.date}")
            continue
        conflict = conflicts_with_my_shifts(s.start_time, s.end_time, my_shifts)
        if conflict:
            logger.info(f"  FILTERED OUT: {s.label} {s.assignment} on {s.date} ({s.start_time} - {s.end_time})")
        else:
            logger.debug(f"  AVAILABLE: {s.label} {s.assignment} on {s.date} ({s.start_time} - {s.end_time})")
            available_shifts.append(s)
    logger.info(
        f"Open shifts: {len(open_shifts)} total, "
        f"{len(available_shifts)} available (no conflicts, 8hr rest)"
    )

    # Step 5: Diff with previous state
    current_keys = {s.unique_key for s in available_shifts}

    to_add = [s for s in available_shifts if s.unique_key not in previous_keys]
    to_remove = [s for s in state.synced_shifts if s.unique_key not in current_keys]
    to_keep = [s for s in state.synced_shifts if s.unique_key in current_keys]

    logger.info(f"To add: {len(to_add)} | To remove: {len(to_remove)} | Unchanged: {len(to_keep)}")

    # Step 6: Sync to Google Calendar
    if to_add or to_remove:
        logger.info("=" * 50)
        logger.info("Syncing to Google Calendar...")
        newly_synced = sync_to_calendar(
            service_account_json=google_sa_json,
            calendar_id=google_calendar_id,
            to_add=to_add,
            to_remove=to_remove,
        )

        # Step 7: Update and save state
        state = SyncState(
            last_run=datetime.now(timezone.utc).isoformat(),
            synced_shifts=to_keep + newly_synced,
        )
        save_state(state)
    else:
        logger.info("No changes needed — calendar is up to date")
        state.last_run = datetime.now(timezone.utc).isoformat()
        save_state(state)

    # Summary
    logger.info("=" * 50)
    logger.info("SYNC COMPLETE")
    logger.info(f"  Added:     {len(to_add)}")
    logger.info(f"  Removed:   {len(to_remove)}")
    logger.info(f"  Total now: {len(state.synced_shifts)}")
    logger.info("=" * 50)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
