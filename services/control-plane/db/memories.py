"""
Database operations for the memories table.
Synchronous — callers must wrap in asyncio.to_thread.

Memories are write-once; no update function is provided.
Embeddings are nullable here — a future async job populates them.
"""

import logging

from db.client import get_client
from db.schemas import MemoryCreate

logger = logging.getLogger(__name__)


def create(data: MemoryCreate) -> dict:
    """Insert a memory row and return the full inserted row."""
    result = get_client().table("memories").insert(data.model_dump(mode="json")).execute()
    return result.data[0]


def list_for_entity(entity_id: str, limit: int = 20) -> list[dict]:
    """
    Return the most recent memories for an entity, newest first.
    Used by agents that need entity context before acting.
    """
    result = (
        get_client()
        .table("memories")
        .select("id, entity_id, memory_type, content, source_event_id, created_at")
        .eq("entity_id", entity_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data
