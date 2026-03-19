"""Scrape the 'Today's Schedule' Gantt view from Lightning Bolt for triage reporting."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

from playwright.sync_api import sync_playwright, Page, Browser, TimeoutError as PwTimeout

from src.scraper import _login, _take_screenshot, _dump_html

logger = logging.getLogger(__name__)

LOCAL_TIMEZONE = os.getenv("LOCAL_TIMEZONE", "America/Chicago")

# =============================================================================
# SELECTORS
# =============================================================================

TRIAGE_SELECTORS = {
    # Post-login selection screen
    "viewer_tile": "#SelectionScreen .ApplicationElement",

    # Top bar context ribbon
    "context_ribbon": "#ContextRibbon",
    "me_button": "#ContextRibbon .ContextRibbonItem.limit-width-large.view",

    # "Today's Schedule" action button in the sidebar dialog
    "today_schedule_btn": ".Dialog.isTop.ViewOptions a.current-action-button",

    # Schedule grid containers — try both; WeekContainer may be child of StandardContainer
    "grid_container_primary": ".StandardContainer .WeekContainer",
    "grid_container_fallback": ".StandardContainer",

    # Data rows (same as weekly grid)
    "data_row": ".DataRow",
    "left_col": ".leftCol[data-cy='leftCol']",
    "data_cell": "[data-cy='dataCell']",

    # Schedule-type dropdown in context ribbon (opens sidebar to switch MD/APP)
    "schedule_type_dropdown": "#ContextRibbon div.ContextRibbonItem.today-template div.ribbon-text.no-mobile",

    # Sidebar items in the schedule-type picker
    "schedule_type_items": ".Dialog.isTop a",
    "schedule_type_fallback": "div.Item:nth-child(2) > div:nth-child(1) > div:nth-child(1)",

    # Day navigation arrows
    "next_day_primary": "#ContextRibbon i.fa-chevron-right",
    "next_day_fallback": "#ContextRibbon i.fa:nth-child(2)",

    # Tooltip selectors (try in order)
    "tooltip_selectors": [
        ".tooltip",
        ".SlotToolTip",
        "[class*='tooltip']",
        "[class*='Tooltip']",
    ],

    # Note icons within a slot
    "note_icon_selectors": [
        ".fa-sticky-note",
        ".fa-note",
        ".note-icon",
        "[class*='note']",
        "i.fa",
    ],

    # Slot elements within a data cell (provider bars)
    "slot_selectors": [
        ".Slot",
        "[data-cy='dataCell'] > *",
    ],
}


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class TriageShift:
    """A single shift entry on the Today's Schedule Gantt view."""
    label: str            # e.g. "T1", "APP A-1A", "Teaching - Long Call"
    providers: list[str]  # One or more provider names on this shift
    start_time: str       # Raw time string as returned by LB, e.g. "7:00am"
    end_time: str         # Raw time string as returned by LB, e.g. "7:00am"
    source: str           # "md" or "app"
    is_next_day_t1: bool = False  # True only for the following-day T1 row


@dataclass
class TriageSchedule:
    """The full triage schedule for a 7am–7am window."""
    date: str                        # YYYY-MM-DD — the "start" date of the window
    shifts: list[TriageShift] = field(default_factory=list)  # Ordered by start time; next-day T1 last


# =============================================================================
# NAVIGATION HELPERS
# =============================================================================

