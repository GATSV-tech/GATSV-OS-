"""
Slack interactions webhook.

Handles inbound POST requests from Slack Interactive Components (button clicks).
Slack sends a URL-encoded body with a single `payload` field containing JSON.

Request verification:
  Slack signs every request with HMAC-SHA256. The raw request body is used for
  verification, so it must be read BEFORE FastAPI parses the form data. The
  signature is checked using connectors.slack.verify_signature.

Response contract:
  Slack requires a 200 within 3 seconds. All heavy work is dispatched as
  asyncio background tasks so the route returns immediately.

Action routing:
  action_id format: "{approve|reject}_{approval_uuid}"
  Dispatched to agents.slack_surface.handle_approval_action.
"""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import connectors.slack as slack_connector
from agents import slack_surface

logger = logging.getLogger(__name__)

router = APIRouter(tags=["slack"])


@router.post("/slack/interactions", status_code=200)
async def slack_interactions(request: Request) -> dict:
    """
    Receive Slack interactive component payloads (block_actions).

    Verifies the request signature, parses the payload, and dispatches
    the action handler as a background task. Always returns 200 to Slack
    within the 3-second window.
    """
    raw_body = await request.body()

    # Signature verification — reject if invalid.
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not slack_connector.verify_signature(raw_body, timestamp, signature):
        logger.warning("slack_interactions: invalid signature — rejecting request")
        return JSONResponse(status_code=401, content={"error": "invalid signature"})

    # Parse URL-encoded form data — Slack sends payload=<url_encoded_json>.
    form = await request.form()
    payload_str = form.get("payload", "")
    if not payload_str:
        logger.warning("slack_interactions: missing payload field")
        return JSONResponse(status_code=400, content={"error": "missing payload"})

    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError as exc:
        logger.warning("slack_interactions: invalid JSON in payload: %s", exc)
        return JSONResponse(status_code=400, content={"error": "invalid payload JSON"})

    # Route by interaction type.
    interaction_type = payload.get("type")

    if interaction_type == "block_actions":
        actions = payload.get("actions", [])
        user_id = payload.get("user", {}).get("id", "unknown")

        for action in actions:
            action_id = action.get("action_id", "")
            approval_id = action.get("value", "")

            if action_id.startswith(("approve_", "reject_")) and approval_id:
                # Fire and forget — return 200 to Slack immediately.
                import asyncio
                asyncio.create_task(
                    slack_surface.handle_approval_action(
                        action_id=action_id,
                        approval_id=approval_id,
                        decided_by=user_id,
                    )
                )
            else:
                logger.info(
                    "slack_interactions: unhandled action_id=%s — ignoring",
                    action_id,
                )

    else:
        logger.info(
            "slack_interactions: unhandled interaction type=%s — ignoring",
            interaction_type,
        )

    return {"ok": True}
