"""
Database operations for the actions table.
Synchronous — callers must wrap in asyncio.to_thread.
"""

import logging
from typing import Any

from db.client import get_client
from db.schemas import ActionCreate

logger = logging.getLogger(__name__)


def create(data: ActionCreate) -> dict:
    """Insert an action row and return the full inserted row."""
    result = get_client().table("actions").insert(data.model_dump(mode="json")).execute()
    return result.data[0]


def update_status(
    action_id: str,
    status: str,
    approved_by: str | None = None,
) -> dict | None:
    """
    Update the status (and optionally approved_by) of an action row.
    Returns the updated row, or None if not found.
    Used by the Slack surface when a founder approves or rejects an action.
    """
    payload: dict = {"status": status}
    if approved_by is not None:
        payload["approved_by"] = approved_by
    result = (
        get_client()
        .table("actions")
        .update(payload)
        .eq("id", action_id)
        .execute()
    )
    return result.data[0] if result.data else None


def get_pending_send_acks() -> list[dict[str, Any]]:
    """
    Return all send_ack action rows that are awaiting email transport.

    Matches rows where:
      action_type = 'send_ack'
      payload->>'transport' = 'pending_connector'

    These are low-risk Operator decisions that have been committed to the DB
    but not yet delivered via email. The email_dispatcher picks these up,
    sends via Postmark, then calls mark_email_dispatched().
    """
    result = (
        get_client()
        .table("actions")
        .select("*")
        .eq("action_type", "send_ack")
        .filter("payload->>transport", "eq", "pending_connector")
        .execute()
    )
    return result.data or []


def mark_email_dispatched(action_id: str, success: bool, error: str | None = None) -> dict | None:
    """
    Update a send_ack action row after the email transport attempt.

    On success: sets payload.transport = 'email_sent'.
    On failure: sets payload.transport = 'email_failed' and records the error message.

    Returns the updated row, or None if not found.
    """
    import json

    # Fetch current row to merge payload (Supabase client doesn't support JSONB patch natively).
    rows = get_client().table("actions").select("payload").eq("id", action_id).execute()
    if not rows.data:
        logger.warning("mark_email_dispatched: action %s not found", action_id)
        return None

    current_payload: dict[str, Any] = rows.data[0].get("payload") or {}
    updated_payload = {**current_payload}

    if success:
        updated_payload["transport"] = "email_sent"
    else:
        updated_payload["transport"] = "email_failed"
        if error:
            updated_payload["transport_error"] = error

    result = (
        get_client()
        .table("actions")
        .update({"payload": updated_payload})
        .eq("id", action_id)
        .execute()
    )
    return result.data[0] if result.data else None
