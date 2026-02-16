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
from src.models import SyncState, SyncedShift, Shift

logger = logging.getLogger(__name__)


def _str_to_bool(value: str) -> bool:
    """Convert environment variable string to boolean."""
    return value.lower() in ("true", "1", "yes", "on")


def main() -> None:
    load_dotenv()

    # Load required environment variables
    lb_username = os.environ["LB_USERNAME"]
    lb_password = os.environ["LB_PASSWORD"]
    ical_url = os.environ["LB_ICAL_URL"]
    google_sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    google_calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

    # Load sync configuration
    sync_open = _str_to_bool(os.getenv("SYNC_OPEN_SHIFTS", "true"))
    sync_picked = _str_to_bool(os.getenv("SYNC_PICKED_SHIFTS", "true"))
    sync_scheduled = _str_to_bool(os.getenv("SYNC_SCHEDULED_SHIFTS", "true"))

    logger.info(f"Sync config: open={sync_open}, picked={sync_picked}, scheduled={sync_scheduled}")

    # Step 1: Load previous sync state
    state = load_state()
    previous_open_keys = {s.unique_key: s for s in state.synced_shifts}
    previous_picked_keys = {s.unique_key: s for s in state.picked_shifts}
    previous_scheduled_keys = {s.unique_key: s for s in state.scheduled_shifts}

    # Step 2: Fetch my shifts from iCal feed
    logger.info("=" * 50)
    logger.info("Fetching personal shifts from iCal feed...")
    my_shifts = fetch_my_shifts(ical_url)
    logger.info(f"Found {len(my_shifts)} scheduled shifts in lookahead window")
    for s in my_shifts:
        logger.info(f"  MY SHIFT: {s.date} | {s.start_time} - {s.end_time} | {s.assignment}")

    # Step 3: Scrape open shifts and picked-up shifts from Lightning Bolt
    logger.info("=" * 50)
    logger.info("Scraping shifts from Lightning Bolt...")
    open_shifts, picked_shifts = scrape_open_shifts(lb_username, lb_password)
    logger.info(f"Found {len(picked_shifts)} picked-up shifts")
    for s in picked_shifts:
        logger.info(f"  PICKED: {s.label} {s.assignment} on {s.date} ({s.start_time} - {s.end_time})")

    # Step 4: Filter — exclude open shifts that overlap with my shifts or picked shifts
    # or that don't have at least 8 hours of rest between shifts.
    # Combine iCal shifts with picked-up shifts for conflict checking.
    combined_my_shifts = my_shifts + [
        Shift(
            date=s.date,
            start_time=s.start_time,
            end_time=s.end_time,
            assignment=s.assignment,
        )
        for s in picked_shifts
    ]

    available_shifts = []
    for s in open_shifts:
        if not s.start_time or not s.end_time:
            logger.debug(f"  SKIP (no times): {s.label} {s.assignment} on {s.date}")
            continue
        conflict = conflicts_with_my_shifts(s.start_time, s.end_time, combined_my_shifts)
        if conflict:
            logger.info(f"  FILTERED OUT: {s.label} {s.assignment} on {s.date} ({s.start_time} - {s.end_time})")
        else:
            logger.debug(f"  AVAILABLE: {s.label} {s.assignment} on {s.date} ({s.start_time} - {s.end_time})")
            available_shifts.append(s)
    logger.info(
        f"Open shifts: {len(open_shifts)} total, "
        f"{len(available_shifts)} available (no conflicts with iCal + picked shifts)"
    )

    # Step 5: Diff with previous state (for open, picked, and scheduled shifts)
    # Convert scheduled shifts to OpenShift for calendar sync
    scheduled_as_open = [s.to_open_shift() for s in my_shifts]

    # Only track shifts for enabled sync types
    current_open_keys = {s.unique_key for s in available_shifts} if sync_open else set()
    current_picked_keys = {s.unique_key for s in picked_shifts} if sync_picked else set()
    current_scheduled_keys = {s.unique_key for s in scheduled_as_open} if sync_scheduled else set()

    # Open shifts diff
    if sync_open:
        open_to_add = [s for s in available_shifts if s.unique_key not in previous_open_keys]
        open_to_remove = [s for s in state.synced_shifts if s.unique_key not in current_open_keys]
        open_to_keep = [s for s in state.synced_shifts if s.unique_key in current_open_keys]
    else:
        # If sync is disabled, remove all previously synced shifts of this type
        open_to_add = []
        open_to_remove = state.synced_shifts
        open_to_keep = []

    # Picked shifts diff
    if sync_picked:
        picked_to_add = [s for s in picked_shifts if s.unique_key not in previous_picked_keys]
        picked_to_remove = [s for s in state.picked_shifts if s.unique_key not in current_picked_keys]
        picked_to_keep = [s for s in state.picked_shifts if s.unique_key in current_picked_keys]
    else:
        picked_to_add = []
        picked_to_remove = state.picked_shifts
        picked_to_keep = []

    # Scheduled shifts diff
    if sync_scheduled:
        scheduled_to_add = [s for s in scheduled_as_open if s.unique_key not in previous_scheduled_keys]
        scheduled_to_remove = [s for s in state.scheduled_shifts if s.unique_key not in current_scheduled_keys]
        scheduled_to_keep = [s for s in state.scheduled_shifts if s.unique_key in current_scheduled_keys]
    else:
        scheduled_to_add = []
        scheduled_to_remove = state.scheduled_shifts
        scheduled_to_keep = []

    logger.info(f"Open shifts: add {len(open_to_add)}, remove {len(open_to_remove)}, keep {len(open_to_keep)}")
    logger.info(f"Picked shifts: add {len(picked_to_add)}, remove {len(picked_to_remove)}, keep {len(picked_to_keep)}")
    logger.info(f"Scheduled shifts: add {len(scheduled_to_add)}, remove {len(scheduled_to_remove)}, keep {len(scheduled_to_keep)}")

    # Step 6: Sync to Google Calendar
    if open_to_add or open_to_remove or picked_to_add or picked_to_remove or scheduled_to_add or scheduled_to_remove:
        logger.info("=" * 50)
        logger.info("Syncing to Google Calendar...")
        newly_synced_open, newly_synced_picked, newly_synced_scheduled = sync_to_calendar(
            service_account_json=google_sa_json,
            calendar_id=google_calendar_id,
            open_to_add=open_to_add,
            open_to_remove=open_to_remove,
            picked_to_add=picked_to_add,
            picked_to_remove=picked_to_remove,
            scheduled_to_add=scheduled_to_add,
            scheduled_to_remove=scheduled_to_remove,
        )

        # Step 7: Update and save state
        state = SyncState(
            last_run=datetime.now(timezone.utc).isoformat(),
            synced_shifts=open_to_keep + newly_synced_open,
            picked_shifts=picked_to_keep + newly_synced_picked,
            scheduled_shifts=scheduled_to_keep + newly_synced_scheduled,
        )
        save_state(state)
    else:
        logger.info("No changes needed — calendar is up to date")
        state.last_run = datetime.now(timezone.utc).isoformat()
        save_state(state)

    # Summary
    logger.info("=" * 50)
    logger.info("SYNC COMPLETE")
    logger.info(f"  Open shifts:      added {len(open_to_add)}, removed {len(open_to_remove)}, total {len(state.synced_shifts)}")
    logger.info(f"  Picked shifts:    added {len(picked_to_add)}, removed {len(picked_to_remove)}, total {len(state.picked_shifts)}")
    logger.info(f"  Scheduled shifts: added {len(scheduled_to_add)}, removed {len(scheduled_to_remove)}, total {len(state.scheduled_shifts)}")
    logger.info("=" * 50)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
