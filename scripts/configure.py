"""Interactive setup script for LBOpenShiftFinder.

Prompts for configuration values, validates them, and writes a .env file.
Optionally pushes secrets to GitHub Actions via the gh CLI.

Usage:
    python scripts/configure.py
"""

import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ENV_FILE = Path(__file__).parent.parent / ".env"


def _prompt(label: str, default: str = "", secret: bool = False, required: bool = True) -> str:
    """Prompt for a value, showing the label and optional default."""
    display = f"  {label} [{default}]: " if default else f"  {label}: "
    while True:
        value = getpass.getpass(display) if secret else input(display).strip()
        if not value:
            if default:
                return default
            if not required:
                return ""
            print(f"    {label} is required — please enter a value.")
            continue
        return value


def _validate_ical_url(url: str) -> bool:
    """Return True if the URL looks reachable. Warns but does not block on auth failures."""
    if not url.startswith("http"):
        print("    URL must start with http:// or https://")
        return False
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "LBOpenShiftFinder/1.0")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status < 400:
                return True
            print(f"    URL returned status {resp.status} — double-check it is correct.")
            return True  # Warn only; LB iCal URLs require auth so non-200 is expected
    except Exception as e:
        # Authentication-required URLs will fail here — that is expected
        print(f"    Could not connect: {e} (this is normal if the URL requires authentication)")
        return True


def _validate_service_account(raw: str) -> dict | None:
    """Parse and validate a service account JSON string. Returns the parsed dict or None."""
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"    Invalid JSON: {e}")
        return None

    required_fields = ["type", "project_id", "private_key", "client_email"]
    missing = [f for f in required_fields if f not in info]
    if missing:
        print(f"    Missing required fields: {', '.join(missing)}")
        return None

    if info.get("type") != "service_account":
        print(f"    Expected type 'service_account', got '{info.get('type')}'")
        return None

    return info


def _validate_timezone(tz: str) -> bool:
    """Return True if tz is a valid IANA timezone name."""
    try:
        ZoneInfo(tz)
        return True
    except ZoneInfoNotFoundError:
        print(f"    '{tz}' is not a valid IANA timezone name.")
        print("    See: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones")
        return False


def _validate_regex(pattern: str) -> bool:
    """Return True if pattern compiles as a valid regex."""
    try:
        re.compile(pattern, re.IGNORECASE)
        return True
    except re.error as e:
        print(f"    Invalid regex: {e}")
        return False


def _push_github_secrets(secrets: dict[str, str]) -> None:
    """Push secrets to GitHub Actions via the gh CLI."""
    print("\n  Pushing secrets to GitHub Actions...")
    failures = []
    for name, value in secrets.items():
        result = subprocess.run(
            ["gh", "secret", "set", name, "--body", value],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"    OK  {name}")
        else:
            print(f"    ERR {name}: {result.stderr.strip()}")
            failures.append(name)

    if failures:
        print(f"\n  Failed to push: {', '.join(failures)}")
        print("  Set them manually: Settings > Secrets and variables > Actions")
    else:
        print("\n  All secrets pushed successfully.")


