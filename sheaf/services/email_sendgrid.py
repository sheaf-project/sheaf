"""SendGrid email backend using the v3 REST API via httpx."""

import logging

from sheaf.config import settings
from sheaf.services.email import EmailBackend

logger = logging.getLogger("sheaf.email.sendgrid")

_client = None


def _get_client():
    global _client
    if _client is None:
        import httpx

        _client = httpx.AsyncClient(
            base_url="https://api.sendgrid.com",
            headers={"Authorization": f"Bearer {settings.sendgrid_api_key}"},
            timeout=30.0,
        )
    return _client


class SendGridBackend(EmailBackend):
    async def send(
        self,
        to: str,
        subject: str,
        body_html: str,
        body_text: str,
    ) -> None:
        client = _get_client()
        resp = await client.post(
            "/v3/mail/send",
            json={
                "personalizations": [{"to": [{"email": to}]}],
                "from": {"email": settings.sendgrid_from},
                "subject": subject,
                "content": [
                    {"type": "text/plain", "value": body_text},
                    {"type": "text/html", "value": body_html},
                ],
            },
        )
        if resp.status_code not in (200, 201, 202):
            logger.error(
                "SendGrid API error: %d %s", resp.status_code, resp.text
            )
            raise RuntimeError(f"SendGrid send failed: {resp.status_code}")
        logger.info("Email sent via SendGrid to %s: %s", to, subject)
