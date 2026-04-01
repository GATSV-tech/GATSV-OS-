"""
Database operations for the chat_messages table.
Stores iMessage bot conversation turns for rolling context window.
Synchronous — callers must wrap in asyncio.to_thread.
"""

import logging

from db.client import get_client

logger = logging.getLogger(__name__)


def append(sender_phone: str, role: str, content: str) -> dict:
    """
    Insert a single conversation turn and return the inserted row.
    role must be 'user' or 'assistant' — enforced by DB check constraint.
    """
    result = (
        get_client()
        .table("chat_messages")
        .insert({"sender_phone": sender_phone, "role": role, "content": content})
        .execute()
    )
    return result.data[0]


def get_recent(sender_phone: str, limit: int = 20) -> list[dict]:
    """
    Return the most recent `limit` messages for sender_phone in
    chronological order (oldest first).

    Fetches newest-first (index-friendly), then reverses so callers
    receive a properly ordered messages list ready to pass to Claude.
    """
    result = (
        get_client()
        .table("chat_messages")
        .select("role, content, created_at")
        .eq("sender_phone", sender_phone)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    # Reverse to chronological order before returning
    return list(reversed(result.data))
