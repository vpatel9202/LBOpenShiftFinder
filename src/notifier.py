"""Email notifications for sync failures and warnings."""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def send_notification(subject: str, body: str) -> None:
    """Send an email notification via SMTP.

    No-ops silently if NOTIFY_ENABLED is false/unset or if any required
    SMTP config variable is missing.
    """
    if os.getenv("NOTIFY_ENABLED", "false").lower() not in ("true", "1", "yes", "on"):
        return

    notify_email = os.getenv("NOTIFY_EMAIL", "").strip()
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "")

    if not all([notify_email, smtp_host, smtp_username, smtp_password]):
        logger.warning(
            "NOTIFY_ENABLED=true but SMTP config is incomplete — "
            "requires NOTIFY_EMAIL, SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD"
        )
        return

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = smtp_username
        msg["To"] = notify_email
        msg.set_content(body)

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

        logger.info(f"Notification sent to {notify_email}: {subject}")
    except Exception as e:
        logger.warning(f"Failed to send notification email: {e}")


class WarningCollector(logging.Handler):
    """Log handler that captures WARNING+ messages for end-of-run reporting."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.setFormatter(logging.Formatter("%(levelname)s [%(name)s]: %(message)s"))
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))
