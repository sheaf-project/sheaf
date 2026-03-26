"""SMTP email backend using aiosmtplib."""

import logging
from email.message import EmailMessage

import aiosmtplib

from sheaf.config import settings
from sheaf.services.email import EmailBackend

logger = logging.getLogger("sheaf.email.smtp")


class SMTPBackend(EmailBackend):
    async def send(
        self,
        to: str,
        subject: str,
        body_html: str,
        body_text: str,
    ) -> None:
        msg = EmailMessage()
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body_text)
        msg.add_alternative(body_html, subtype="html")

        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user or None,
                password=settings.smtp_password or None,
                use_tls=settings.smtp_tls and settings.smtp_port == 465,
                start_tls=settings.smtp_tls and settings.smtp_port != 465,
            )
            logger.info("Email sent to %s: %s", to, subject)
        except Exception:
            logger.exception("Failed to send email to %s: %s", to, subject)
            raise
