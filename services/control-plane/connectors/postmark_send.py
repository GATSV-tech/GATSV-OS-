"""
Postmark outbound email connector.

Sends transactional email via the Postmark REST API using the server token.
Async — safe to await directly in async contexts.

Used by scheduler/email_dispatcher.py to deliver pending send_ack actions.
"""

import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

POSTMARK_API_BASE = "https://api.postmarkapp.com"


class PostmarkError(Exception):
    """Raised when the Postmark API returns a non-2xx response."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Postmark API error {status_code}: {body}")


async def send_email(
    to_email: str,
    subject: str,
    text_body: str,
    from_email: str | None = None,
) -> dict[str, Any]:
    """
    Send a transactional email via Postmark.

    Args:
        to_email: Recipient address.
        subject: Email subject line.
        text_body: Plain-text body content.
        from_email: Sender address. Defaults to POSTMARK_FROM_EMAIL if set,
                    otherwise falls back to the server's default sender signature.

    Returns:
        Parsed JSON response from Postmark (contains MessageID, etc.).

    Raises:
        RuntimeError: If POSTMARK_SERVER_TOKEN is not configured.
        PostmarkError: If Postmark returns a non-2xx response.
    """
    if not settings.postmark_server_token:
        raise RuntimeError(
            "POSTMARK_SERVER_TOKEN must be set to send email."
        )

    payload: dict[str, Any] = {
        "To": to_email,
        "Subject": subject,
        "TextBody": text_body,
    }

    sender = from_email or getattr(settings, "postmark_from_email", None)
    if sender:
        payload["From"] = sender

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{POSTMARK_API_BASE}/email",
            headers={
                "X-Postmark-Server-Token": settings.postmark_server_token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if not response.is_success:
        logger.error(
            "Postmark send failed: status=%s body=%s",
            response.status_code, response.text,
        )
        raise PostmarkError(response.status_code, response.text)

    data = response.json()
    logger.debug(
        "Postmark send ok: to=%s message_id=%s",
        to_email, data.get("MessageID"),
    )
    return data
