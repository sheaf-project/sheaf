"""AWS SES email backend using boto3."""

import logging
from functools import lru_cache

from sheaf.config import settings
from sheaf.services.email import EmailBackend

logger = logging.getLogger("sheaf.email.ses")


@lru_cache(maxsize=1)
def _get_ses_client():
    import boto3

    kwargs: dict = {"region_name": settings.ses_region}
    if settings.ses_access_key and settings.ses_secret_key:
        kwargs["aws_access_key_id"] = settings.ses_access_key
        kwargs["aws_secret_access_key"] = settings.ses_secret_key
    return boto3.client("ses", **kwargs)


class SESBackend(EmailBackend):
    async def send(
        self,
        to: str,
        subject: str,
        body_html: str,
        body_text: str,
    ) -> None:
        import asyncio

        client = _get_ses_client()
        try:
            await asyncio.to_thread(
                client.send_email,
                Source=settings.ses_from,
                Destination={"ToAddresses": [to]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": body_text, "Charset": "UTF-8"},
                        "Html": {"Data": body_html, "Charset": "UTF-8"},
                    },
                },
            )
            logger.info("Email sent via SES to %s: %s", to, subject)
        except Exception:
            logger.exception("Failed to send email via SES to %s: %s", to, subject)
            raise
