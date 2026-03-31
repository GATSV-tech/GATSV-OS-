"""
Sendblue outbound connector.

Sends iMessage/SMS/RCS via the Sendblue REST API and optionally registers a
status_callback URL so delivery state changes (SENT, DELIVERED, ERROR, etc.)
are POSTed back to /inbound/imessage/status.

Async — safe to await directly in FastAPI handlers.
"""

import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

SENDBLUE_API_BASE = "https://api.sendblue.co"


class SendblueError(Exception):
    """Raised when the Sendblue API returns a non-2xx response."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Sendblue API error {status_code}: {body}")


async def send_message(
    to_number: str,
    content: str,
    status_callback: str | None = None,
) -> dict[str, Any]:
    """
    Send an outbound iMessage/SMS/RCS via Sendblue.

    Args:
        to_number: Recipient in E.164 format.
        content: Message text.
        status_callback: Optional URL Sendblue will POST delivery updates to.
                         Construct via build_status_callback_url() if needed.

    Returns:
        Parsed JSON response from Sendblue (contains message_handle, status, etc.).

    Raises:
        RuntimeError: If SENDBLUE_API_KEY, SENDBLUE_API_SECRET, or
                      SENDBLUE_FROM_NUMBER are not configured.
        SendblueError: If Sendblue returns a non-2xx response.
    """
    if not settings.sendblue_api_key or not settings.sendblue_api_secret:
        raise RuntimeError(
            "SENDBLUE_API_KEY and SENDBLUE_API_SECRET must be set to send messages."
        )
    if not settings.sendblue_from_number:
        raise RuntimeError(
            "SENDBLUE_FROM_NUMBER must be set to send messages."
        )

    payload: dict[str, Any] = {
        "number": to_number,
        "from_number": settings.sendblue_from_number,
        "content": content,
    }
    if status_callback:
        payload["status_callback"] = status_callback

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{SENDBLUE_API_BASE}/api/send-message",
            headers={
                "sb-api-key-id": settings.sendblue_api_key,
                "sb-api-secret-key": settings.sendblue_api_secret,
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if not response.is_success:
        logger.error(
            "Sendblue send failed: status=%s body=%s",
            response.status_code, response.text,
        )
        raise SendblueError(response.status_code, response.text)

    logger.debug("Sendblue send ok: to=%s handle=%s", to_number, response.json().get("message_handle"))
    return response.json()


def build_status_callback_url() -> str | None:
    """
    Construct the delivery status callback URL for this service.
    Returns None if APP_BASE_URL or SENDBLUE_WEBHOOK_SECRET are not configured.
    """
    if not settings.app_base_url or not settings.sendblue_webhook_secret:
        return None
    base = settings.app_base_url.rstrip("/")
    return f"{base}/inbound/imessage/status?token={settings.sendblue_webhook_secret}"
