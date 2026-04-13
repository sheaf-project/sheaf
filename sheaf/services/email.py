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
        elif settings.email_backend == "sendgrid":
            try:
                from sheaf.services.email_sendgrid import SendGridBackend
            except ImportError:
                raise RuntimeError(
                    "EMAIL_BACKEND=sendgrid requires the 'sendgrid' extra. "
                    "Install with: pip install sheaf[sendgrid]  "
                    "(Docker: add 'sendgrid' to the pip install extras in Dockerfile)"
                ) from None

            _backend = SendGridBackend()
            logger.info("Using SendGrid email backend")
        else:
            _backend = NoneBackend()
            logger.info("Email disabled (EMAIL_BACKEND=none)")
    return _backend


async def send_email(
    to: str,
    subject: str,
    body_html: str,
    body_text: str,
    *,
    force: bool = False,
) -> None:
    """Send a transactional email via the configured backend.

    If the recipient has a non-OK email_delivery_status (bounced or
    complained), the send is skipped. Pass force=True for revalidation
    flows that must reach the user regardless of current state.
    """
    if not force and await _is_blocked_recipient(to):
        logger.info("Skipping send to %s (blocked by delivery status): %s", to, subject)
        return

    backend = get_email_backend()
    await backend.send(to, subject, body_html, body_text)


async def _is_blocked_recipient(email: str) -> bool:
    """Return True if this recipient is flagged as hard-bounced or complained."""
    from sqlalchemy import select

    from sheaf.crypto import blind_index
    from sheaf.database import async_session_factory
    from sheaf.models.user import EmailDeliveryStatus, User

    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(User.email_delivery_status).where(
                    User.email_hash == blind_index(email)
                )
            )
            status = result.scalar_one_or_none()
    except Exception:
        # Don't block sends on a DB hiccup during gate check — fail open.
        logger.exception("Delivery gate check failed; allowing send")
        return False

    if status is None:
        return False
    return status != EmailDeliveryStatus.OK
