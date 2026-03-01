#!/usr/bin/env python3
"""Send a test notification email to verify SMTP configuration.

Usage:
    python scripts/test_notify.py

Reads SMTP settings from the environment (or .env). Exits non-zero if
NOTIFY_ENABLED is false or SMTP config is incomplete so the GH Actions
step shows a clear failure rather than a silent no-op.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Guard: fail loudly instead of silently no-op-ing
if os.getenv("NOTIFY_ENABLED", "false").lower() not in ("true", "1", "yes", "on"):
    print("ERROR: NOTIFY_ENABLED is not set to true — nothing to test.", file=sys.stderr)
    sys.exit(1)

required = {
    "NOTIFY_EMAIL": os.getenv("NOTIFY_EMAIL", "").strip(),
    "SMTP_HOST": os.getenv("SMTP_HOST", "").strip(),
    "SMTP_USERNAME": os.getenv("SMTP_USERNAME", "").strip(),
    "SMTP_PASSWORD": os.getenv("SMTP_PASSWORD", ""),
}
missing = [k for k, v in required.items() if not v]
if missing:
    print(f"ERROR: Missing required SMTP variables: {', '.join(missing)}", file=sys.stderr)
    sys.exit(1)

from src.notifier import send_notification  # noqa: E402 — after env validation

now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
subject = f"[LBOpenShiftFinder] Test Notification — {now}"
body = (
    "This is a test notification from LBOpenShiftFinder.\n\n"
    "If you received this email, your SMTP configuration is working correctly.\n\n"
    f"Sent at: {now}\n"
    f"SMTP host: {os.getenv('SMTP_HOST')}:{os.getenv('SMTP_PORT', '587')}\n"
    f"Recipient: {os.getenv('NOTIFY_EMAIL')}\n"
)

send_notification(subject, body)
print(f"Test notification sent to {os.getenv('NOTIFY_EMAIL')}. Check your inbox.")
