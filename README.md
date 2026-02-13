# LBOpenShiftFinder

Automatically finds open shifts on the Lightning Bolt (PerfectServe) schedule viewer and syncs them to a Google Calendar. Filters out days you're already working so you only see shifts you can actually pick up.

## How It Works

1. **Fetches your personal shifts** from your Lightning Bolt iCal subscription feed
2. **Scrapes open shifts** ("OPEN 1", "OPEN 2", etc.) from the LB Viewer using Playwright
3. **Filters** open shifts to exclude days you're already scheduled
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
git clone https://github.com/your-username/LBOpenShiftFinder.git
cd LBOpenShiftFinder
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

| Variable | Description |
|----------|-------------|
| `LB_USERNAME` | Lightning Bolt login email |
| `LB_PASSWORD` | Lightning Bolt login password |
| `LB_ICAL_URL` | Your iCal subscription URL from LB |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service account key JSON (single line) |
| `GOOGLE_CALENDAR_ID` | Target Google Calendar ID |

### 3. Google Calendar Service Account Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable the **Google Calendar API**
3. Go to **IAM & Admin > Service Accounts** and create a service account
4. Create a JSON key and download it
5. In Google Calendar, share your target calendar with the service account email (give it **"Make changes to events"** permission)
6. Paste the JSON key contents (collapsed to a single line) as `GOOGLE_SERVICE_ACCOUNT_JSON`

Verify setup:
```bash
python scripts/verify_google_setup.py
```

### 4. GitHub Actions (Automated Runs)

Add the same variables as **repository secrets** in your GitHub repo settings. The workflow at `.github/workflows/sync.yml` runs automatically at 6am, 10am, 2pm, and 6pm CT, or manually via the Actions tab.

## Usage

### Run Locally

```bash
# Full sync (headless)
python -m src.main

# Debug with visible browser
python -m src.scraper

# Recon mode — navigates to the schedule, tests extraction, pauses for inspection
python -m src.scraper --recon
```

### Calendar Events

Open shifts appear on your Google Calendar as:
- **Title:** `OPEN: {Assignment} ({Label})` (e.g., "OPEN: R27 (OPEN 1)")
- **Color:** Sage (green)
- **Description:** Auto-managed notice with shift details

Events are tagged with a private extended property so the tool only manages its own events.

## Project Structure

```
src/
  main.py            # Orchestrator — runs the full sync pipeline
  scraper.py         # Playwright scraper for Lightning Bolt Viewer
  ical_parser.py     # Parses your iCal feed for working days
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
