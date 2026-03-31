"""
Database operations for the entities table.
Synchronous — callers must wrap in asyncio.to_thread.
"""

import logging
from datetime import datetime, timezone

from postgrest.exceptions import APIError

from db.client import get_client
from db.schemas import EntityCreate

logger = logging.getLogger(__name__)


def upsert_by_contact(email: str | None, phone: str | None, name: str | None) -> dict:
    """
    Find-or-create a contact entity. Match priority: email → phone → insert new.

    - Email present and found: update last_seen_at, return existing row.
    - Phone present and found (email absent or not found): same.
    - Neither: always insert a new entity.

    Race-condition handling: if a concurrent insert wins the unique constraint,
    retry the select and return the winner's row.
    """
    client = get_client()

    if email:
        result = client.table("entities").select("*").eq("email", email).limit(1).execute()
        if result.data:
            return _touch(client, result.data[0])

    if phone:
        result = client.table("entities").select("*").eq("phone", phone).limit(1).execute()
        if result.data:
            return _touch(client, result.data[0])

    data = EntityCreate(type="contact", email=email, phone=phone, name=name)
    try:
        result = client.table("entities").insert(data.model_dump(mode="json")).execute()
        return result.data[0]
    except APIError as exc:
        if _is_unique_violation(exc):
            # Race condition: another request inserted the same email or phone.
            # Retry the select and return the winner's row.
            logger.info("entities.upsert_by_contact: race dedup email=%s phone=%s", email, phone)
            if email:
                result = client.table("entities").select("*").eq("email", email).limit(1).execute()
                if result.data:
                    return result.data[0]
            if phone:
                result = client.table("entities").select("*").eq("phone", phone).limit(1).execute()
                if result.data:
                    return result.data[0]
        raise


def _touch(client, entity: dict) -> dict:
    """Update last_seen_at on an existing entity and return it."""
    client.table("entities").update(
        {"last_seen_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", entity["id"]).execute()
    return entity


def _is_unique_violation(exc: APIError) -> bool:
    return getattr(exc, "code", None) == "23505" or "23505" in str(exc)
