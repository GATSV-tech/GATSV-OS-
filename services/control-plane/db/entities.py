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


def upsert_by_email(email: str | None, name: str | None) -> dict:
    """
    Find-or-create a contact entity.

    If email is provided and an entity with that email already exists, update
    last_seen_at and return the existing row. Otherwise insert a new entity.

    If email is None, always inserts a new entity (no dedup without an email).
    On a race-condition unique violation during insert, retries the select.
    """
    client = get_client()

    if email:
        result = client.table("entities").select("*").eq("email", email).limit(1).execute()
        if result.data:
            entity = result.data[0]
            client.table("entities").update(
                {"last_seen_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", entity["id"]).execute()
            return entity

    data = EntityCreate(type="contact", email=email, name=name)
    try:
        result = client.table("entities").insert(data.model_dump(mode="json")).execute()
        return result.data[0]
    except APIError as exc:
        if _is_unique_violation(exc) and email:
            # Race condition: another request inserted the same email between our
            # SELECT and INSERT. Fetch and return the winner's row.
            logger.info("entities.upsert_by_email: race dedup email=%s", email)
            result = client.table("entities").select("*").eq("email", email).limit(1).execute()
            if result.data:
                return result.data[0]
        raise


def _is_unique_violation(exc: APIError) -> bool:
    return getattr(exc, "code", None) == "23505" or "23505" in str(exc)
