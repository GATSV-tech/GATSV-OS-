"""
Database operations for the approvals table.
Synchronous — callers must wrap in asyncio.to_thread.

Approvals are created by agents when an action requires founder sign-off.
Decisions are applied by the Slack surface (or any future approval interface).
The update_decision function is included here for completeness; it is not
called by the Operator — the approval interface owns that path.
"""

import logging

from db.client import get_client
from db.schemas import ApprovalCreate, ApprovalDecision

logger = logging.getLogger(__name__)


def get_by_id(approval_id: str) -> dict | None:
    """Return the full approval row for approval_id, or None if not found."""
    result = (
        get_client()
        .table("approvals")
        .select(
            "id, action_id, event_id, requested_by, summary, context, "
            "options, decision, decided_by, decided_at, notified_at, created_at"
        )
        .eq("id", approval_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def create(data: ApprovalCreate) -> dict:
    """Insert an approval request and return the full inserted row."""
    result = get_client().table("approvals").insert(data.model_dump(mode="json")).execute()
    return result.data[0]


def list_unnotified(limit: int = 50) -> list[dict]:
    """
    Return open approvals that have not yet been posted to Slack (notified_at IS NULL).
    Ordered oldest-first so the queue is worked in submission order.
    """
    result = (
        get_client()
        .table("approvals")
        .select(
            "id, action_id, event_id, requested_by, summary, context, "
            "options, expires_at, created_at"
        )
        .is_("decision", "null")
        .is_("notified_at", "null")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    return result.data


def mark_notified(approval_id: str) -> dict | None:
    """
    Set notified_at = now() on an approval row.
    Returns the updated row, or None if not found.
    """
    from datetime import datetime, timezone
    result = (
        get_client()
        .table("approvals")
        .update({"notified_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", approval_id)
        .execute()
    )
    return result.data[0] if result.data else None


def list_open(limit: int = 50) -> list[dict]:
    """
    Return pending approval requests (no decision yet), oldest first.
    Used by the approval interface to surface the queue.
    """
    result = (
        get_client()
        .table("approvals")
        .select(
            "id, action_id, event_id, requested_by, summary, context, "
            "options, expires_at, created_at"
        )
        .is_("decision", "null")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    return result.data


def update_decision(approval_id: str, decision: ApprovalDecision) -> dict | None:
    """
    Apply a founder decision to an approval row.
    Returns the updated row, or None if the approval was not found.
    """
    payload = decision.model_dump(mode="json")
    result = (
        get_client()
        .table("approvals")
        .update(payload)
        .eq("id", approval_id)
        .execute()
    )
    return result.data[0] if result.data else None
