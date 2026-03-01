# LBOpenShiftFinder — Claude Context

## Project Overview

Scrapes [Lightning Bolt (PerfectServe)](https://lblite.lightning-bolt.com) schedule viewer for open shifts, filters them against the user's personal iCal schedule, and syncs results to Google Calendar via a service account. Runs automatically on GitHub Actions 4× daily.

**Three shift types are tracked:**
- **Open shifts** — unassigned shifts available to pick up (sage green, colorId=2)
- **Picked-up shifts** — open shifts the user has claimed (blueberry blue, colorId=9)
- **Scheduled shifts** — regular personal shifts from iCal subscription (grape purple, colorId=3)

---

## File Structure

```
src/
  scraper.py       — Playwright scraper: login → navigate → extract shifts
  ical_parser.py   — iCal feed parser + conflict detection logic
  calendar_sync.py — Google Calendar API (service account auth)
  main.py          — Orchestrator: fetch → diff → sync
  notifier.py      — Email notifications (SMTP) for failures and warnings
  state.py         — JSON state persistence (load/save)
  models.py        — Dataclasses: Shift, OpenShift, SyncedShift, SyncState
state/
  synced_shifts.json  — Persisted state (committed by GitHub Actions)
.github/workflows/
  sync.yml         — GitHub Actions workflow (runs 4× daily)
.env.example       — All config options documented with defaults
scripts/
  verify_google_setup.py  — One-time helper to verify GCal service account
```

---

## Data Models (`src/models.py`)

```python
Shift           # iCal personal shift: date, start_time, end_time, assignment
OpenShift       # Scraped shift: date, start_time, end_time, assignment, label
SyncedShift     # Synced shift: all OpenShift fields + google_event_id
SyncState       # Full state: last_run + three lists of SyncedShift
```

- All datetimes are **naive local (America/Chicago)** ISO strings — never tz-aware
- `unique_key` property on each model drives the diff logic (deduplication)
- `Shift.to_open_shift()` converts iCal shifts to `OpenShift` for calendar sync

---

## Scraper (`src/scraper.py`)

### Navigation Flow (7 steps)
1. Navigate to login URL, fill credentials, submit
2. Wait for selection screen → click "Viewer" application tile
3. Click "Me" button in context ribbon → sidebar opens
4. Click the target organization/view link (the `bsw_dallas_link` key in `SELECTORS` — update this selector if switching organizations)
5. Open Settings (gear icon) → enable "Show Times" checkbox → close dropdown
6. Open Filter Personnel → search "Open" → check all results → close
7. Loop through months extracting the weekly grid

### Critical Playwright Lessons
1. **LB is a SPA** — never use `networkidle`, always use element-based waits
2. **WeekContainer parent has height/width: 0px** — use `state="attached"` not the default `visible`
3. **CSS checkboxes** — `#show_times` input is visually hidden, must use `page.evaluate("el => el.click()")` or click the label; never Playwright `.click()` on hidden inputs
4. **All dropdowns cover `.spacer`** — use `page.keyboard.press("Escape")` to close both the Settings dropdown and the Filter Personnel dropdown; never click `.spacer > div:nth-child(1)` while a menu is open (it times out because the open menu overlays it)
5. **Filter personnel button** — selector is `div:nth-child(2) > div:nth-child(1)` (2nd child in flex row); `div:nth-child(1)` is the settings gear instead

### Grid DOM Structure
```
.header-wrapper
  └── .header .date[data-cy='dayColumn'][data-date='MM/DD/YYYY']   ← week dates

.data-rows
  └── .DataRow
        ├── .leftCol[data-cy='leftCol']                            ← assignment name
        └── [data-cy='dataCell'] × 7                               ← one per day
              └── span[data-cy='DataCellTextValue']                ← cell text
                    └── span.times                                 ← time range (if enabled)
```

- Headers and rows use **absolute positioning** (`top: Npx`); a row belongs to the nearest header above it
- **Taken shifts** have `span.text.pending-chg` class on the text element
- **Picked-up shifts** (taken by user) are identified by `MY_NAME_PATTERN` regex on cell text (case-insensitive)
- Open shifts have label text matching `OPEN\s*\d*` (e.g. "OPEN 1", "OPEN 2")

### Return Value
```python
scrape_open_shifts(username, password) -> tuple[list[OpenShift], list[OpenShift]]
#                                                    ^open             ^picked
```

### Multi-Month Scraping
The scraper loops forward through months until no new open shifts are found in a given month, deduplicating across overlapping month views.

---

## iCal Parser (`src/ical_parser.py`)

- Fetches from `LB_ICAL_URL` using `requests` + `recurring-ical-events`
- Lookahead window: `ICAL_LOOKAHEAD_DAYS` (default 180) days from today
- All times converted to naive **America/Chicago** local datetimes
- All-day events (date-only DTSTART) get `T00:00:00` appended

### Conflict Detection (`conflicts_with_my_shifts`)
A conflict exists when an open shift:
1. **Overlaps** with a scheduled shift, OR
2. Has **less than `MIN_REST_HOURS`** (default 8) of gap before or after any scheduled shift

Picked-up shifts are combined with iCal shifts for conflict checking (prevents double-booking).

---

## Calendar Sync (`src/calendar_sync.py`)

- Auth: **service account** (not OAuth) — JSON key stored as `GOOGLE_SERVICE_ACCOUNT_JSON` secret
- Events are tagged with `extendedProperties.private.lbOpenShiftFinder = "true"` for tracking
- Event summaries:
  - Open: `"OPEN: R24 (OPEN 1)"`
  - Picked: `"PICKED: R24 (OPEN 1)"`
  - Scheduled: `"R24"` (just the assignment name)
- Colors are configurable via env vars (see Configuration section)

---

## Orchestrator (`src/main.py`)

### Full Pipeline
```
1. Load state (state/synced_shifts.json)
2. Fetch iCal shifts (my_shifts)
3. Scrape LB (open_shifts, picked_shifts)
4. Build combined_my_shifts = my_shifts + picked_shifts (for conflict detection)
5. Filter available_shifts (remove conflicts)
6. Build scheduled_as_open = my_shifts excluding EXCLUDED_SHIFT_LABELS
7. Diff each type (open, picked, scheduled) against previous state
8. If KEEP_PAST_SHIFTS=true: rescue started shifts from to_remove lists
9. Call sync_to_calendar() for all adds/removes
10. Save updated state
```

### Key Helper Functions
- `_str_to_bool(value)` — converts env var strings to bool (`"true"`, `"1"`, `"yes"`, `"on"`)
- `_shift_has_started(shift)` — returns True if `start_time < now()` (protects ongoing + completed shifts)

### Notification Flow (`src/notifier.py`)
`main()` installs a `WarningCollector` (a `logging.Handler`) on the root logger before calling `_run()`. After `_run()` completes:
- **On success with warnings**: sends a warning email listing all WARNING+ messages captured during the run
- **On unhandled exception**: sends a failure email with the exception and full traceback, then re-raises so GitHub Actions still marks the run as failed

A shift with no scraped times (previously silent DEBUG) is promoted to WARNING so it surfaces in notifications.

`send_notification()` is a no-op when `NOTIFY_ENABLED` is false/unset or any required SMTP field is missing.

---

## State (`src/state.py` + `state/synced_shifts.json`)

```json
{
  "last_run": "2026-02-27T12:00:00+00:00",
  "synced_shifts": [...],      // open shifts (sage green)
  "picked_shifts": [...],      // picked-up shifts (blueberry blue)
  "scheduled_shifts": [...]    // scheduled iCal shifts (grape purple)
}
```

Each entry in the lists is a `SyncedShift` with: `date`, `start_time`, `end_time`, `assignment`, `label`, `google_event_id`.

**To force a full re-sync:** delete `state/synced_shifts.json`, commit, push. The next run removes all calendar events and recreates them fresh.

---

## GitHub Actions Workflow (`.github/workflows/sync.yml`)

- Runs at `0 12,16,20 * * *` and `0 0 * * *` UTC (≈ 6am, 10am, 2pm, 6pm CT)
- Manual trigger also available (`workflow_dispatch`)
- Has `permissions: contents: write` so it can commit state updates
- Caches pip packages (via `setup-python cache: "pip"`) and Playwright browsers (`actions/cache@v4` keyed on `hashFiles('requirements.txt')`)
- After sync, commits `state/synced_shifts.json` if changed

**Important:** GitHub Actions frequently commits state between local commits, causing push rejections. Always use:
```bash
git pull --rebase && git push
```

---

## Configuration (`.env` / GitHub Secrets)

### Required
| Variable | Description |
|---|---|
| `LB_USERNAME` | Lightning Bolt login email |
| `LB_PASSWORD` | Lightning Bolt password |
| `LB_ICAL_URL` | Personal iCal subscription URL from LB |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full JSON key (single line) for GCal service account |
| `GOOGLE_CALENDAR_ID` | Target Google Calendar ID (e.g. `user@gmail.com`) |

### Optional (with defaults)
| Variable | Default | Description |
|---|---|---|
| `LB_VIEW_NAME` | `"BSW Hospital Medicine - Dallas"` | Substring of the sidebar view link to click after login. Falls back to first link if unset or not found |
| `LOCAL_TIMEZONE` | `"America/Chicago"` | IANA timezone name used for all time comparisons and calendar events. Must match where shifts are worked |
| `MY_NAME_PATTERN` | `""` | Regex to detect your picked-up shifts (e.g. `(john\|jonathan)\s+doe`). Empty = no picked-up detection |
| `KEEP_PAST_SHIFTS` | `false` | When true, preserves started/completed shifts on calendar (for payroll verification) |
| `SYNC_OPEN_SHIFTS` | `true` | Sync available open shifts |
| `SYNC_PICKED_SHIFTS` | `true` | Sync picked-up shifts |
| `SYNC_SCHEDULED_SHIFTS` | `true` | Sync iCal scheduled shifts |
| `EXCLUDED_SHIFT_LABELS` | `""` | Comma-separated labels to skip syncing (e.g. `Vacation`). Still used for conflict detection |
| `MIN_REST_HOURS` | `8` | Minimum gap between shifts for conflict detection |
| `ICAL_LOOKAHEAD_DAYS` | `180` | How many days ahead to fetch from iCal |
| `OPEN_SHIFT_COLOR` | `2` | Google Calendar colorId for open shifts (Sage green) |
| `PICKED_SHIFT_COLOR` | `9` | Google Calendar colorId for picked-up shifts (Blueberry blue) |
| `SCHEDULED_SHIFT_COLOR` | `3` | Google Calendar colorId for scheduled shifts (Grape purple) |
| `NOTIFY_ENABLED` | `false` | Send email notifications for failures and warnings |
| `NOTIFY_EMAIL` | `""` | Recipient email address for notifications |
| `SMTP_HOST` | `""` | SMTP server hostname (e.g. `smtp.gmail.com`) |
| `SMTP_PORT` | `587` | SMTP port — 587 for STARTTLS, 465 for SSL |
| `SMTP_USERNAME` | `""` | SMTP login username |
| `SMTP_PASSWORD` | `""` | SMTP password or app password |

**Google Calendar colorIds:** 1=Lavender, 2=Sage, 3=Grape, 4=Flamingo, 5=Banana, 6=Tangerine, 7=Peacock, 8=Graphite, 9=Blueberry, 10=Basil, 11=Tomato

---

## Changing Calendar Colors

**Changing color env vars does NOT retroactively update existing events** — only newly added events use the new color. To update all events:
1. Delete `state/synced_shifts.json`, commit, push
2. Next sync removes all old events and recreates them with new colors

---

## Common Pitfalls & Notes

- **`LOCAL_TIMEZONE` must be consistent** — GitHub Actions runs UTC; `_shift_has_started()` uses `datetime.now(ZoneInfo(LOCAL_TIMEZONE))` to avoid a 6-hour offset bug where upcoming shifts would appear to have already started
- **`KEEP_PAST_SHIFTS` uses `start_time < now`** (not end_time) — protects shifts that are ongoing OR completed, so a mid-shift sync never deletes an active event
- **`EXCLUDED_SHIFT_LABELS`** filters calendar sync only — excluded labels (e.g. Vacation) still participate in conflict detection to block open shifts during those days
- **`MY_NAME_PATTERN` defaults to empty** — if not set, picked-up shift detection is silently disabled (shifts show as open instead of picked)
- **iCal lookahead was historically 60 days** — raised to 180 because shifts near the boundary were slipping through conflict detection
- **State file is committed to git** by the Actions bot after every sync — this is intentional persistence
- **`remove_open_shift` catches exceptions** — manually deleted calendar events don't break the sync

---

## Development Workflow

```bash
# Run locally
cp .env.example .env   # fill in credentials
pip install -r requirements.txt
playwright install chromium --with-deps
python -m src.main

# Always pull before push (Actions bot commits state frequently)
git pull --rebase && git push

# Force full re-sync (clears all calendar events and state)
rm state/synced_shifts.json
git add state/synced_shifts.json
git commit -m "Clear sync state to force re-sync"
git pull --rebase && git push
```

---

## Dependencies

```
playwright>=1.40.0            # Browser automation
google-api-python-client      # Google Calendar API
google-auth                   # Service account auth
icalendar>=5.0.0              # iCal parsing
recurring-ical-events>=2.1.0  # Recurring event expansion
requests>=2.31.0              # HTTP for iCal fetch
python-dotenv>=1.0.0          # .env loading
```
