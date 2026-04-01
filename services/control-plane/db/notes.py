"""
Database operations for the notes table.
Synchronous — callers must wrap in asyncio.to_thread.
"""

import logging

from db.client import get_client

logger = logging.getLogger(__name__)


def create(phone_number: str, content: str) -> dict:
    """Insert a note and return the inserted row."""
    result = (
        get_client()
        .table("notes")
        .insert({"phone_number": phone_number, "content": content})
        .execute()
    )
    return result.data[0]
