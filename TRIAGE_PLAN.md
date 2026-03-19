# Triage Report — Implementation Plan

## Feature Overview

A separate daily automation that scrapes the Lightning Bolt "Today's Schedule" view
(both MD and APP calendars) and generates a PDF triage sheet, then emails it to
configured recipients. Runs independently of the existing open-shift sync.

**Use case:** The T1/T2/T3 triage physicians need to know who is covering each
admitting/triage shift throughout the day so they can route new admissions correctly.
This sheet replaces a manually filled paper T1 worksheet.

---

## New Files

```
src/
  triage_scraper.py    — Playwright scraper for Today's Schedule (MD + APP)
  triage_pdf.py        — PDF generation (fpdf2)
  triage_notifier.py   — Email with PDF attachment + recipient resolution
  triage_main.py       — Orchestrator (entry point)
.github/workflows/
  triage.yml           — Separate GitHub Actions workflow (runs daily at 6:30am CT)
```

No new state file needed — this is stateless (generate and send, done).

---

## Navigation Flow

### Step 1: Login
Reuse `_login()` from `src/scraper.py` exactly as-is.

### Step 2: Navigate to Viewer
Click the "Viewer" application tile (`#SelectionScreen .ApplicationElement`),
then wait for `#ContextRibbon` to appear (same as existing scraper).

### Step 3: Open Today's Schedule (MD)
After Viewer loads, click "Me" button → sidebar opens. Instead of clicking the
BSW Dallas `.view-link`, click the **"Today's Schedule"** button:

```
Selector: .Dialog.isTop.ViewOptions a.current-action-button
```

Wait for the daily schedule grid to render.

### Step 4: Switch Schedule Type (MD → APP)
The MD schedule loads by default. To switch to the APP schedule:
1. Click the schedule-type dropdown in the context ribbon:
   ```
   Selector: #ContextRibbon div.ContextRibbonItem.today-template div.ribbon-text.no-mobile
   ```
2. Click "BSW Hospital Medicine Dallas APP" in the resulting sidebar:
   ```
   Selector: div.Item:nth-child(2) > div:nth-child(1) > div:nth-child(1)
   ```

### Step 5: Navigate to Next Day (for following-day T1)
There are forward/back date arrows in the context ribbon (visible in screenshots
as "< MARCH 16, 2026 >"). The right arrow selector is likely similar to the
existing `#ContextRibbon i.fa:nth-child(2)` but needs recon confirmation.
Navigate forward one day, extract T1 only, then the session is done.

---

## DOM Structure — Today's Schedule (Daily View)

**Status: Partially known. Recon mode is built into triage_scraper.py.**

The daily view is a Gantt-style chart, structurally different from the weekly grid
used by the open-shift scraper. Based on screenshots and LB SPA patterns:

- Rows represent shift assignments (same `.DataRow` / `.leftCol[data-cy='leftCol']`
  structure is suspected, but not confirmed).
- Each filled slot in a row is a provider bar positioned by time on the X axis.
- Clicking a slot opens a modal showing: shift label, date, time range, provider name.
- Hovering a slot shows a tooltip: date, time range, provider name, [note text if any].

### Tooltip (hover) — confirmed from screenshots
Small popup appears on hover showing:
```
Mar 19, 2026
7:00am - 5:00pm
[Provider Name]
[note text if present, e.g. "long call"]
```

### Click modal — confirmed from screenshots
Orange detail box showing:
```
[Shift Label]  (e.g. "R2")
[Day], [Month] [Date], [Year]
[Start] - [End]
[Provider Name]
SCHEDULI...  CAP: --
DETAILS
[Department path]
Last Modified on — ...
Last Modified by — ...
[REPLACE]
```
Click modal requires dismissal (Escape or click X) before continuing.
**Prefer hover over click** since no dismissal needed.

### Note Icons — confirmed from screenshots
A small note icon appears on the **right side** of a provider's bar when that
provider has a note attached. The note text appears in the hover tooltip.
The yellow highlight visible in hover_tooltip.png is just the hover state,
NOT an indicator of a note.

**Note detection strategy:**
1. Find provider bars in the row
2. Check each bar for a child element matching a note-icon selector
   (exact selector TBD — use recon mode if needed; likely `.fa-note`, `.note-icon`,
   or a similar Font Awesome element)
3. If note icon found → hover over the bar → parse tooltip text
4. Match note text against patterns: "long call", "short call", "on call", "call"

---

## Data Extraction Strategy

For each target shift label:
1. Find the row whose left-column label matches
2. Find all provider slot elements within that row
3. For each slot:
   - Hover to get tooltip → extract provider name + time range + note (if any)
   - Note: multiple providers may share one row (e.g. APP A-2 can have 4+ names)
4. Return list of `TriageShift` objects

**Day boundary:** The "day" for triage purposes runs 7:00 AM → 7:00 AM next day.
Shifts that started before 7:00 AM (i.e., overnight from previous day) should still
appear on today's sheet if they end after 7:00 AM today.

---

## Shift Labels of Interest

### MD Schedule (scraped from Today's Schedule → default/MD view)
```
T1, T2, T3, A2, A3, A4, A5, A5 RRT
```

### Teaching Shifts (MD — detected via note icon, not by label)
On **weekdays**: one provider will have a note "short call" or "long call"
(may appear as just "short" or "long" — use fuzzy/contains matching).
→ Label them "Teaching - Short Call" and "Teaching - Long Call"

On **weekends**: one provider will have a note "on call" or "call"
→ Label them "Teaching - Weekend Call"

