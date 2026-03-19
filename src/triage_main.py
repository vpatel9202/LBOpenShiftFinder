"""Triage report orchestrator: scrape schedule, generate PDF, send email."""

from __future__ import annotations

import argparse
import json
import logging
import os
import traceback
from datetime import datetime

from src.triage_scraper import scrape_triage_schedule, run_triage_recon
from src.triage_pdf import generate_triage_pdf
from src.triage_notifier import build_recipient_list, send_triage_email
from src.notifier import send_notification, WarningCollector

logger = logging.getLogger(__name__)


def _str_to_bool(value: str) -> bool:
    """Convert environment variable string to boolean."""
    return value.lower() in ("true", "1", "yes", "on")


def _run(args: argparse.Namespace) -> None:
    # Step 1: Check TRIAGE_ENABLED
    if not _str_to_bool(os.getenv("TRIAGE_ENABLED", "false")):
        logger.info("TRIAGE_ENABLED is false — skipping")
        return

    # Step 2: Validate required credentials
    username = os.getenv("LB_USERNAME", "").strip()
    password = os.getenv("LB_PASSWORD", "").strip()
    if not username:
        raise ValueError("LB_USERNAME is required but not set")
    if not password:
        raise ValueError("LB_PASSWORD is required but not set")

    # Step 3: Parse all config
    md_shifts: list[str] = [
        s.strip()
        for s in os.getenv("TRIAGE_MD_SHIFTS", "T1,T2,T3,A2,A3,A4,A5,A5-RRT").split(",")
        if s.strip()
    ]
    app_shifts: list[str] = [
        s.strip()
        for s in os.getenv("TRIAGE_APP_SHIFTS", "APP PA,APP A-1A,APP A-1B,APP A-2,APP A-3").split(",")
        if s.strip()
    ]
    app_schedule_name: str = os.getenv("TRIAGE_APP_SCHEDULE_NAME", "BSW Hospital Medicine Dallas APP")
    hospital_name: str = os.getenv("TRIAGE_HOSPITAL_NAME", "BSW Hospital Medicine Dallas")

    responsibilities_raw = os.getenv("TRIAGE_SHIFT_RESPONSIBILITIES", "{}")
    try:
        responsibilities: dict[str, str] = json.loads(responsibilities_raw)
    except json.JSONDecodeError:
        logger.warning(
            "TRIAGE_SHIFT_RESPONSIBILITIES is not valid JSON — using empty dict"
        )
        responsibilities = {}

    static_emails: list[str] = [
        s.strip()
        for s in os.getenv("TRIAGE_STATIC_EMAILS", "").split(",")
        if s.strip()
    ]
    shift_recipients: list[str] = [
        s.strip()
        for s in os.getenv("TRIAGE_SHIFT_RECIPIENTS", "").split(",")
        if s.strip()
    ]

    name_email_map_raw = os.getenv("TRIAGE_NAME_EMAIL_MAP", "{}")
    try:
        name_email_map: dict[str, str] = json.loads(name_email_map_raw)
    except json.JSONDecodeError:
        logger.warning(
            "TRIAGE_NAME_EMAIL_MAP is not valid JSON — using empty dict"
        )
        name_email_map = {}

    # Step 4: Recon mode — headed browser for DOM inspection; return early
    if args.recon:
        run_triage_recon(username, password)
        return

    # Step 5: Full pipeline
    # a. Scrape schedule
    schedule = scrape_triage_schedule(
        username,
        password,
        md_shifts,
        app_shifts,
        app_schedule_name,
    )

    # b. Generate PDF
    pdf_bytes = generate_triage_pdf(schedule, responsibilities, hospital_name)

    # c. Resolve recipients
    recipients = build_recipient_list(schedule, static_emails, shift_recipients, name_email_map)

    # d. Send email
    send_triage_email(pdf_bytes, recipients, schedule.date)

    # e. Log completion
    logger.info(f"Triage report complete for {schedule.date}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s]: %(message)s")

    parser = argparse.ArgumentParser(description="LB triage sheet generator")
    parser.add_argument("--recon", action="store_true", help="Run in recon mode (headed browser, DOM inspection)")
    args = parser.parse_args()

    collector = WarningCollector()
    logging.getLogger().addHandler(collector)

    try:
        _run(args)
    except Exception as exc:
        send_notification(
            "[LBOpenShiftFinder] Triage report FAILED",
            f"The triage run failed with an unhandled exception.\n\n"
            f"Error: {exc}\n\n"
            f"Traceback:\n{traceback.format_exc()}\n\n"
            f"Check your GitHub Actions logs for full details.",
        )
        raise
    else:
        if collector.messages:
            send_notification(
                "[LBOpenShiftFinder] Triage report completed with warnings",
                f"Triage report completed at {datetime.now().isoformat()} but logged "
                f"{len(collector.messages)} warning(s):\n\n"
                + "\n".join(f"  • {m}" for m in collector.messages)
                + "\n\nCheck your GitHub Actions logs for full details.",
            )
    finally:
        logging.getLogger().removeHandler(collector)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    main()
