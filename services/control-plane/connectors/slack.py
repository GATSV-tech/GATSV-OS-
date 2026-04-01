"""
Slack Web API connector.

Thin synchronous wrapper using httpx (already a project dependency).
Callers must wrap in asyncio.to_thread.

Covers two responsibilities:
  1. Sending messages (chat.postMessage with optional Block Kit blocks).
  2. Verifying inbound request signatures from Slack Interactive Components.

If SLACK_BOT_TOKEN / SLACK_SIGNING_SECRET are not configured, post_message
raises RuntimeError and verify_signature returns True (dev/test passthrough).
"""

import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

_API = "https://slack.com/api"
_TIMEOUT_S = 10


def post_message(
    channel: str,
    text: str,
    blocks: list[dict] | None = None,
) -> dict:
    """
    POST /chat.postMessage.
    Returns the Slack API response dict on success.
    Raises RuntimeError if Slack returns ok=false.
    Raises httpx.HTTPError on network/transport failures.
    """
    if not settings.slack_bot_token:
        raise RuntimeError("SLACK_BOT_TOKEN is not configured")

    payload: dict[str, Any] = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks

    resp = httpx.post(
        f"{_API}/chat.postMessage",
        json=payload,
        headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
        timeout=_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
    return data


def verify_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """
    Verify a Slack request signature using HMAC-SHA256.

    Slack signs every request with:
      v0=HMAC-SHA256(signing_secret, "v0:<timestamp>:<raw_body>")

    Returns True if valid, False otherwise.
    If SLACK_SIGNING_SECRET is not set, returns True (dev passthrough).
    Rejects requests older than 5 minutes to prevent replay attacks.
    """
    if not settings.slack_signing_secret:
        return True  # dev/test passthrough — no secret configured

    try:
        age_seconds = abs(time.time() - float(timestamp))
    except (ValueError, TypeError):
        return False

    if age_seconds > 300:
        logger.warning("slack: rejected stale request (age=%.0fs)", age_seconds)
        return False

    base = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected_mac = hmac.new(
        key=settings.slack_signing_secret.encode(),
        msg=base.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    expected = f"v0={expected_mac}"
    return hmac.compare_digest(expected, signature)
