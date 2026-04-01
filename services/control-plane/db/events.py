"""
Database operations for the events table.
Synchronous — callers must wrap in asyncio.to_thread.
"""

import logging

from postgrest.exceptions import APIError

from db.client import get_client
from db.schemas import EventCreate, EventUpdate

logger = logging.getLogger(__name__)


def find_by_source(source: str, source_id: str) -> dict | None:
    """
    Return the event row matching (source, source_id), or None if not found.
    Uses the dedup index — fast path for Gatekeeper dedup check.
    """
    result = (
        get_client()
        .table("events")
        .select("id, entity_id")
        .eq("source", source)
        .eq("source_id", source_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def create(data: EventCreate) -> dict | None:
    """
    Insert a new event row. Returns the full inserted row.
    Returns None if the insert hits the unique constraint (race-condition duplicate).
    Raises on all other errors.
    """
    try:
        result = get_client().table("events").insert(data.model_dump(mode="json")).execute()
        return result.data[0]
    except APIError as exc:
        if _is_unique_violation(exc):
            logger.info("events.create: unique constraint hit source=%s source_id=%s", data.source, data.source_id)
            return None
        raise


def get_by_id(event_id: str) -> dict | None:
    """Return the full event row for event_id, or None if not found."""
    result = (
        get_client()
        .table("events")
        .select(
            "id, source, source_id, status, bucket, priority, confidence, "
            "sender_name, sender_email, subject, body, entity_id, received_at, "
            "created_at, updated_at"
        )
        .eq("id", event_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def update(event_id: str, data: EventUpdate) -> dict | None:
    """
    Apply a partial update to an event row.
    Returns the updated row, or None if the event was not found.
    Only fields explicitly set in EventUpdate are sent to Supabase.
    """
    payload = data.model_dump(mode="json", exclude_none=True)
    if not payload:
        return get_by_id(event_id)
    result = (
        get_client()
        .table("events")
        .update(payload)
        .eq("id", event_id)
        .execute()
    )
    return result.data[0] if result.data else None


def _is_unique_violation(exc: APIError) -> bool:
    return getattr(exc, "code", None) == "23505" or "23505" in str(exc)