def main() -> None:
    print("=" * 60)
    print("  LBOpenShiftFinder — Interactive Setup")
    print("=" * 60)
    print()

    if ENV_FILE.exists():
        print(f"  Existing .env found at {ENV_FILE}")
        answer = input("  Overwrite it? [y/N] ").strip().lower()
        if answer != "y":
            print("  Aborted.")
            sys.exit(0)
        print()

    config: dict[str, str] = {}

    # ---- Lightning Bolt credentials ----
    print("--- Lightning Bolt ---")
    config["LB_USERNAME"] = _prompt("Username (email)")
    config["LB_PASSWORD"] = _prompt("Password", secret=True)

    print()
    print("  Your iCal URL is in Lightning Bolt under schedule settings or export options.")
    print("  It looks like: https://lblite.lightning-bolt.com/ical/...")
    while True:
        url = _prompt("iCal URL")
        _validate_ical_url(url)
        config["LB_ICAL_URL"] = url
        break

    # ---- Google Calendar ----
    print()
    print("--- Google Calendar ---")
    print("  For GOOGLE_SERVICE_ACCOUNT_JSON, enter either:")
    print("    - A path to your downloaded JSON key file, e.g. ./gcp-service-account-key.json")
    print("    - The raw JSON contents pasted directly")

    while True:
        raw = _prompt("Service account (file path or raw JSON)")
        # Accept a file path
        candidate = Path(raw)
        if candidate.exists() and candidate.suffix == ".json":
            raw = candidate.read_text(encoding="utf-8")
        sa_info = _validate_service_account(raw)
        if sa_info:
            config["GOOGLE_SERVICE_ACCOUNT_JSON"] = json.dumps(sa_info)
            print(f"    Service account: {sa_info['client_email']}")
            break

    config["GOOGLE_CALENDAR_ID"] = _prompt("Calendar ID (e.g. you@gmail.com)")

    # ---- Optional settings ----
    print()
    print("--- Optional Settings (press Enter to keep the shown default) ---")

    while True:
        tz = _prompt("Timezone", default="America/Chicago")
        if _validate_timezone(tz):
            config["LOCAL_TIMEZONE"] = tz
            break

    config["LB_VIEW_NAME"] = _prompt(
        "LB view name (sidebar link text after login)",
        default="BSW Hospital Medicine - Dallas",
    )

    print()
    print("  MY_NAME_PATTERN is a case-insensitive regex matching your name in the schedule,")
    print("  used to detect shifts you've already picked up (e.g. '(john|jonathan)\\s+doe').")
    print("  Leave empty to disable picked-up shift detection.")
    while True:
        pattern = _prompt("Name regex", required=False)
        if not pattern:
            config["MY_NAME_PATTERN"] = ""
            break
        if _validate_regex(pattern):
            config["MY_NAME_PATTERN"] = pattern
            break

    # ---- Write .env ----
    print()
    print(f"Writing {ENV_FILE}...")
    lines = [
        "# Generated by scripts/configure.py — edit as needed\n",
        "\n",
        "# ---- Lightning Bolt ----\n",
        f"LB_USERNAME={config['LB_USERNAME']}\n",
        f"LB_PASSWORD={config['LB_PASSWORD']}\n",
        f"LB_ICAL_URL={config['LB_ICAL_URL']}\n",
        f"LB_VIEW_NAME={config['LB_VIEW_NAME']}\n",
        "\n",
        "# ---- Google Calendar ----\n",
        f"GOOGLE_SERVICE_ACCOUNT_JSON={config['GOOGLE_SERVICE_ACCOUNT_JSON']}\n",
        f"GOOGLE_CALENDAR_ID={config['GOOGLE_CALENDAR_ID']}\n",
        "\n",
        "# ---- Sync options (uncomment to override defaults) ----\n",
        f"LOCAL_TIMEZONE={config['LOCAL_TIMEZONE']}\n",
    ]

    if config["MY_NAME_PATTERN"]:
        lines.append(f"MY_NAME_PATTERN={config['MY_NAME_PATTERN']}\n")
    else:
        lines.append("# MY_NAME_PATTERN=\n")

    lines += [
        "# KEEP_PAST_SHIFTS=false\n",
        "# SYNC_OPEN_SHIFTS=true\n",
        "# SYNC_PICKED_SHIFTS=true\n",
        "# SYNC_SCHEDULED_SHIFTS=true\n",
        "# EXCLUDED_SHIFT_LABELS=\n",
        "# MIN_REST_HOURS=8\n",
        "# ICAL_LOOKAHEAD_DAYS=180\n",
        "\n",
        "# ---- Calendar colors (Google Calendar colorId 1–11) ----\n",
        "# 1=Lavender 2=Sage 3=Grape 4=Flamingo 5=Banana\n",
        "# 6=Tangerine 7=Peacock 8=Graphite 9=Blueberry 10=Basil 11=Tomato\n",
        "# OPEN_SHIFT_COLOR=2      # Sage (green)\n",
        "# PICKED_SHIFT_COLOR=9    # Blueberry (blue)\n",
        "# SCHEDULED_SHIFT_COLOR=3 # Grape (purple)\n",
    ]

    ENV_FILE.write_text("".join(lines), encoding="utf-8")
    print(f"  Written: {ENV_FILE}")

    # ---- Verify ----
    print()
    print("Verifying Google Calendar access...")
    verify_script = Path(__file__).parent / "verify_google_setup.py"
    result = subprocess.run(
        [sys.executable, str(verify_script)],
        cwd=ENV_FILE.parent,
        capture_output=True,
        text=True,
    )
    print(result.stdout.rstrip())
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr.rstrip())
        print()
        print("WARNING: Verification failed — check the errors above before running the full sync.")

    # ---- GitHub CLI push ----
    if shutil.which("gh"):
        print()
        answer = input("Push secrets to GitHub Actions via gh CLI? [y/N] ").strip().lower()
        if answer == "y":
            # Always push the 5 required secrets; push optional ones only if non-default
            gh_secrets: dict[str, str] = {
                "LB_USERNAME": config["LB_USERNAME"],
                "LB_PASSWORD": config["LB_PASSWORD"],
                "LB_ICAL_URL": config["LB_ICAL_URL"],
                "GOOGLE_SERVICE_ACCOUNT_JSON": config["GOOGLE_SERVICE_ACCOUNT_JSON"],
                "GOOGLE_CALENDAR_ID": config["GOOGLE_CALENDAR_ID"],
            }
            if config["MY_NAME_PATTERN"]:
                gh_secrets["MY_NAME_PATTERN"] = config["MY_NAME_PATTERN"]
            if config["LOCAL_TIMEZONE"] != "America/Chicago":
                gh_secrets["LOCAL_TIMEZONE"] = config["LOCAL_TIMEZONE"]
            if config["LB_VIEW_NAME"] != "BSW Hospital Medicine - Dallas":
                gh_secrets["LB_VIEW_NAME"] = config["LB_VIEW_NAME"]
            _push_github_secrets(gh_secrets)

    print()
    print("=" * 60)
    print("  Setup complete! Run the sync:")
    print("    python -m src.main")
    print("=" * 60)


if __name__ == "__main__":
    main()