def _navigate_to_today_schedule(page: Page) -> None:
    """Navigate from the post-login selection screen to Today's Schedule.

    Flow:
      1. Click "Viewer" tile on the selection screen
      2. Click "Me" button in the context ribbon
      3. Wait for sidebar animation
      4. Click the "Today's Schedule" action button in the sidebar
      5. Wait for the daily grid to render
    """
    # Step 1: Click the "Viewer" application tile
    logger.info("Clicking Viewer tile...")
    page.wait_for_selector(TRIAGE_SELECTORS["viewer_tile"], timeout=15000)
    page.click(TRIAGE_SELECTORS["viewer_tile"])
    page.wait_for_selector(TRIAGE_SELECTORS["context_ribbon"], timeout=30000)

    # Step 2: Click "Me" button to open the view picker sidebar
    logger.info("Clicking 'Me' button...")
    page.wait_for_selector(TRIAGE_SELECTORS["me_button"], timeout=15000)
    page.click(TRIAGE_SELECTORS["me_button"])

    # Step 3: Wait for sidebar animation
    page.wait_for_timeout(1500)

    # Step 4: Click "Today's Schedule" action button in the sidebar
    logger.info("Clicking 'Today's Schedule' button...")
    page.wait_for_selector(TRIAGE_SELECTORS["today_schedule_btn"], timeout=10000)
    page.click(TRIAGE_SELECTORS["today_schedule_btn"])

    # Step 5: Wait for the daily grid to render
    # Try the primary container; log a warning if it times out and fall back
    logger.info("Waiting for daily grid to render...")
    try:
        page.wait_for_selector(
            TRIAGE_SELECTORS["grid_container_primary"],
            timeout=20000,
            state="attached",
        )
        logger.info("Daily grid container (primary) found")
    except PwTimeout:
        logger.warning(
            "Primary grid container (.WeekContainer) not found; "
            "falling back to .StandardContainer"
        )
        try:
            page.wait_for_selector(
                TRIAGE_SELECTORS["grid_container_fallback"],
                timeout=10000,
                state="attached",
            )
            logger.info("Daily grid container (fallback) found")
        except PwTimeout:
            logger.warning("Neither grid container selector matched — DOM may differ from expected")

    logger.info("Navigation to Today's Schedule complete")


def _switch_to_app_schedule(page: Page, app_schedule_name: str) -> None:
    """Switch the Today's Schedule view from MD to APP schedule.

    Flow:
      1. Click the schedule-type dropdown in the context ribbon
      2. Wait for sidebar to open
      3. Click the item matching app_schedule_name (substring, case-insensitive)
      4. Fall back to the 2nd item if no match found
      5. Wait for grid to re-render

    Args:
        page: Playwright page object.
        app_schedule_name: Substring to match against schedule type link text.
    """
    logger.info(f"Switching to APP schedule: '{app_schedule_name}'...")
    page.wait_for_selector(TRIAGE_SELECTORS["schedule_type_dropdown"], timeout=10000)
    page.click(TRIAGE_SELECTORS["schedule_type_dropdown"])
    page.wait_for_timeout(1000)  # Wait for sidebar to open

    # Find all items in the sidebar and match by text
    items = page.query_selector_all(TRIAGE_SELECTORS["schedule_type_items"])
    clicked = False
    for item in items:
        text = item.inner_text().strip()
        if app_schedule_name.lower() in text.lower():
            logger.info(f"Found matching schedule type: '{text}'")
            item.click()
            clicked = True
            break

    if not clicked:
        # Fallback: click the 2nd item
        available = [i.inner_text().strip() for i in items]
        logger.warning(
            f"Could not find schedule matching '{app_schedule_name}'. "
            f"Available: {available}. Falling back to 2nd item."
        )
        fallback = page.query_selector(TRIAGE_SELECTORS["schedule_type_fallback"])
        if fallback:
            fallback.click()
        elif len(items) > 1:
            items[1].click()
        elif items:
            logger.warning("Only one schedule type item found; clicking it as last resort")
            items[0].click()
        else:
            logger.warning("No schedule type items found; cannot switch schedule")
            return

    # Wait for grid to re-render
    page.wait_for_timeout(2000)
    logger.info("Switch to APP schedule complete")


def _switch_to_md_schedule(page: Page, md_schedule_name: str) -> None:
    """Switch the Today's Schedule view back to the MD schedule.

    Mirrors _switch_to_app_schedule but matches against md_schedule_name.

    Args:
        page: Playwright page object.
        md_schedule_name: Substring to match against schedule type link text.
    """
    logger.info(f"Switching back to MD schedule: '{md_schedule_name}'...")
    page.wait_for_selector(TRIAGE_SELECTORS["schedule_type_dropdown"], timeout=10000)
    page.click(TRIAGE_SELECTORS["schedule_type_dropdown"])
    page.wait_for_timeout(1000)

    items = page.query_selector_all(TRIAGE_SELECTORS["schedule_type_items"])
    clicked = False
    for item in items:
        text = item.inner_text().strip()
        if md_schedule_name.lower() in text.lower():
            logger.info(f"Found matching MD schedule type: '{text}'")
            item.click()
            clicked = True
            break

    if not clicked:
        available = [i.inner_text().strip() for i in items]
        logger.warning(
            f"Could not find MD schedule matching '{md_schedule_name}'. "
            f"Available: {available}. Falling back to first item."
        )
        if items:
            items[0].click()
        else:
            logger.warning("No schedule type items found; cannot switch to MD schedule")
            return

    page.wait_for_timeout(2000)
    logger.info("Switch to MD schedule complete")


