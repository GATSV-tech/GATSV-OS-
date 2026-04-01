"""
Read-only queries used by the digest agent.
Synchronous — callers must wrap in asyncio.to_thread.
All datetime arguments must be timezone-aware UTC.
"""

import logging
from datetime import datetime

from db.client import get_client

logger = logging.getLogger(__name__)


def events_in_window(since: datetime, until: datetime) -> list[dict]:
    """
    Return all events created in [since, until).
    Callers count and group by source as needed.
    """
    result = (
        get_client()
        .table("events")
        .select("source, status, created_at")
        .gte("created_at", since.isoformat())
        .lt("created_at", until.isoformat())
        .execute()
    )
    return result.data


def scheduled_for_window(phone_number: str, since: datetime, until: datetime) -> list[dict]:
    """
    Return scheduled tasks for phone_number whose scheduled_at falls in [since, until).
    Includes all statuses so the digest shows what was scheduled even if already sent.
    """
    result = (
        get_client()
        .table("scheduled_tasks")
        .select("content, scheduled_at, status")
        .eq("sender_phone", phone_number)
        .gte("scheduled_at", since.isoformat())
        .lt("scheduled_at", until.isoformat())
        .order("scheduled_at", desc=False)
        .execute()
    )
    return result.data


def errors_since(since: datetime) -> list[dict]:
    """Return health_log error rows created at or after `since`."""
    result = (
        get_client()
        .table("health_logs")
        .select("service, message, created_at")
        .eq("event_type", "error")
        .gte("created_at", since.isoformat())
        .order("created_at", desc=False)
        .execute()
    )
    return result.data
