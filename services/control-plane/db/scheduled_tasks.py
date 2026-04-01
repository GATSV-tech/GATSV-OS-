"""
Database operations for the scheduled_tasks table.
Synchronous — callers must wrap in asyncio.to_thread.
"""

import logging
from datetime import datetime, timezone

from db.client import get_client
from db.schemas import ScheduledTaskCreate

logger = logging.getLogger(__name__)


def create(data: ScheduledTaskCreate) -> dict:
    """Insert a scheduled task and return the inserted row."""
    result = (
        get_client()
        .table("scheduled_tasks")
        .insert(data.model_dump(mode="json"))
        .execute()
    )
    return result.data[0]


def get_due(limit: int = 50) -> list[dict]:
    """
    Return pending tasks whose scheduled_at is at or before now (UTC).
    Results are ordered by scheduled_at ascending so earlier tasks fire first.
    limit caps the batch size per tick to prevent thundering-herd on startup
    after a long downtime.
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    result = (
        get_client()
        .table("scheduled_tasks")
        .select("id, sender_phone, content, scheduled_at")
        .eq("status", "pending")
        .lte("scheduled_at", now_utc)
        .order("scheduled_at", desc=False)
        .limit(limit)
        .execute()
    )
    return result.data


def list_pending(sender_phone: str) -> list[dict]:
    """
    Return all pending tasks for sender_phone ordered by scheduled_at ascending.
    Includes past-due pending tasks (scheduler may have been down).
    """
    result = (
        get_client()
        .table("scheduled_tasks")
        .select("id, content, scheduled_at")
        .eq("sender_phone", sender_phone)
        .eq("status", "pending")
        .order("scheduled_at", desc=False)
        .execute()
    )
    return result.data


def mark_status(task_id: str, status: str) -> None:
    """Update the status of a single task. Used by the scheduler after firing."""
    get_client().table("scheduled_tasks").update({"status": status}).eq("id", task_id).execute()
