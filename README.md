# LBOpenShiftFinder

Automatically finds open shifts on the Lightning Bolt (PerfectServe) schedule viewer and syncs them to a Google Calendar. Filters out shifts that conflict with your schedule — including overlap and minimum rest time — so you only see shifts you can actually pick up.

## How It Works

1. **Fetches your personal shifts** from your Lightning Bolt iCal subscription feed (configurable lookahead, default 180 days)
2. **Scrapes open shifts** ("OPEN 1", "OPEN 2", etc.) and **picked-up shifts** (open shifts taken by you, detected via `MY_NAME_PATTERN`) from the LB Viewer using Playwright, advancing through months until no more shifts are found
3. **Filters out conflicts** — any open shift that overlaps with your schedule or doesn't leave at least 8 hours of rest (configurable) is excluded. Picked-up shifts also count as your schedule for this check.
4. **Syncs three shift types** to a Google Calendar in distinct colors:
   - **Open shifts** (available to pick up) — Sage green
   - **Picked-up shifts** (shifts you've claimed) — Blueberry blue
   - **Scheduled shifts** (your regular iCal shifts) — Grape purple
5. **Runs automatically** via GitHub Actions (4x daily) or manually on-demand

## Setup

### Prerequisites

- Python 3.12+
- A Lightning Bolt account with Viewer access
- A Google Cloud project with the Calendar API enabled
- A Google Calendar shared with a service account

### 1. Clone and Install

```bash
git clone https://github.com/vpatel9202/LBOpenShiftFinder.git
cd LBOpenShiftFinder
pip install -r requirements.txt
playwright install chromium
```

### 2. Lightning Bolt iCal URL

You need your personal iCal subscription URL from Lightning Bolt. This is used to fetch your scheduled shifts so the tool knows which open shifts conflict with your schedule.

1. Log into Lightning Bolt
2. Find your iCal subscription link (usually under schedule settings or export options)
3. Copy the full URL — it looks like `https://lblite.lightning-bolt.com/ical/...`

### 3. Google Calendar Service Account Setup

The tool uses a Google service account (not personal OAuth) to manage calendar events. This allows it to run unattended in CI.

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Enable the **Google Calendar API**:
   - Go to **APIs & Services > Library**
   - Search for "Google Calendar API" and click **Enable**
4. Create a service account:
   - Go to **IAM & Admin > Service Accounts**
   - Click **Create Service Account**
   - Give it a name (e.g., "lb-shift-sync") and click **Create and Continue**
   - Skip the optional role/access steps and click **Done**
5. Create a JSON key:
   - Click on the service account you just created
   - Go to the **Keys** tab
   - Click **Add Key > Create new key > JSON**
   - Download the JSON file — you'll need the contents for configuration
6. Share your Google Calendar with the service account:
   - Open [Google Calendar](https://calendar.google.com/)
   - Go to the target calendar's **Settings and sharing**
   - Under **Share with specific people or groups**, add the service account email
     (found in the JSON file as `client_email`, e.g., `lb-shift-sync@your-project.iam.gserviceaccount.com`)
   - Set permission to **"Make changes to events"**
7. Find your Calendar ID:
   - In Calendar Settings, scroll to **Integrate calendar**
   - Copy the **Calendar ID** (for your primary calendar, it's your email address)

Verify the setup works:
```bash
python scripts/verify_google_setup.py
```

### 4. Configure Environment (Local Runs)

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description | Example |
|----------|-------------|---------|
| `LB_USERNAME` | Lightning Bolt login email | `you@hospital.org` |
| `LB_PASSWORD` | Lightning Bolt login password | |
| `LB_ICAL_URL` | Your iCal subscription URL from LB | `https://lblite.lightning-bolt.com/ical/...` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Entire service account JSON key (single line) | `{"type":"service_account",...}` |
| `GOOGLE_CALENDAR_ID` | Target Google Calendar ID | `you@gmail.com` |

For `GOOGLE_SERVICE_ACCOUNT_JSON`, paste the entire contents of the downloaded JSON key file on a single line.

### 5. GitHub Actions (Automated Runs)

To run the sync automatically, you need to add **repository secrets** in your GitHub repo:

1. Go to your repo on GitHub
2. Click **Settings > Secrets and variables > Actions**
3. Click **New repository secret** and add each of the following:

| Secret Name | Value |
|-------------|-------|
| `LB_USERNAME` | Your Lightning Bolt login email |
| `LB_PASSWORD` | Your Lightning Bolt login password |
| `LB_ICAL_URL` | Your iCal subscription URL |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The full JSON key file contents (can be multi-line) |
| `GOOGLE_CALENDAR_ID` | Your Google Calendar ID |

The workflow at `.github/workflows/sync.yml` runs automatically at approximately 6am, 10am, 2pm, and 6pm CT, and can also be triggered manually:

1. Go to the **Actions** tab in your repo
2. Click **Sync Open Shifts** in the left sidebar
3. Click **Run workflow**

To **disable** automatic runs: Actions tab > click the workflow > `...` menu > **Disable workflow**. You can re-enable it the same way.

## Configuration

All configuration options can be set in your `.env` file (for local runs) or as GitHub secrets (for automated runs):

### Scraper Options

| Variable | Default | Description |
|----------|---------|-------------|
| `LB_VIEW_NAME` | *(your org name)* | Substring of the sidebar view link clicked after login. Falls back to first available link if not found |
| `MY_NAME_PATTERN` | `""` | Case-insensitive regex matching your name in the schedule, used to detect picked-up shifts (e.g. `(john\|jonathan)\s+doe`). Empty = no picked-up detection |

### Sync Options

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNC_OPEN_SHIFTS` | `true` | Sync available open shifts to calendar |
| `SYNC_PICKED_SHIFTS` | `true` | Sync shifts you've picked up to calendar |
| `SYNC_SCHEDULED_SHIFTS` | `true` | Sync your regular scheduled shifts (from iCal) to calendar |
| `KEEP_PAST_SHIFTS` | `false` | When `true`, shifts that have already started are never removed — useful for payroll verification |
| `EXCLUDED_SHIFT_LABELS` | `""` | Comma-separated iCal event labels to exclude from calendar sync (e.g. `Vacation`). These still block open shifts via conflict detection. |
| `LOCAL_TIMEZONE` | `America/Chicago` | IANA timezone for all time comparisons and calendar events. Required to match correctly on GitHub Actions (which runs UTC) |
| `MIN_REST_HOURS` | `8` | Minimum hours of rest required between shifts for conflict filtering |
| `ICAL_LOOKAHEAD_DAYS` | `180` | How many days ahead to fetch from your iCal feed |

### Calendar Colors

Customize which Google Calendar color each shift type uses (1-11):

| Variable | Default | Description |
|----------|---------|-------------|
| `OPEN_SHIFT_COLOR` | `2` | Color for available open shifts (Sage/green) |
| `PICKED_SHIFT_COLOR` | `9` | Color for picked-up shifts (Blueberry/blue) |
| `SCHEDULED_SHIFT_COLOR` | `3` | Color for scheduled shifts (Grape/purple) |

**Available colors:** 1=Lavender, 2=Sage, 3=Grape, 4=Flamingo, 5=Banana, 6=Tangerine, 7=Peacock, 8=Graphite, 9=Blueberry, 10=Basil, 11=Tomato

### Example `.env` with Custom Configuration

```env
# Credentials (required)
LB_USERNAME=you@hospital.org
LB_PASSWORD=your_password
LB_ICAL_URL=https://lblite.lightning-bolt.com/ical/your-feed
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
GOOGLE_CALENDAR_ID=you@gmail.com

# Scraper (optional)
LB_VIEW_NAME=My Hospital - Main Campus  # sidebar link text to click after login
MY_NAME_PATTERN=(john|jonathan)\s+doe   # regex to detect your picked-up shifts

# Sync options (optional)
LOCAL_TIMEZONE=America/Chicago
KEEP_PAST_SHIFTS=true        # Keep worked shifts for payroll verification
SYNC_OPEN_SHIFTS=true
SYNC_PICKED_SHIFTS=true
SYNC_SCHEDULED_SHIFTS=false  # Don't sync regular shifts, only open/picked
EXCLUDED_SHIFT_LABELS=Vacation,CME  # These won't appear on calendar but still block open shifts
MIN_REST_HOURS=10            # Require 10 hours between shifts instead of 8
ICAL_LOOKAHEAD_DAYS=90       # Only look 3 months ahead

# Colors (optional)
OPEN_SHIFT_COLOR=5           # Use Banana (yellow) for open shifts
PICKED_SHIFT_COLOR=11        # Use Tomato (red) for picked shifts
SCHEDULED_SHIFT_COLOR=7      # Use Peacock (teal) for scheduled shifts
```

## Usage

### Run Locally

```bash
# Full sync (headless browser)
python -m src.main

# Debug with visible browser
python -m src.scraper

# Recon mode — navigates to the schedule, tests extraction, pauses for inspection
python -m src.scraper --recon
```

### Filtering Rules

An open shift is excluded if any of the following are true:
- It **overlaps in time** with one of your scheduled or picked-up shifts (even by 1 minute)
- There is **less than the minimum rest time** (default 8 hours, configurable via `MIN_REST_HOURS`) between the end of one shift and the start of the other (in either direction)
  - Example: Your shift ends at 5am → an open shift starting at 7am the same day is blocked (only 2 hours gap)
  - Example: An open shift ends at 7am → your shift starts at 7am → blocked (0 hours gap)

**`EXCLUDED_SHIFT_LABELS`** — iCal events with these labels (e.g. `Vacation`) are excluded from calendar sync, but they **still participate in conflict detection**. This means a vacation day will block open shifts that fall on that day, even though the vacation event itself won't appear on your calendar.

### Calendar Events

The tool creates color-coded events for each shift type:

**Open Shifts** (available to pick up):
- **Title:** `OPEN: {Assignment} ({Label})` (e.g., "OPEN: R27 (OPEN 1)")
- **Color:** Sage/green (default, configurable)
- **Description:** Shift available on Lightning Bolt

**Picked Shifts** (shifts you've picked up):
- **Title:** `PICKED: {Assignment} ({Label})` (e.g., "PICKED: R27 (OPEN 1)")
- **Color:** Blueberry/blue (default, configurable)
- **Description:** Shift picked up by you

**Scheduled Shifts** (your regular shifts from iCal):
- **Title:** `{Assignment}` (e.g., "R27")
- **Color:** Grape/purple (default, configurable)
- **Description:** Your regular scheduled shift

All events are tagged with a private extended property (`lbOpenShiftFinder=true`) so the tool only manages its own events — your other calendar entries are never touched.

### State Management

The file `state/synced_shifts.json` tracks which shifts are currently on your calendar (including their Google event IDs). This allows the tool to:
- **Detect removed shifts** — if a shift disappears from LB or your iCal feed, it gets deleted from your calendar
- **Avoid duplicates** — shifts already on the calendar aren't re-added
- **Self-correct** — if filtering rules or sync settings change, the calendar is updated accordingly

In CI, this file is auto-committed after each run. If you need to reset, replace its contents with:
```json
{"last_run": null, "synced_shifts": [], "picked_shifts": [], "scheduled_shifts": []}
```

## Project Structure

```
src/
  main.py            # Orchestrator — runs the full sync pipeline
  scraper.py         # Playwright scraper for Lightning Bolt Viewer
  ical_parser.py     # Parses iCal feed + conflict detection logic
  calendar_sync.py   # Google Calendar API integration
  models.py          # Data classes (Shift, OpenShift, SyncedShift, SyncState)
  state.py           # State persistence (tracks synced shifts)
scripts/
  verify_google_setup.py   # Verifies service account can access calendar
state/
  synced_shifts.json       # Persisted sync state (auto-committed by CI)
.github/workflows/
  sync.yml                 # GitHub Actions cron workflow
```

## Development

### Recon Mode

When the LB Viewer UI changes, use recon mode to inspect the DOM and update selectors:

```bash
python -m src.scraper --recon
```

This opens a visible browser, automates the full navigation flow, enables "Show Times", runs the extraction logic, and then pauses so you can inspect with DevTools. Screenshots and HTML dumps are saved to `screenshots/`.

### Troubleshooting

- **Screenshots**: Every sync run saves screenshots to `screenshots/`. In CI, these are uploaded as artifacts (retained for 7 days) — check them under the Actions tab if a run fails.
- **Debug logging**: Run with `python -m src.main` to see `FILTERED OUT` lines showing which shifts were excluded and why.
- **State issues**: If the calendar gets out of sync, reset `state/synced_shifts.json` to `{"last_run": null, "synced_shifts": [], "picked_shifts": [], "scheduled_shifts": []}` and run again — all managed events will be re-created.
- **Selector changes**: If LB updates their UI, selectors in `src/scraper.py` may break. Use recon mode to identify new selectors.