The teaching provider will be on one of the Rx shifts (R12–R27 range). Scan
all rows, not just the target list, for note icons. When found, add a
synthetic TriageShift entry with the teaching label.

### APP Schedule (scraped from APP calendar view)
```
APP PA, APP A-1A, APP A-1B, APP A-2, APP A-3
```

**Important:** Physicians can and do fill APP shifts. The name→email lookup must
search the combined provider roster (MD + APP name lists), not just the APP roster.

### Special: Following Day's T1
Navigate to the next calendar day on the MD schedule and extract the T1 row only.
Append as the last row of the PDF with a visual separator and label like:
"Next Day — T1 (handoff)".

---

## PDF Format

Library: **fpdf2** (lightweight, no system dependencies).

### Layout
Clean table with the following columns:
| Shift | Time | Provider(s) | Responsibilities |
|---|---|---|---|

- Header: "BSW Hospital Medicine Dallas — Triage Sheet — [Date]"
- Rows ordered: T1 first, then chronologically by shift start time
  (MD shifts, then APP shifts interleaved by time, then Next Day T1 at bottom)
- Multi-provider rows: provider names stacked within a single table cell
- Teaching shift rows: inserted at the appropriate time position
- "Responsibilities" column content: sourced from `TRIAGE_SHIFT_RESPONSIBILITIES`
  env var (JSON dict, keys = shift labels)
- Page size: Letter (8.5" × 11")

---

## Email Delivery

### Recipients
Two independent mechanisms, combined and deduplicated:

1. **Static list** (`TRIAGE_STATIC_EMAILS`): comma-separated email addresses.
   Used for admin/non-clinical recipients who are always on the list.

2. **Dynamic shift-based** (`TRIAGE_SHIFT_RECIPIENTS`): comma-separated shift
   labels (e.g. `"T1,T2"`). For each label:
   - Look up who is working that shift from the scraped schedule
   - Fuzzy-match provider name against `TRIAGE_NAME_EMAIL_MAP` (JSON dict,
     `{"Full Name": "email@domain.com"}`)
   - If match found (threshold ≥ 85), add their email
   - Physicians filling APP shifts are in the same map — no special handling needed

### Email format
- Subject: `"Triage Sheet — [Weekday], [Month] [Date], [Year]"`
- Body: brief plain-text note
- Attachment: the generated PDF

### SMTP
Reuse existing `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` vars.

---

## Configuration

All new variables prefixed `TRIAGE_`. Add to `.env.example` and `triage.yml`.

| Variable | Default | Description |
|---|---|---|
| `TRIAGE_ENABLED` | `false` | Master switch |
| `TRIAGE_MD_SHIFTS` | `T1,T2,T3,A2,A3,A4,A5,A5 RRT` | MD shift labels to include |
| `TRIAGE_APP_SHIFTS` | `APP PA,APP A-1A,APP A-1B,APP A-2,APP A-3` | APP shift labels to include |
| `TRIAGE_APP_SCHEDULE_NAME` | `BSW Hospital Medicine Dallas APP` | Text to match in the schedule-type dropdown |
| `TRIAGE_SHIFT_RESPONSIBILITIES` | `{}` | JSON: `{"T1": "Triage 7a-3:30p, Admit", ...}` |
| `TRIAGE_STATIC_EMAILS` | `` | Comma-sep static recipient emails |
| `TRIAGE_SHIFT_RECIPIENTS` | `` | Comma-sep shift labels to auto-email (e.g. `T1,T2`) |
| `TRIAGE_NAME_EMAIL_MAP` | `{}` | JSON: `{"Full Name": "email@domain.com"}` |
| `TRIAGE_SEND_HOUR` | `6` | Hour (CT) to send report |
| `TRIAGE_SEND_MINUTE` | `30` | Minute to send report |
| `TRIAGE_HOSPITAL_NAME` | `BSW Hospital Medicine Dallas` | PDF header label |

Reuses: `LB_USERNAME`, `LB_PASSWORD`, `LOCAL_TIMEZONE`, `SMTP_*`

---

## GitHub Actions

New file: `.github/workflows/triage.yml`

- Separate workflow from `sync.yml` (different schedule, different purpose)
- Cron: `30 12 * * *` UTC = 6:30 AM CT (standard time); adjust for DST if needed
- `workflow_dispatch` for manual trigger
- No state commit needed (stateless)
- Uploads screenshots as artifact on failure

---

## New Dependencies

```
fpdf2>=2.7.0       # PDF generation
rapidfuzz>=3.0.0   # Fuzzy name matching for recipient lookup
```

---

## Implementation Order

1. `src/triage_scraper.py` — most complex; includes recon mode
2. `src/triage_pdf.py` — straightforward once data model is defined
3. `src/triage_notifier.py` — email + recipient resolution
4. `src/triage_main.py` — thin orchestrator
5. `.github/workflows/triage.yml` — GitHub Actions
6. `requirements.txt` + `.env.example` updates

---

## Known Unknowns / Recon Required

The Today's Schedule daily view DOM structure is **partially unknown**.
`triage_scraper.py` will include a `run_triage_recon()` function (headless=False)
that navigates to the view and dumps HTML + screenshots so selectors can be
confirmed or corrected.

Specific unknowns:
- Exact CSS selector for provider slot elements (bars in the Gantt)
- Exact CSS selector for the note icon within a slot
- Exact CSS selector for the date forward/back navigation arrows in daily view
- Whether `.DataRow` / `.leftCol[data-cy='leftCol']` apply to daily view or not

If initial selectors don't work, run:
```bash
python -m src.triage_main --recon
```
and inspect the dumped HTML in `screenshots/`.
