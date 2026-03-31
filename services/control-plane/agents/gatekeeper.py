"""
Gatekeeper agent.

Receives a ParsedInbound from a connector and:
  1. Deduplicates against existing events (select-first; unique constraint as safety net)
  2. Finds or creates the sender entity
  3. Inserts the normalized event row
  4. Writes an action row and health_log for every outcome

No LLM calls — pure normalization. token_input/token_output/usd_cost are always 0.
All Supabase calls are synchronous and are wrapped in asyncio.to_thread.
Unrecoverable errors are logged to health_logs and re-raised so the webhook caller
receives a 500 and retries.
"""

import asyncio
import logging
import time
from typing import Literal

from pydantic import BaseModel

import db.actions as db_actions
import db.entities as db_entities
import db.events as db_events
import db.health_logs as db_health_logs
from connectors.base import ParsedInbound
from db.schemas import ActionCreate, EventCreate, HealthLogCreate

logger = logging.getLogger(__name__)


class GatekeeperResult(BaseModel):
    event_id: str | None       # None when deduped via race-condition constraint
    entity_id: str | None
    status: Literal["created", "duplicate"]
    duration_ms: int


async def run(parsed: ParsedInbound) -> GatekeeperResult:
    """
    Entry point. Logs unrecoverable errors to health_logs, then re-raises.
    Returns GatekeeperResult on both success and duplicate paths.
    """
    start = time.monotonic()
    try:
        return await _normalize(parsed, start)
    except Exception as exc:
        logger.error(
            "Gatekeeper unrecoverable error source=%s source_id=%s: %s",
            parsed.source, parsed.source_id, exc,
            exc_info=True,
        )
        try:
            await asyncio.to_thread(
                db_health_logs.create,
                HealthLogCreate(
                    service="gatekeeper",
                    event_type="error",
                    message=f"unrecoverable error: {exc}",
                    metadata={"source": parsed.source, "source_id": parsed.source_id},
                ),
            )
        except Exception:
            logger.error("Failed to write error health_log", exc_info=True)
        raise


async def _normalize(parsed: ParsedInbound, start: float) -> GatekeeperResult:
    # 1. Dedup — skip entirely if source_id is absent
    if parsed.source_id:
        existing = await asyncio.to_thread(
            db_events.find_by_source, parsed.source, parsed.source_id
        )
        if existing:
            duration_ms = _elapsed_ms(start)
            logger.info(
                "Gatekeeper: duplicate event source=%s source_id=%s existing_id=%s",
                parsed.source, parsed.source_id, existing["id"],
            )
            await _write_action(
                action_type="deduplicate_event",
                event_id=existing["id"],
                duration_ms=duration_ms,
                payload={"reason": "select_dedup", "source": parsed.source, "source_id": parsed.source_id},
            )
            return GatekeeperResult(
                event_id=existing["id"],
                entity_id=existing.get("entity_id"),
                status="duplicate",
                duration_ms=duration_ms,
            )

    # 2. Upsert entity
    entity = await asyncio.to_thread(
        db_entities.upsert_by_contact, parsed.sender_email, parsed.sender_phone, parsed.sender_name
    )

    # 3. Create event — returns None on unique-constraint race
    event = await asyncio.to_thread(
        db_events.create,
        EventCreate(
            source=parsed.source,
            source_id=parsed.source_id,
            raw_payload=parsed.raw_payload,
            status="normalized",
            sender_name=parsed.sender_name,
            sender_email=parsed.sender_email,
            subject=parsed.subject,
            body=parsed.body,
            received_at=parsed.received_at,
            entity_id=entity["id"],
        ),
    )

    duration_ms = _elapsed_ms(start)

    if event is None:
        # Unique constraint caught a race-condition duplicate
        logger.info(
            "Gatekeeper: constraint dedup source=%s source_id=%s",
            parsed.source, parsed.source_id,
        )
        await _write_action(
            action_type="deduplicate_event",
            event_id=None,
            duration_ms=duration_ms,
            payload={"reason": "constraint_dedup", "source": parsed.source, "source_id": parsed.source_id},
        )
        return GatekeeperResult(
            event_id=None,
            entity_id=entity["id"],
            status="duplicate",
            duration_ms=duration_ms,
        )

    # 4. Audit trail
    await _write_action(
        action_type="normalize_event",
        event_id=event["id"],
        duration_ms=duration_ms,
        payload={"source": parsed.source, "source_id": parsed.source_id},
    )
    await asyncio.to_thread(
        db_health_logs.create,
        HealthLogCreate(
            service="gatekeeper",
            event_type="info",
            message="event normalized",
            metadata={
                "event_id": event["id"],
                "entity_id": entity["id"],
                "source": parsed.source,
                "source_id": parsed.source_id,
                "duration_ms": duration_ms,
            },
        ),
    )

    logger.info(
        "Gatekeeper: event created event_id=%s entity_id=%s source=%s",
        event["id"], entity["id"], parsed.source,
    )

    return GatekeeperResult(
        event_id=event["id"],
        entity_id=entity["id"],
        status="created",
        duration_ms=duration_ms,
    )


async def _write_action(
    *,
    action_type: str,
    event_id: str | None,
    duration_ms: int,
    payload: dict,
) -> None:
    await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="gatekeeper",
            action_type=action_type,
            event_id=event_id,
            duration_ms=duration_ms,
            payload=payload,
        ),
    )


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
