"""Email sending abstraction with SMTP and SES backends."""

import abc
import logging

logger = logging.getLogger("sheaf.email")

_backend: "EmailBackend | None" = None


class EmailBackend(abc.ABC):
    @abc.abstractmethod
    async def send(
        self,
        to: str,
        subject: str,
        body_html: str,
        body_text: str,
    ) -> None:
        """Send a single transactional email."""


class NoneBackend(EmailBackend):
    """No-op backend. Logs a warning when email would have been sent."""

    async def send(self, to: str, subject: str, body_html: str, body_text: str) -> None:
        logger.warning("Email not sent (EMAIL_BACKEND=none): to=%s subject=%s", to, subject)


def get_email_backend() -> EmailBackend:
    global _backend
    if _backend is None:
        from sheaf.config import settings

        if settings.email_backend == "smtp":
            try:
                from sheaf.services.email_smtp import SMTPBackend
            except ImportError:
                raise RuntimeError(
                    "EMAIL_BACKEND=smtp requires the 'smtp' extra. "
                    "Install with: pip install sheaf[smtp]  "
                    "(Docker: add 'smtp' to the pip install extras in Dockerfile)"
                ) from None

            _backend = SMTPBackend()
            logger.info("Using SMTP email backend (%s:%d)", settings.smtp_host, settings.smtp_port)
        elif settings.email_backend == "ses":
            try:
                from sheaf.services.email_ses import SESBackend
            except ImportError:
                raise RuntimeError(
                    "EMAIL_BACKEND=ses requires the 'ses' extra. "
                    "Install with: pip install sheaf[ses]  "
                    "(Docker: add 'ses' to the pip install extras in Dockerfile)"
                ) from None

            _backend = SESBackend()
            logger.info("Using SES email backend (region: %s)", settings.ses_region)
        else:
            _backend = NoneBackend()
            logger.info("Email disabled (EMAIL_BACKEND=none)")
    return _backend


async def send_email(
    to: str,
    subject: str,
    body_html: str,
    body_text: str,
) -> None:
    """Send a transactional email via the configured backend."""
    backend = get_email_backend()
    await backend.send(to, subject, body_html, body_text)
