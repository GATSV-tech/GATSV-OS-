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
