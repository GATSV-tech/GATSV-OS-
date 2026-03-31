"""
Database operations for the health_logs table.
Synchronous — callers must wrap in asyncio.to_thread.
"""

import logging

from db.client import get_client
from db.schemas import HealthLogCreate

logger = logging.getLogger(__name__)


def create(data: HealthLogCreate) -> dict:
    """Insert a health_log row and return the full inserted row."""
    result = get_client().table("health_logs").insert(data.model_dump(mode="json")).execute()
    return result.data[0]