def _navigate_next_day(page: Page) -> None:
    """Click the next-day arrow in the context ribbon.

    Tries the primary chevron-right selector first; falls back to the
    generic 2nd fa icon if the specific class is not present.
    """
    logger.info("Navigating to next day...")
    clicked = False

    try:
        arrow = page.query_selector(TRIAGE_SELECTORS["next_day_primary"])
        if arrow:
            arrow.click()
            clicked = True
            logger.info("Clicked next-day arrow (primary selector)")
    except Exception as e:
        logger.debug(f"Primary next-day selector failed: {e}")

    if not clicked:
        try:
            arrow = page.query_selector(TRIAGE_SELECTORS["next_day_fallback"])
            if arrow:
                arrow.click()
                clicked = True
                logger.info("Clicked next-day arrow (fallback selector)")
        except Exception as e:
            logger.warning(f"Fallback next-day selector also failed: {e}")

    if not clicked:
        logger.warning("Could not find next-day arrow — page may not have advanced")

    page.wait_for_timeout(2000)  # Wait for day transition to complete


# =============================================================================
# EXTRACTION HELPERS
# =============================================================================

def _get_tooltip_text(page: Page, slot) -> str | None:
    """Hover over a slot element and read the tooltip text.

    Tries each tooltip selector in sequence; returns the inner text of the
    first one found, or None if no tooltip appears.
    """
    try:
        slot.hover()
        page.wait_for_timeout(600)
    except Exception as e:
        logger.debug(f"Hover failed: {e}")
        return None

    for sel in TRIAGE_SELECTORS["tooltip_selectors"]:
        try:
            tooltip_el = page.query_selector(sel)
            if tooltip_el:
                text = tooltip_el.inner_text().strip()
                if text:
                    return text
        except Exception:
            continue

    return None


