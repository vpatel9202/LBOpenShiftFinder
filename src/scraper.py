"""Scrape open shifts from the Lightning Bolt web viewer using Playwright."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, time
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, Browser, TimeoutError as PwTimeout

from src.models import OpenShift

logger = logging.getLogger(__name__)

LB_LOGIN_URL = "https://lblite.lightning-bolt.com/login"
SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"
MY_NAME_PATTERN = os.getenv("MY_NAME_PATTERN", "")

# =============================================================================
# CSS SELECTORS
# =============================================================================

SELECTORS = {
    # Login page
    "username_input": "[name='txtUserName']",
    "password_input": "[name='txtUserPass']",
    "login_button": "[name='submit']",

    # Post-login selection screen: "Viewer" application tile
    "viewer_tile": "#SelectionScreen .ApplicationElement",

    # Viewer top bar: "Me" button (switches to view picker sidebar)
    "me_button": "#ContextRibbon .ContextRibbonItem.limit-width-large.view",

    # Sidebar dialog: "BSW Hospital Medicine - Dallas" link
    "bsw_dallas_link": ".Dialog.isTop.ViewOptions a.view-link",

    # Filter Personnel dropdown button (2nd child in the flex row, 1st div within it)
    "filter_personnel_btn": "#TopBar .flex-between.flex-1 > .flex:nth-child(1) > div:nth-child(2) > div:nth-child(1)",

    # Search input within the filter dropdown
    "filter_search_input": ".menu.open .search-input",

    # Checkbox items in the filter dropdown (after searching "Open")
    "filter_checkboxes": ".menu.open .scrollable .pointer.listitem .fa-checkbox",

    # A neutral area to click to close the dropdown
    "close_dropdown_click": ".spacer > div:nth-child(1)",

    # Month navigation: right arrow to go to next month
    "next_month_arrow": "#ContextRibbon i.fa:nth-child(2)",

    # Settings dropdown (gear icon) — used to enable "Show Times"
    "settings_btn": "#TopBar .flex-between.flex-1 > .flex:nth-child(1) > div:nth-child(1)",
    "show_times_checkbox": "#show_times",

    # Schedule grid container
    "grid_container": ".StandardContainer .WeekContainer",

    # Week header rows containing date columns
    "header_wrapper": ".header-wrapper",
    "header_date": ".header .date[data-cy='dayColumn']",

    # Data rows containing shift cells
    "data_rows": ".data-rows",
    "data_row": ".DataRow",
    "left_col": ".leftCol[data-cy='leftCol']",
    "data_cell": "[data-cy='dataCell']",

    # Shift cell text (inside DataCell)
    "cell_text": "span[data-cy='DataCellTextValue']",

    # Times sub-span (appears when "Show Times" is enabled)
    # e.g. <span class="times">9:00pm – 7:00am (02/18)</span>
    "cell_times": "span.times",

    # Shift popup (appears when clicking a cell)
    "slot_popup": ".SlotPopUp:not(.hidden)",
    "slot_popup_any": ".SlotPopUp",
}


def _take_screenshot(page: Page, name: str) -> Path:
    """Save a screenshot for debugging."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"{timestamp}_{name}.png"
    page.screenshot(path=str(path), full_page=True)
    logger.info(f"Screenshot saved: {path}")
    return path


