"""
Database operations for the user_prefs table.
Synchronous — callers must wrap in asyncio.to_thread.
"""

import logging
from datetime import datetime, timezone

from db.client import get_client

logger = logging.getLogger(__name__)


def get_pref(phone_number: str, key: str) -> str | None:
    """Return the value for a preference key, or None if not set."""
    result = (
        get_client()
        .table("user_prefs")
        .select("value")
        .eq("phone_number", phone_number)
        .eq("key", key)
        .limit(1)
        .execute()
    )
    return result.data[0]["value"] if result.data else None


def set_pref(phone_number: str, key: str, value: str) -> dict:
    """Upsert a preference and return the row. Safe to call repeatedly."""
    result = (
        get_client()
        .table("user_prefs")
        .upsert(
            {
                "phone_number": phone_number,
                "key": key,
                "value": value,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="phone_number,key",
        )
        .execute()
    )
    return result.data[0]
