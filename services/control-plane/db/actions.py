"""
Database operations for the actions table.
Synchronous — callers must wrap in asyncio.to_thread.
"""

import logging

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
