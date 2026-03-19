"""Build recipient list and send triage PDF via SMTP."""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage

from rapidfuzz import process as fuzz_process

from src.triage_scraper import TriageSchedule

logger = logging.getLogger(__name__)


def _format_subject_date(schedule_date: str) -> str:
    """Parse YYYY-MM-DD into 'Wednesday, March 19, 2026' format."""
    dt = datetime.strptime(schedule_date, "%Y-%m-%d")
    return dt.strftime("%A, %B %-d, %Y")


def build_recipient_list(
    schedule: TriageSchedule,
    static_emails: list[str],
    shift_recipients: list[str],
    name_email_map: dict[str, str],
    fuzzy_threshold: int = 85,
) -> list[str]:
    """Return deduplicated list of recipient email addresses.

    Args:
        schedule: The TriageSchedule containing all extracted shifts.
        static_emails: Always-included addresses (empty strings filtered out).
        shift_recipients: Shift labels whose providers are auto-resolved to emails
            (e.g. ["T1", "T2"]).
        name_email_map: Mapping of full provider name to email address.
        fuzzy_threshold: Minimum rapidfuzz score (0–100) for a name match.

    Returns:
        Deduplicated list of email addresses, preserving insertion order.
    """
    recipients: list[str] = []
    seen: set[str] = set()

    # Step 1: Add static emails (skip empty strings)
    for email in static_emails:
        email = email.strip()
        if email and email not in seen:
            recipients.append(email)
            seen.add(email)

    # Step 2: Resolve providers from matching shifts
    for label in shift_recipients:
        label_lower = label.lower()
        matching_shifts = [s for s in schedule.shifts if s.label.lower() == label_lower]

        for shift in matching_shifts:
            for provider_name in shift.providers:
                result = fuzz_process.extractOne(
                    provider_name,
                    name_email_map.keys(),
                    score_cutoff=fuzzy_threshold,
                )
                if result is not None:
                    email = name_email_map[result[0]]
                    if email not in seen:
                        recipients.append(email)
                        seen.add(email)
                else:
                    logger.warning(
                        f"No email match for provider '{provider_name}' (shift {label})"
                    )

    # Step 3: Warn if list is empty
    if not recipients:
        logger.warning("Recipient list is empty — triage email will not be sent")

    return recipients


def send_triage_email(
    pdf_bytes: bytes,
    recipients: list[str],
    schedule_date: str,
) -> None:
    """Send the triage PDF to all recipients via SMTP.

    No-op if recipients is empty or SMTP config is incomplete.

    Args:
        pdf_bytes: Raw PDF content to attach.
        recipients: List of recipient email addresses.
        schedule_date: YYYY-MM-DD string used for subject line and filename.
    """
    if not recipients:
        logger.warning("Recipient list is empty — skipping triage email")
        return

    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "")

    if not all([smtp_host, smtp_username, smtp_password]):
        logger.warning(
            "SMTP config is incomplete — requires SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD"
        )
        return

    try:
        msg = EmailMessage()
        msg["Subject"] = f"Triage Sheet — {_format_subject_date(schedule_date)}"
        msg["From"] = smtp_username
        msg["To"] = ", ".join(recipients)
        msg.set_content(
            "Please find today's triage sheet attached.\n\n"
            "This message was generated automatically."
        )
        msg.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename=f"triage_{schedule_date}.pdf",
        )

        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as smtp:
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(msg)

        logger.info(f"Triage email sent to {len(recipients)} recipient(s)")
    except Exception as e:
        logger.warning(f"Failed to send triage email: {e}")
