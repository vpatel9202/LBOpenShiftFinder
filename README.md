# LBOpenShiftFinder

Automatically finds open shifts on the Lightning Bolt (PerfectServe) schedule viewer and syncs them to a Google Calendar. Filters out shifts that conflict with your schedule — including overlap and minimum rest time — so you only see shifts you can actually pick up.

## How It Works

1. **Fetches your personal shifts** from your Lightning Bolt iCal subscription feed (up to 6 months out)
2. **Scrapes open shifts** ("OPEN 1", "OPEN 2", etc.) from the LB Viewer using Playwright, advancing through months until no more open shifts are found
3. **Filters out conflicts** — any open shift that overlaps with your schedule or doesn't leave at least 8 hours of rest between shifts is excluded
4. **Syncs** available open shifts to a Google Calendar, adding new ones and removing stale ones
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
- It **overlaps in time** with one of your scheduled shifts (even by 1 minute)
- There is **less than 8 hours** between the end of one shift and the start of the other (in either direction)
  - Example: Your shift ends at 5am → an open shift starting at 7am the same day is blocked (only 2 hours gap)
  - Example: An open shift ends at 7am → your shift starts at 7am → blocked (0 hours gap)

The 8-hour rest minimum is defined as `MIN_REST_HOURS` in `src/ical_parser.py` if you need to adjust it.

### Calendar Events

Open shifts appear on your Google Calendar as:
- **Title:** `OPEN: {Assignment} ({Label})` (e.g., "OPEN: R27 (OPEN 1)")
- **Color:** Sage (green)
- **Description:** Auto-managed notice with shift details

Events are tagged with a private extended property (`lbOpenShiftFinder=true`) so the tool only manages its own events — your other calendar entries are never touched.

### State Management

The file `state/synced_shifts.json` tracks which shifts are currently on your calendar (including their Google event IDs). This allows the tool to:
- **Detect removed shifts** — if an open shift disappears from LB, it gets deleted from your calendar
- **Avoid duplicates** — shifts already on the calendar aren't re-added
- **Self-correct** — if filtering rules change, conflicting shifts are removed on the next run

In CI, this file is auto-committed after each run. If you need to reset, replace its contents with:
```json
{"last_run": null, "synced_shifts": []}
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
- **State issues**: If the calendar gets out of sync, reset `state/synced_shifts.json` to `{"last_run": null, "synced_shifts": []}` and run again — all managed events will be re-created.
- **Selector changes**: If LB updates their UI, selectors in `src/scraper.py` may break. Use recon mode to identify new selectors.