def _dump_html(page: Page, name: str, selector: str | None = None) -> Path:
    """Dump HTML content to a file for inspection."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"{timestamp}_{name}.html"

    if selector:
        try:
            element = page.query_selector(selector)
            html = element.inner_html() if element else "SELECTOR NOT FOUND"
        except Exception:
            html = "SELECTOR ERROR"
    else:
        html = page.content()

    path.write_text(html, encoding="utf-8")
    logger.info(f"HTML dump saved: {path}")
    return path


def _login(page: Page, username: str, password: str) -> None:
    """Log into Lightning Bolt."""
    logger.info("Navigating to login page...")
    page.goto(LB_LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_selector(SELECTORS["username_input"], timeout=15000)

    logger.info("Filling login credentials...")
    page.fill(SELECTORS["username_input"], username)
    page.fill(SELECTORS["password_input"], password)
    page.click(SELECTORS["login_button"])

    # Wait for the selection screen to appear (proves login succeeded)
    page.wait_for_selector(SELECTORS["viewer_tile"], timeout=30000)
    logger.info("Login completed")


def _navigate_to_open_shifts(page: Page) -> None:
    """Navigate from the post-login selection screen to the filtered open shifts view.

    Flow:
      1. Click "Viewer" tile on the selection screen
      2. Click "Me" button in the top bar
      3. Click "BSW Hospital Medicine - Dallas" in the sidebar
      4. Click "Filter Personnel" dropdown
      5. Search "Open" and select all matching checkboxes
      6. Close the dropdown
    """
    # Step 1: Click the "Viewer" application tile
    logger.info("Clicking Viewer tile...")
    page.wait_for_selector(SELECTORS["viewer_tile"], timeout=15000)
    page.click(SELECTORS["viewer_tile"])
    # Wait for the "Me" button to appear (proves Viewer loaded)
    page.wait_for_selector(SELECTORS["me_button"], timeout=30000)

    # Step 2: Click "Me" button to open the view picker sidebar
    logger.info("Clicking 'Me' button...")
    page.wait_for_selector(SELECTORS["me_button"], timeout=15000)
    page.click(SELECTORS["me_button"])
    page.wait_for_timeout(1500)  # Wait for sidebar animation

    # Step 3: Click "BSW Hospital Medicine - Dallas" in the sidebar
    logger.info("Selecting BSW Hospital Medicine - Dallas...")
    page.wait_for_selector(SELECTORS["bsw_dallas_link"], timeout=10000)

    # There may be multiple view-links; find the one containing "BSW Hospital Medicine - Dallas"
    links = page.query_selector_all(SELECTORS["bsw_dallas_link"])
    clicked = False
    for link in links:
        text = link.inner_text().strip()
        if "BSW Hospital Medicine" in text and "Dallas" in text:
            link.click()
            clicked = True
            break

    if not clicked:
        # Fallback: click the first view-link if only one exists, or log available options
        if links:
            available = [l.inner_text().strip() for l in links]
            logger.warning(f"Could not find BSW Dallas link. Available: {available}")
            logger.info("Clicking first available view link as fallback...")
            links[0].click()
        else:
            raise RuntimeError("No view links found in sidebar")

    # Wait for schedule to render — the filter button proves the schedule page loaded
    page.wait_for_selector(SELECTORS["filter_personnel_btn"], timeout=30000)
    page.wait_for_timeout(2000)  # Extra time for grid to render

    # Step 4: Click "Filter Personnel" dropdown
    logger.info("Opening Filter Personnel dropdown...")
    page.wait_for_selector(SELECTORS["filter_personnel_btn"], timeout=15000)
    page.click(SELECTORS["filter_personnel_btn"])
    page.wait_for_timeout(500)

    # Step 5: Type "Open" in the search box
    logger.info("Searching for 'Open' in personnel filter...")
    page.wait_for_selector(SELECTORS["filter_search_input"], timeout=5000)
    page.fill(SELECTORS["filter_search_input"], "Open")
    page.wait_for_timeout(1000)  # Wait for filter to update

    # Step 6: Select all matching checkboxes
    checkboxes = page.query_selector_all(SELECTORS["filter_checkboxes"])
    logger.info(f"Found {len(checkboxes)} 'Open' filter checkboxes, selecting all...")
    for checkbox in checkboxes:
        checkbox.click()
        page.wait_for_timeout(200)

    # Step 7: Close the dropdown by clicking whitespace
    logger.info("Closing filter dropdown...")
    page.wait_for_timeout(500)
    page.click(SELECTORS["close_dropdown_click"])
    page.wait_for_timeout(1500)  # Wait for schedule to re-render

    logger.info("Navigation to open shifts view complete")


def _enable_show_times(page: Page) -> None:
    """Enable the 'Show Times' checkbox in the settings dropdown.

    This makes shift times visible inline in the grid cells, so we don't
    need to click each cell individually to read times from a popup.

    The checkbox input is CSS-hidden (visual control is label::before),
    so we use JavaScript throughout to avoid Playwright actionability checks.
    """
    logger.info("Enabling 'Show Times' in settings...")

    # Open the settings dropdown by clicking the gear icon
    page.click(SELECTORS["settings_btn"])
    page.wait_for_timeout(500)

    # Toggle via JavaScript — the input is CSS-hidden so Playwright clicks hang
    toggled = page.evaluate("""() => {
        const cb = document.querySelector('#show_times');
        if (!cb) return 'not_found';
        if (cb.checked) return 'already_checked';
        cb.click();
        return 'toggled';
    }""")

    if toggled == "not_found":
        logger.warning("Could not find 'Show Times' checkbox")
    elif toggled == "already_checked":
        logger.info("'Show Times' was already enabled")
    else:
        logger.info("'Show Times' enabled")

    page.wait_for_timeout(500)

    # Close settings dropdown — press Escape since the dropdown overlays
    # the grid spacer and blocks whitespace clicks
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)  # Wait for grid to re-render with times


def _scroll_to_load_grid(page: Page) -> None:
    """Scroll through the entire grid to ensure all virtualized rows are rendered."""
    container = page.query_selector(SELECTORS["grid_container"])
    if not container:
        logger.warning("Could not find grid container for scrolling")
        return

    # Find the scrollable parent (the one with overflow: auto)
    scroll_el = container.query_selector("[style*='overflow']")
    if not scroll_el:
        scroll_el = container

    # Get total scrollable height
    scroll_height = page.evaluate("el => el.scrollHeight", scroll_el)
    client_height = page.evaluate("el => el.clientHeight", scroll_el)

    if scroll_height <= client_height:
        return  # No scrolling needed

    # Scroll in increments to trigger virtualized rendering
    current = 0
    step = client_height // 2
    while current < scroll_height:
        page.evaluate(f"el => el.scrollTop = {current}", scroll_el)
        page.wait_for_timeout(300)
        current += step

    # Scroll back to top
    page.evaluate("el => el.scrollTop = 0", scroll_el)
    page.wait_for_timeout(500)


def _build_date_map(page: Page) -> dict[int, list[str]]:
    """Build a mapping from header element top-position to list of dates (MM/DD/YYYY).

    The grid has multiple week blocks. Each .header-wrapper sits at a specific
    vertical position, followed by .data-rows blocks that belong to it.
    We use the absolute `top` from the style attribute to correlate rows to headers.

    Returns:
        dict mapping header top-position (px) to list of 7 date strings.
    """
    headers = page.query_selector_all(SELECTORS["header_wrapper"])
    date_map: dict[int, list[str]] = {}

    for header in headers:
        style = header.get_attribute("style") or ""
        top_match = re.search(r"top:\s*(\d+)px", style)
        if not top_match:
            continue
        top_px = int(top_match.group(1))

        date_els = header.query_selector_all(SELECTORS["header_date"])
        dates = []
        for d in date_els:
            date_val = d.get_attribute("data-date")
            if date_val:
                dates.append(date_val)
        if dates:
            date_map[top_px] = dates

    logger.info(f"Found {len(date_map)} week headers in grid")
    return date_map


def _get_header_for_row(row_top: int, header_tops: list[int]) -> int | None:
    """Find the header that a data row belongs to.

    A data row belongs to the closest header that is ABOVE it (lower top value).
    """
    best = None
    for ht in header_tops:
        if ht <= row_top:
            if best is None or ht > best:
                best = ht
    return best


def _read_popup_times(page: Page, cell) -> tuple[str | None, str | None]:
    """Click a cell to open the SlotPopUp and read times from it.

    Returns (start_time_text, end_time_text) or (None, None) on failure.
    """
    try:
        cell.click()
        page.wait_for_timeout(800)

        popup = page.query_selector(SELECTORS["slot_popup"])
        if not popup:
            logger.debug("No popup appeared after clicking cell")
            return None, None

        popup_text = popup.inner_text().strip()
        logger.debug(f"Popup text: {popup_text}")

        # Close the popup by clicking elsewhere
        page.click(SELECTORS["close_dropdown_click"])
        page.wait_for_timeout(300)

        return popup_text, None  # Return raw text for parsing
    except Exception as e:
        logger.debug(f"Error reading popup: {e}")
        return None, None


def _extract_open_shifts(page: Page) -> tuple[list[OpenShift], list[OpenShift]]:
    """Parse the schedule grid and extract open shifts and picked-up shifts.

    Grid structure (from DOM recon):
      - .StandardContainer > .WeekContainer holds the entire grid
      - .header-wrapper elements contain week date headers
        - .header > .date[data-cy="dayColumn"][data-date="MM/DD/YYYY"]
      - .data-rows elements contain assignment rows
        - .DataRow > .leftCol (assignment name) + 7x .DataCell (one per day)
        - Each DataCell may contain a .Slot with span[data-cy="DataCellTextValue"]
        - Open shifts show "OPEN 1", "OPEN 2", etc.
        - Taken shifts have span.text.pending-chg class ("OPEN 1 → [Name]")
      - Elements are absolutely positioned; rows belong to the header above them

    Returns:
        Tuple of (open_shifts, picked_shifts) where picked_shifts are those
        taken by the user (detected via MY_NAME_PATTERN match in the cell text).
    """
    open_shifts: list[OpenShift] = []
    picked_shifts: list[OpenShift] = []

    # The WeekContainer's parent has height/width: 0px with overflow visible,
    # so Playwright considers it "hidden". Wait for DOM attachment instead.
    page.wait_for_selector(SELECTORS["grid_container"], timeout=15000, state="attached")

    # Build the date mapping from week headers
    date_map = _build_date_map(page)
    if not date_map:
        logger.error("No week headers found — cannot map cells to dates")
        _dump_html(page, "no_headers_error")
        return open_shifts, picked_shifts

    header_tops = sorted(date_map.keys())

    # Iterate all data row blocks
    data_rows_blocks = page.query_selector_all(SELECTORS["data_rows"])
    logger.info(f"Found {len(data_rows_blocks)} data row blocks in grid")

    for block in data_rows_blocks:
        # Get the vertical position of this row block
        style = block.get_attribute("style") or ""
        top_match = re.search(r"top:\s*(\d+)px", style)
        if not top_match:
            continue
        row_top = int(top_match.group(1))

        # Find which header this row belongs to
        header_top = _get_header_for_row(row_top, header_tops)
        if header_top is None:
            continue
        week_dates = date_map[header_top]

        # Get the assignment name from the left column
        row = block.query_selector(SELECTORS["data_row"])
        if not row:
            continue

        left_col = row.query_selector(SELECTORS["left_col"])
        if not left_col:
            continue
        assignment = left_col.inner_text().strip()

        # Iterate the data cells (one per day column)
        cells = row.query_selector_all(SELECTORS["data_cell"])
        for col_idx, cell in enumerate(cells):
            if col_idx >= len(week_dates):
                break

            # Check for a shift text element
            text_el = cell.query_selector(SELECTORS["cell_text"])
            if not text_el:
                continue

            # Get the full cell text
            cell_text = text_el.inner_text().strip()
            label = cell_text.split("\n")[0].strip()

            # Only process cells that contain "OPEN"
            if not re.match(r"OPEN\s*\d*", label, re.IGNORECASE):
                continue

            # Check if this shift is taken by someone
            text_classes = text_el.get_attribute("class") or ""
            is_picked_by_me = False
            if "pending-chg" in text_classes:
                # Taken shift — check if it's taken by me (case-insensitive match against MY_NAME_PATTERN)
                if MY_NAME_PATTERN and re.search(MY_NAME_PATTERN, cell_text, re.IGNORECASE):
                    is_picked_by_me = True
                    logger.debug(f"Found picked-up shift: {label} on {week_dates[col_idx]}")
                else:
                    # Taken by someone else — skip it
                    continue

            # Map column index to date
            date_str = week_dates[col_idx]  # Format: MM/DD/YYYY
            shift_date = _parse_date(date_str)
            if not shift_date:
                continue

            # Extract times from the .times child span
            # Format: "9:00pm – 7:00am (02/18)" or "8:00am – 5:00pm"
            times_el = text_el.query_selector(SELECTORS["cell_times"])
            start_time, end_time = None, None
            if times_el:
                times_text = times_el.inner_text().strip()
                start_time, end_time = _parse_times(times_text, shift_date)

            if not start_time or not end_time:
                # Fallback: try reading times from popup
                popup_text, _ = _read_popup_times(page, cell)
                if popup_text:
                    start_time, end_time = _parse_times(popup_text, shift_date)

            if not start_time or not end_time:
                logger.warning(f"Could not determine times for {label} on {shift_date} ({assignment})")
                # Still add the shift with date-only times (all-day)
                base = datetime.strptime(shift_date, "%Y-%m-%d")
                start_time = base.replace(hour=0, minute=0).isoformat()
                end_time = base.replace(hour=23, minute=59).isoformat()

            shift = OpenShift(
                date=shift_date,
                start_time=start_time,
                end_time=end_time,
                assignment=assignment,
                label=label,
            )

            if is_picked_by_me:
                picked_shifts.append(shift)
            else:
                open_shifts.append(shift)

    logger.info(f"Found {len(open_shifts)} open shifts and {len(picked_shifts)} picked-up shifts")
    return open_shifts, picked_shifts


def _parse_date(date_text: str) -> str | None:
    """Parse a date string from the LB viewer into YYYY-MM-DD format.

    The data-date attribute uses MM/DD/YYYY (e.g. "02/23/2026").
    """
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%b %d, %Y", "%b %d"):
        try:
            dt = datetime.strptime(date_text, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.now().year)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    logger.warning(f"Could not parse date: '{date_text}'")
    return None


def _parse_single_time(t: str) -> time | None:
    """Parse a single time string like '9:00pm', '7:00am', '7:00 AM', '19:00'."""
    t = t.strip()

    # Normalize: "9:00pm" → "9:00 PM", "7:00am" → "7:00 AM"
    m = re.match(r"^(\d{1,2}:\d{2})\s*([AaPp][Mm]?)$", t)
    if m:
        time_part = m.group(1)
        suffix = m.group(2).upper()
        if len(suffix) == 1:
            suffix += "M"  # "P" → "PM", "A" → "AM"
        normalized = f"{time_part} {suffix}"
        try:
            return datetime.strptime(normalized, "%I:%M %p").time()
        except ValueError:
            pass

    # Try "7:00 AM" / "7:00 PM" as-is
    for fmt in ("%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(t, fmt).time()
        except ValueError:
            continue

    # Try 24-hour "19:00"
    try:
        return datetime.strptime(t, "%H:%M").time()
    except ValueError:
        pass

    return None


def _parse_times(time_text: str, shift_date: str | None) -> tuple[str | None, str | None]:
    """Parse start and end times from the .times span text.

    Actual formats from the LB viewer (with Show Times enabled):
      "9:00pm – 7:00am (02/18)"   — overnight shift, end is next day
      "8:00am – 5:00pm"           — same-day shift
      "9:00pm – 7:00am (03/01)"   — overnight crossing month boundary

    The (MM/DD) suffix indicates the end time falls on a different date.
    When present, we use shift_date for start and the parenthesized date for end.
    When absent, both start and end are on shift_date.
    """
    if not shift_date:
        return None, None

    # Match: "start_time – end_time" with optional "(MM/DD)" suffix
    match = re.search(
        r"(\d{1,2}:\d{2}\s*[AaPp][Mm]?)\s*[-–]\s*(\d{1,2}:\d{2}\s*[AaPp][Mm]?)"
        r"(?:\s*\((\d{1,2}/\d{1,2})\))?",
        time_text,
    )
    if not match:
        # Try 24-hour fallback
        match = re.search(
            r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})"
            r"(?:\s*\((\d{1,2}/\d{1,2})\))?",
            time_text,
        )
    if not match:
        logger.warning(f"Could not parse times: '{time_text}'")
        return None, None

    start_t = _parse_single_time(match.group(1))
    end_t = _parse_single_time(match.group(2))
    end_date_suffix = match.group(3)  # e.g. "02/18" or None

    if not start_t or not end_t:
        logger.warning(f"Could not parse individual times: '{time_text}'")
        return None, None

    start_base = datetime.strptime(shift_date, "%Y-%m-%d")
    start_dt = start_base.replace(hour=start_t.hour, minute=start_t.minute)

    if end_date_suffix:
        # Overnight shift — end time is on a different date
        # The suffix is MM/DD without year; infer year from shift_date
        end_month, end_day = map(int, end_date_suffix.split("/"))
        end_base = start_base.replace(month=end_month, day=end_day)
        # Handle year rollover (Dec shift ending in Jan)
        if end_base < start_base:
            end_base = end_base.replace(year=end_base.year + 1)
        end_dt = end_base.replace(hour=end_t.hour, minute=end_t.minute)
    else:
        end_dt = start_base.replace(hour=end_t.hour, minute=end_t.minute)

    return start_dt.isoformat(), end_dt.isoformat()


def scrape_open_shifts(username: str, password: str, headless: bool = True) -> tuple[list[OpenShift], list[OpenShift]]:
    """Main entry point: scrape open shifts from Lightning Bolt.

    Args:
        username: LB login username/email.
        password: LB login password.
        headless: Run browser in headless mode (False for recon/debug).

    Returns:
        Tuple of (open_shifts, picked_shifts) where picked_shifts are those
        already picked up by the user.
    """
    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=headless)
        page: Page = browser.new_page()

        try:
            _login(page, username, password)
            _take_screenshot(page, "after_login")

            _navigate_to_open_shifts(page)
            _take_screenshot(page, "open_shifts_view")

            # Enable "Show Times" so times appear inline in the grid
            _enable_show_times(page)
            _take_screenshot(page, "show_times_enabled")

            # Scroll grid to ensure all virtualized rows are loaded
            _scroll_to_load_grid(page)

            all_open_shifts: list[OpenShift] = []
            all_picked_shifts: list[OpenShift] = []
            seen_keys: set[str] = set()
            month_num = 0

            while True:
                _scroll_to_load_grid(page)
                month_open, month_picked = _extract_open_shifts(page)
                _take_screenshot(page, f"month_{month_num}_extraction")

                # Deduplicate both lists — month views overlap (e.g. May shows Apr 27–May 3)
                new_open = [s for s in month_open if s.unique_key not in seen_keys]
                new_picked = [s for s in month_picked if s.unique_key not in seen_keys]

                if not new_open and not new_picked:
                    logger.info(f"Month {month_num}: no new shifts — stopping")
                    break

                for s in new_open + new_picked:
                    seen_keys.add(s.unique_key)
                all_open_shifts.extend(new_open)
                all_picked_shifts.extend(new_picked)
                logger.info(f"Month {month_num}: {len(new_open)} open + {len(new_picked)} picked (total: {len(all_open_shifts)} + {len(all_picked_shifts)})")

                # Advance to next month
                month_num += 1
                logger.info(f"Navigating to month {month_num}...")
                page.click(SELECTORS["next_month_arrow"])
                page.wait_for_timeout(2000)

            logger.info(f"Total across {month_num + 1} months: {len(all_open_shifts)} open, {len(all_picked_shifts)} picked")
            return all_open_shifts, all_picked_shifts

        except PwTimeout as e:
            logger.error(f"Timeout during scraping: {e}")
            _take_screenshot(page, "error_timeout")
            _dump_html(page, "error_timeout")
            raise
        except Exception as e:
            logger.error(f"Error during scraping: {e}")
            _take_screenshot(page, "error_general")
            _dump_html(page, "error_general")
            raise
        finally:
            browser.close()


def run_recon(username: str, password: str) -> None:
    """Run in recon mode: headed browser with pauses for DOM inspection.

    Automates the full navigation flow, then pauses at the schedule grid
    so you can inspect the DOM structure of shift cells.
    """
    print("\n" + "=" * 70)
    print("RECON MODE — Lightning Bolt Scraper")
    print("=" * 70)
    print("The browser will navigate automatically through the login and")
    print("menu flow, then pause at the schedule grid for DOM inspection.")
    print(f"Screenshots/HTML will be saved to: {SCREENSHOTS_DIR.resolve()}")
    print("=" * 70 + "\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        # Automated login
        print("[STEP 1] Logging in...")
        _login(page, username, password)
        _take_screenshot(page, "recon_01_after_login")
        print("  Login successful.")

        # Automated navigation to open shifts
        print("[STEP 2] Navigating to open shifts view...")
        _navigate_to_open_shifts(page)
        _take_screenshot(page, "recon_02_open_shifts")
        _dump_html(page, "recon_02_open_shifts")
        print("  Navigation complete. Schedule grid should now be visible.")

        # Enable Show Times
        print("[STEP 3] Enabling 'Show Times'...")
        _enable_show_times(page)
        _take_screenshot(page, "recon_03_show_times")
        _dump_html(page, "recon_03_show_times")
        print("  'Show Times' enabled.")

        # Test extraction
        print("[STEP 4] Testing shift extraction...")
        _scroll_to_load_grid(page)
        open_shifts, picked_shifts = _extract_open_shifts(page)
        print(f"  Found {len(open_shifts)} open shifts:")
        for s in open_shifts:
            print(f"    {s.label}: {s.assignment} on {s.date} ({s.start_time} - {s.end_time})")
        if picked_shifts:
            print(f"  Found {len(picked_shifts)} picked-up shifts:")
            for s in picked_shifts:
                print(f"    {s.label}: {s.assignment} on {s.date} ({s.start_time} - {s.end_time})")

        # Pause for inspection
        print("\n[STEP 5] Manual inspection.")
        print("  Use DevTools (F12) to verify the extracted data.")
        input("  Press Enter when done inspecting to save HTML and close...")
        _take_screenshot(page, "recon_04_final")
        _dump_html(page, "recon_04_final")

        browser.close()

    print("\n" + "=" * 70)
    print("RECON COMPLETE")
    print(f"Check {SCREENSHOTS_DIR.resolve()} for screenshots and HTML dumps.")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    import argparse
    import os
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Lightning Bolt open shift scraper")
    parser.add_argument("--recon", action="store_true", help="Run in recon mode (headed browser with pauses)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    username = os.environ["LB_USERNAME"]
    password = os.environ["LB_PASSWORD"]

    if args.recon:
        run_recon(username, password)
    else:
        open_shifts, picked_shifts = scrape_open_shifts(username, password, headless=False)
        print(f"Open shifts ({len(open_shifts)}):")
        for s in open_shifts:
            print(f"  {s.label}: {s.assignment} on {s.date} ({s.start_time} - {s.end_time})")
        if picked_shifts:
            print(f"\nPicked-up shifts ({len(picked_shifts)}):")
            for s in picked_shifts:
                print(f"  {s.label}: {s.assignment} on {s.date} ({s.start_time} - {s.end_time})")