def _parse_tooltip(tooltip_text: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Parse a hover tooltip into its components.

    Expected format (confirmed from screenshots):
        Mar 19, 2026
        7:00am - 5:00pm
        Christina Sandwell
        long call

    Returns:
        (start_time, end_time, provider_name, note_text)
        Any field may be None if not found/parsed.
    """
    lines = [line.strip() for line in tooltip_text.strip().splitlines() if line.strip()]

    if len(lines) < 2:
        return None, None, None, None

    # Line 0: date (ignore)
    # Line 1: time range "7:00am - 5:00pm" or "7:00am – 5:00pm"
    time_line = lines[1]
    start_time: str | None = None
    end_time: str | None = None

    import re
    time_match = re.search(
        r"(\d{1,2}:\d{2}\s*(?:am|pm|AM|PM)?)\s*[-–]\s*(\d{1,2}:\d{2}\s*(?:am|pm|AM|PM)?)",
        time_line,
        re.IGNORECASE,
    )
    if time_match:
        start_time = time_match.group(1).strip()
        end_time = time_match.group(2).strip()

    # Line 2: provider name (if present)
    provider_name: str | None = lines[2] if len(lines) > 2 else None

    # Line 3+: note text (join remaining lines)
    note_text: str | None = " ".join(lines[3:]) if len(lines) > 3 else None

    return start_time, end_time, provider_name, note_text


def _classify_note(note_text: str) -> str | None:
    """Classify a teaching note into a TriageShift label.

    Args:
        note_text: The note text from the tooltip (line 3+).

    Returns:
        A teaching shift label string, or None if not a teaching shift.
    """
    t = note_text.lower()
    if "long" in t:
        return "Teaching - Long Call"
    if "short" in t:
        return "Teaching - Short Call"
    if "call" in t:  # catches "on call", "call", "weekend call"
        return "Teaching - Weekend Call"
    return None


def _has_note_icon(slot) -> bool:
    """Check whether a slot element contains a note icon."""
    for sel in TRIAGE_SELECTORS["note_icon_selectors"]:
        try:
            icon = slot.query_selector(sel)
            if icon:
                return True
        except Exception:
            continue
    return False


def _get_slot_elements(row):
    """Return all slot/provider bar elements within a DataRow.

    Tries .Slot children of data cells first; falls back to direct children
    of data cells that contain any text content.
    """
    slots = []

    # Try .Slot elements inside data cells
    try:
        found = row.query_selector_all("[data-cy='dataCell'] .Slot")
        if found:
            return found
    except Exception:
        pass

    # Fallback: direct children of data cells that have visible text
    try:
        cells = row.query_selector_all("[data-cy='dataCell']")
        for cell in cells:
            children = cell.query_selector_all(":scope > *")
            for child in children:
                text = child.inner_text().strip()
                if text:
                    slots.append(child)
    except Exception:
        pass

    return slots


# =============================================================================
# MAIN EXTRACTION
# =============================================================================

def _extract_schedule(
    page: Page,
    target_labels: list[str],
    scan_all_for_notes: bool,
    source: str,
) -> list[TriageShift]:
    """Extract triage shift data from the Today's Schedule Gantt view.

    Args:
        page: Playwright page object on the Today's Schedule view.
        target_labels: Row labels to extract (e.g. ["T1", "T2", "A2"]).
        scan_all_for_notes: If True, also scan non-target rows for teaching
            note icons (used for MD schedule; False for APP schedule).
        source: "md" or "app" — sets TriageShift.source.

    Returns:
        List of TriageShift objects extracted from the page.
    """
    shifts: list[TriageShift] = []
    target_lower = {lbl.lower() for lbl in target_labels}

    # Find the grid container — try primary then fallback
    container = page.query_selector(TRIAGE_SELECTORS["grid_container_primary"])
    if not container:
        logger.warning(
            "Primary grid container (.WeekContainer) not found; "
            "trying fallback (.StandardContainer)"
        )
        container = page.query_selector(TRIAGE_SELECTORS["grid_container_fallback"])

    if not container:
        logger.warning("No grid container found — cannot extract schedule")
        _dump_html(page, f"triage_no_container_{source}")
        return shifts

    # Find all DataRow elements within the container
    rows = container.query_selector_all(TRIAGE_SELECTORS["data_row"])
    if not rows:
        logger.warning(f"Found 0 DataRow elements in grid container ({source})")
        _dump_html(page, f"triage_no_rows_{source}")
        return shifts

    logger.info(f"Found {len(rows)} DataRow elements ({source})")

    for row in rows:
        try:
            _process_row(page, row, target_lower, scan_all_for_notes, source, shifts)
        except Exception as e:
            logger.warning(f"Error processing row: {e}")
            continue

    logger.info(f"Extracted {len(shifts)} triage shifts ({source})")
    return shifts


def _process_row(
    page: Page,
    row,
    target_lower: set[str],
    scan_all_for_notes: bool,
    source: str,
    shifts: list[TriageShift],
) -> None:
    """Process a single DataRow and append matching TriageShift entries to shifts.

    Modifies shifts in place.
    """
    # Get label from left column
    left_col = row.query_selector(TRIAGE_SELECTORS["left_col"])
    if not left_col:
        return
    label = left_col.inner_text().strip()
    if not label:
        return

    is_target = label.lower() in target_lower

    if not is_target and not scan_all_for_notes:
        return  # Nothing to do for this row

    # Get slot elements (provider bars)
    slot_elements = _get_slot_elements(row)

    if not slot_elements:
        if is_target:
            logger.debug(f"No slot elements found for target row: '{label}'")
        return

    if is_target:
        # Group slots by time to detect multi-provider same-time shifts
        time_groups: dict[tuple[str | None, str | None], list[str]] = {}

        for slot in slot_elements:
            tooltip_text = _get_tooltip_text(page, slot)

            if tooltip_text:
                start_time, end_time, provider_name, note_text = _parse_tooltip(tooltip_text)
            else:
                # Fallback: read slot's direct inner text for provider name
                raw_text = slot.inner_text().strip()
                provider_name = raw_text.split("\n")[0].strip() if raw_text else None
                start_time = None
                end_time = None
                note_text = None

            if not provider_name:
                continue

            time_key = (start_time, end_time)

            if time_key in time_groups:
                # Same time window — add to existing group (multi-provider)
                time_groups[time_key].append(provider_name)
            else:
                time_groups[time_key] = [provider_name]

            # Check for teaching note on this slot (only when scan_all_for_notes)
            if scan_all_for_notes and note_text:
                teaching_label = _classify_note(note_text)
                if teaching_label:
                    shifts.append(TriageShift(
                        label=teaching_label,
                        providers=[provider_name],
                        start_time=start_time or "",
                        end_time=end_time or "",
                        source=source,
                    ))
                    logger.debug(f"Teaching shift detected: {teaching_label} — {provider_name}")
            elif scan_all_for_notes and _has_note_icon(slot):
                # Note icon present but tooltip didn't give us line 3+ text — re-hover
                tooltip_text2 = _get_tooltip_text(page, slot)
                if tooltip_text2:
                    _, _, pname2, note_text2 = _parse_tooltip(tooltip_text2)
                    if note_text2:
                        teaching_label = _classify_note(note_text2)
                        if teaching_label and pname2:
                            shifts.append(TriageShift(
                                label=teaching_label,
                                providers=[pname2],
                                start_time=start_time or "",
                                end_time=end_time or "",
                                source=source,
                            ))

        # Convert time_groups into TriageShift entries
        for (start_time, end_time), providers in time_groups.items():
            shifts.append(TriageShift(
                label=label,
                providers=providers,
                start_time=start_time or "",
                end_time=end_time or "",
                source=source,
            ))

    elif scan_all_for_notes:
        # Non-target row — only scan for note icons / teaching notes
        for slot in slot_elements:
            has_icon = _has_note_icon(slot)
            if not has_icon:
                continue

            tooltip_text = _get_tooltip_text(page, slot)
            if not tooltip_text:
                continue

            start_time, end_time, provider_name, note_text = _parse_tooltip(tooltip_text)
            if not note_text or not provider_name:
                continue

            teaching_label = _classify_note(note_text)
            if teaching_label:
                shifts.append(TriageShift(
                    label=teaching_label,
                    providers=[provider_name],
                    start_time=start_time or "",
                    end_time=end_time or "",
                    source=source,
                ))
                logger.debug(
                    f"Teaching shift (non-target row '{label}'): "
                    f"{teaching_label} — {provider_name}"
                )


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def scrape_triage_schedule(
    username: str,
    password: str,
    md_shifts: list[str],
    app_shifts: list[str],
    app_schedule_name: str,
    md_schedule_name: str = "",
    headless: bool = True,
) -> TriageSchedule:
    """Scrape the Today's Schedule triage view from Lightning Bolt.

    Args:
        username: LB login email.
        password: LB login password.
        md_shifts: Row labels to extract from the MD schedule
            (e.g. ["T1", "T2", "T3", "A2", "A3", "A4", "A5", "A5 RRT"]).
        app_shifts: Row labels to extract from the APP schedule
            (e.g. ["APP PA", "APP A-1A", "APP A-1B", "APP A-2", "APP A-3"]).
        app_schedule_name: Substring matching the APP schedule name in the
            schedule-type dropdown (e.g. "BSW Hospital Medicine Dallas APP").
        md_schedule_name: Substring matching the MD schedule name in the
            schedule-type dropdown. Used when switching back after APP extraction.
            Defaults to empty string (will match first item).
        headless: Run browser in headless mode (False for recon/debug).

    Returns:
        TriageSchedule with all extracted shifts. The date field is set to
        today's date (the "start" of the 7am–7am window).
    """
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo(LOCAL_TIMEZONE)).strftime("%Y-%m-%d")

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=headless)
        page: Page = browser.new_page()

        try:
            # Step 1: Login
            _login(page, username, password)
            _take_screenshot(page, "triage_01_after_login")

            # Step 2: Navigate to Today's Schedule (MD)
            _navigate_to_today_schedule(page)
            _take_screenshot(page, "triage_02_today_schedule")

            # Step 3: Extract MD schedule
            logger.info("Extracting MD schedule shifts...")
            md_extracted = _extract_schedule(
                page,
                target_labels=md_shifts,
                scan_all_for_notes=True,
                source="md",
            )
            _take_screenshot(page, "triage_03_md_extracted")

            # Step 4: Switch to APP schedule
            _switch_to_app_schedule(page, app_schedule_name)
            _take_screenshot(page, "triage_04_app_schedule")

            # Step 5: Extract APP schedule
            logger.info("Extracting APP schedule shifts...")
            app_extracted = _extract_schedule(
                page,
                target_labels=app_shifts,
                scan_all_for_notes=False,
                source="app",
            )
            _take_screenshot(page, "triage_05_app_extracted")

            # Step 6: Navigate forward one day (for next-day T1)
            _navigate_next_day(page)
            _take_screenshot(page, "triage_06_next_day")

            # Step 7: Switch back to MD schedule
            if md_schedule_name:
                _switch_to_md_schedule(page, md_schedule_name)
            else:
                # No MD schedule name provided — open dropdown and click first item
                logger.info("No MD schedule name provided; clicking first schedule-type item...")
                page.wait_for_selector(TRIAGE_SELECTORS["schedule_type_dropdown"], timeout=10000)
                page.click(TRIAGE_SELECTORS["schedule_type_dropdown"])
                page.wait_for_timeout(1000)
                items = page.query_selector_all(TRIAGE_SELECTORS["schedule_type_items"])
                if items:
                    items[0].click()
                page.wait_for_timeout(2000)

            _take_screenshot(page, "triage_07_next_day_md")

            # Step 8: Extract T1 only from next day; mark is_next_day_t1
            logger.info("Extracting next-day T1 shift...")
            next_day_raw = _extract_schedule(
                page,
                target_labels=["T1"],
                scan_all_for_notes=False,
                source="md",
            )
            next_day_t1: list[TriageShift] = []
            for shift in next_day_raw:
                next_day_t1.append(TriageShift(
                    label=shift.label,
                    providers=shift.providers,
                    start_time=shift.start_time,
                    end_time=shift.end_time,
                    source=shift.source,
                    is_next_day_t1=True,
                ))

            _take_screenshot(page, "triage_08_next_day_t1")

            # Step 9: Assemble and return TriageSchedule
            # Ordering: MD shifts + APP shifts (ordered by start time as extracted),
            # then next-day T1 last (as specified)
            all_shifts = md_extracted + app_extracted + next_day_t1

            logger.info(
                f"Triage extraction complete: {len(md_extracted)} MD shifts, "
                f"{len(app_extracted)} APP shifts, {len(next_day_t1)} next-day T1 shift(s)"
            )

            return TriageSchedule(date=today, shifts=all_shifts)

        except PwTimeout as e:
            logger.error(f"Timeout during triage scraping: {e}")
            _take_screenshot(page, "triage_error_timeout")
            _dump_html(page, "triage_error_timeout", None)
            raise
        except Exception as e:
            logger.error(f"Error during triage scraping: {e}")
            _take_screenshot(page, "triage_error_general")
            _dump_html(page, "triage_error_general", None)
            raise
        finally:
            browser.close()


# =============================================================================
# RECON MODE
# =============================================================================

def run_triage_recon(username: str, password: str) -> None:
    """Run in recon mode: headed browser with pauses for DOM inspection.

    Navigates step by step through the Today's Schedule flow, dumping HTML
    and pausing for user input at each major step. Prints all found row
    labels to stdout.
    """
    print("\n" + "=" * 70)
    print("RECON MODE — Lightning Bolt Triage Scraper (Today's Schedule)")
    print("=" * 70)
    print("The browser will navigate step-by-step through the login and")
    print("Today's Schedule flow, pausing for inspection at each stage.")
    print("=" * 70 + "\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        # Step 1: Login
        print("[STEP 1] Logging in...")
        _login(page, username, password)
        _take_screenshot(page, "triage_recon_01_after_login")
        _dump_html(page, "triage_recon_01_after_login")
        print("  Login successful.")
        input("  Press Enter to continue to Today's Schedule navigation...")

        # Step 2: Navigate to Today's Schedule
        print("[STEP 2] Navigating to Today's Schedule...")
        _navigate_to_today_schedule(page)
        _take_screenshot(page, "triage_recon_02_today_schedule")
        _dump_html(page, "triage_recon_02_today_schedule")
        print("  Today's Schedule view loaded.")
        input("  Press Enter to inspect the grid and print row labels...")

        # Step 3: Dump row labels from current view
        print("[STEP 3] Scanning DataRow labels in current view...")
        _print_row_labels(page)
        _dump_html(page, "triage_recon_03_grid")
        input("  Press Enter to attempt slot extraction on all rows...")

        # Step 4: Attempt full extraction (no filter) and print results
        print("[STEP 4] Extracting all visible rows (no label filter)...")
        _recon_extract_all(page)
        _take_screenshot(page, "triage_recon_04_extracted")
        _dump_html(page, "triage_recon_04_extraction")
        input("  Press Enter to close browser...")

        browser.close()

    print("\n" + "=" * 70)
    print("TRIAGE RECON COMPLETE")
    print("=" * 70 + "\n")


def _print_row_labels(page: Page) -> None:
    """Print all DataRow left-column labels found on the current page."""
    container = page.query_selector(TRIAGE_SELECTORS["grid_container_primary"])
    if not container:
        container = page.query_selector(TRIAGE_SELECTORS["grid_container_fallback"])

    if not container:
        print("  WARNING: No grid container found")
        return

    rows = container.query_selector_all(TRIAGE_SELECTORS["data_row"])
    print(f"  Found {len(rows)} DataRow elements")
    for row in rows:
        left_col = row.query_selector(TRIAGE_SELECTORS["left_col"])
        label = left_col.inner_text().strip() if left_col else "(no leftCol)"
        print(f"    Label: {label!r}")


def _recon_extract_all(page: Page) -> None:
    """Extract and print all rows (ignoring target_labels filter) for recon."""
    container = page.query_selector(TRIAGE_SELECTORS["grid_container_primary"])
    if not container:
        container = page.query_selector(TRIAGE_SELECTORS["grid_container_fallback"])
    if not container:
        print("  WARNING: No grid container found for extraction")
        return

    rows = container.query_selector_all(TRIAGE_SELECTORS["data_row"])
    print(f"  Processing {len(rows)} rows...")

    for row in rows:
        left_col = row.query_selector(TRIAGE_SELECTORS["left_col"])
        label = left_col.inner_text().strip() if left_col else "(no leftCol)"
        slot_elements = _get_slot_elements(row)
        print(f"  Row '{label}': {len(slot_elements)} slot(s)")
        for i, slot in enumerate(slot_elements):
            tooltip_text = _get_tooltip_text(page, slot)
            if tooltip_text:
                start_time, end_time, provider_name, note_text = _parse_tooltip(tooltip_text)
                print(f"    Slot {i}: {provider_name} | {start_time}–{end_time} | note={note_text!r}")
            else:
                raw = slot.inner_text().strip().replace("\n", " ")
                print(f"    Slot {i}: (no tooltip) raw={raw!r}")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    import os
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Lightning Bolt triage schedule scraper")
    parser.add_argument("--recon", action="store_true", help="Run in recon mode (headed browser with pauses)")
    parser.add_argument(
        "--md-shifts",
        default="T1,T2,T3,A2,A3,A4,A5,A5 RRT",
        help="Comma-separated MD shift labels to extract",
    )
    parser.add_argument(
        "--app-shifts",
        default="APP PA,APP A-1A,APP A-1B,APP A-2,APP A-3",
        help="Comma-separated APP shift labels to extract",
    )
    parser.add_argument(
        "--app-schedule-name",
        default="BSW Hospital Medicine Dallas APP",
        help="Substring of the APP schedule type name in the dropdown",
    )
    parser.add_argument(
        "--md-schedule-name",
        default="",
        help="Substring of the MD schedule type name in the dropdown (leave empty to use first item)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    lb_username = os.environ["LB_USERNAME"]
    lb_password = os.environ["LB_PASSWORD"]

    if args.recon:
        run_triage_recon(lb_username, lb_password)
    else:
        md_shifts = [s.strip() for s in args.md_shifts.split(",") if s.strip()]
        app_shifts = [s.strip() for s in args.app_shifts.split(",") if s.strip()]

        schedule = scrape_triage_schedule(
            username=lb_username,
            password=lb_password,
            md_shifts=md_shifts,
            app_shifts=app_shifts,
            app_schedule_name=args.app_schedule_name,
            md_schedule_name=args.md_schedule_name,
            headless=False,
        )

        print(f"\nTriage Schedule for {schedule.date}:")
        print(f"  Total shifts extracted: {len(schedule.shifts)}")
        for shift in schedule.shifts:
            next_day_tag = " [NEXT DAY]" if shift.is_next_day_t1 else ""
            providers_str = ", ".join(shift.providers) if shift.providers else "(none)"
            print(
                f"  [{shift.source.upper()}] {shift.label}{next_day_tag}: "
                f"{providers_str} | {shift.start_time}–{shift.end_time}"
            )
