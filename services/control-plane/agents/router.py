"""
Router agent.

Receives a GatekeeperResult and:
  1. Skips duplicate events (gatekeeper status == "duplicate") without LLM call.
  2. Fetches the full event row from the DB.
  3. Calls Claude Haiku with a classify_event tool to produce bucket/priority/confidence.
  4. Updates the event row (bucket, priority, confidence, status="routed").
  5. Writes an action row and health_log for every outcome.

Bucket definitions:
  sales          — new leads, pricing inquiries, partnership probes, demo requests
  delivery       — active client work: project updates, deliverable requests, feedback
  support        — post-sale issues, complaints, support questions from known clients
  founder_review — anything ambiguous, sensitive, or requiring a judgment call
  noise          — spam, automated pings, OOO replies, newsletters, bot traffic

Priority definitions:
  high   — time-sensitive or revenue-impacting; act within hours
  medium — standard turnaround; act within one business day
  low    — informational or archival; no urgency

All Supabase calls are synchronous and wrapped in asyncio.to_thread.
LLM call uses anthropic.AsyncAnthropic — no thread wrapping needed.
"""

import asyncio
import logging
import time
from decimal import Decimal
from typing import Any, Literal

import anthropic
from pydantic import BaseModel

import db.actions as db_actions
import db.events as db_events
import db.health_logs as db_health_logs
from agents.gatekeeper import GatekeeperResult
from config import settings
from db.schemas import ActionCreate, EventUpdate, HealthLogCreate

logger = logging.getLogger(__name__)

# Patchable at module level — mocked in tests.
_anthropic = anthropic.AsyncAnthropic()

# Use Haiku for classification — fast and cheap for structured routing decisions.
_MODEL = "claude-haiku-4-5-20251001"

# Pricing for Haiku (adjust if Anthropic updates)
_COST_PER_INPUT_TOKEN = Decimal("0.8") / Decimal("1_000_000")   # $0.80 / 1M
_COST_PER_OUTPUT_TOKEN = Decimal("4") / Decimal("1_000_000")    # $4.00 / 1M

_CLASSIFICATION_TOOL: dict[str, Any] = {
    "name": "classify_event",
    "description": (
        "Classify an inbound business event into a routing bucket with priority and confidence. "
        "Return the single best-fit bucket even when multiple could apply."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "bucket": {
                "type": "string",
                "enum": ["sales", "delivery", "support", "founder_review", "noise"],
                "description": (
                    "sales: new leads, pricing, partnership, demo requests. "
                    "delivery: active client work, project updates, deliverable requests. "
                    "support: post-sale issues or complaints from known clients. "
                    "founder_review: ambiguous, sensitive, or requires human judgment. "
                    "noise: spam, OOO, newsletters, automated pings."
                ),
            },
            "priority": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": (
                    "high: time-sensitive or revenue-impacting — act within hours. "
                    "medium: standard — act within one business day. "
                    "low: informational or archival — no urgency."
                ),
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score 0.0–1.0 for this classification.",
            },
            "reasoning": {
                "type": "string",
                "description": "One-sentence explanation for the classification decision.",
            },
        },
        "required": ["bucket", "priority", "confidence", "reasoning"],
    },
}

_SYSTEM_PROMPT = """\
You are a routing classifier for a founder-led service business.
You receive inbound business events (emails, form submissions, messages) and classify them.

ICP context: the business serves founders and small agencies (2–15 people) with recurring
inbound work, client delivery, and coordination needs. Classify tightly within this context.

Classify each event into exactly one bucket:
- sales: prospecting, lead qualification, pricing asks, demo/consultation requests, partnership probes
- delivery: active client communication, project status, deliverable requests or reviews, kick-offs
- support: post-sale complaints, issues, change requests, or support questions from existing clients
- founder_review: anything ambiguous, sensitive, multi-topic, or requiring a judgment call
- noise: spam, OOO auto-replies, newsletters, list traffic, automated system notifications

Use the classify_event tool. Return only the tool call — no extra prose.\
"""


class RouterResult(BaseModel):
    event_id: str
    bucket: str | None = None
    priority: str | None = None
    confidence: float | None = None
    status: Literal["routed", "skipped", "error"]
    duration_ms: int


async def run(gk_result: GatekeeperResult) -> RouterResult:
    """
    Entry point. Routes a GatekeeperResult through classification.
    Logs unrecoverable errors to health_logs and returns a RouterResult with status="error".
    Never raises — callers always receive a result.
    """
    start = time.monotonic()

    # Skip duplicates — Gatekeeper already handled them.
    if gk_result.status == "duplicate" or gk_result.event_id is None:
        logger.info("Router: skipping duplicate event_id=%s", gk_result.event_id)
        return RouterResult(
            event_id=gk_result.event_id or "unknown",
            status="skipped",
            duration_ms=_elapsed_ms(start),
        )

    try:
        return await _classify(gk_result.event_id, start)
    except Exception as exc:
        duration_ms = _elapsed_ms(start)
        logger.error(
            "Router unrecoverable error event_id=%s: %s",
            gk_result.event_id, exc,
            exc_info=True,
        )
        try:
            await asyncio.to_thread(
                db_health_logs.create,
                HealthLogCreate(
                    service="router",
                    event_type="error",
                    message=f"unrecoverable error: {exc}",
                    metadata={"event_id": gk_result.event_id},
                ),
            )
        except Exception:
            logger.error("Router: failed to write error health_log", exc_info=True)
        return RouterResult(
            event_id=gk_result.event_id,
            status="error",
            duration_ms=duration_ms,
        )


async def _classify(event_id: str, start: float) -> RouterResult:
    # 1. Fetch full event row.
    event = await asyncio.to_thread(db_events.get_by_id, event_id)
    if event is None:
        raise ValueError(f"event not found: {event_id}")

    # 2. Build the user message from normalized event fields.
    user_msg = _build_user_message(event)

    # 3. Call Claude Haiku with tool_use to get structured classification.
    response = await _anthropic.messages.create(
        model=_MODEL,
        max_tokens=256,
        system=_SYSTEM_PROMPT,
        tools=[_CLASSIFICATION_TOOL],
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": user_msg}],
    )

    # 4. Parse classification from tool_use block.
    classification = _extract_classification(response)
    bucket = classification["bucket"]
    priority = classification["priority"]
    confidence = float(classification["confidence"])
    reasoning = classification.get("reasoning", "")

    # 5. Token cost accounting.
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    usd_cost = (
        Decimal(input_tokens) * _COST_PER_INPUT_TOKEN
        + Decimal(output_tokens) * _COST_PER_OUTPUT_TOKEN
    )

    duration_ms = _elapsed_ms(start)

    # 6. Update event row.
    await asyncio.to_thread(
        db_events.update,
        event_id,
        EventUpdate(bucket=bucket, priority=priority, confidence=confidence, status="routed"),
    )

    # 7. Write action row.
    await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="router",
            action_type="classify_event",
            event_id=event_id,
            duration_ms=duration_ms,
            token_input=input_tokens,
            token_output=output_tokens,
            usd_cost=usd_cost,
            payload={
                "bucket": bucket,
                "priority": priority,
                "confidence": confidence,
                "reasoning": reasoning,
                "model": _MODEL,
                "source": event.get("source"),
            },
        ),
    )

    # 8. Health log.
    await asyncio.to_thread(
        db_health_logs.create,
        HealthLogCreate(
            service="router",
            event_type="info",
            message="event routed",
            metadata={
                "event_id": event_id,
                "bucket": bucket,
                "priority": priority,
                "confidence": confidence,
                "duration_ms": duration_ms,
                "usd_cost": str(usd_cost),
            },
        ),
    )

    logger.info(
        "Router: event_id=%s bucket=%s priority=%s confidence=%.2f",
        event_id, bucket, priority, confidence,
    )

    return RouterResult(
        event_id=event_id,
        bucket=bucket,
        priority=priority,
        confidence=confidence,
        status="routed",
        duration_ms=duration_ms,
    )


def _build_user_message(event: dict) -> str:
    """Build a concise plain-text summary of the event for classification."""
    parts = [f"Source: {event.get('source', 'unknown')}"]
    if event.get("sender_name"):
        parts.append(f"Sender: {event['sender_name']}")
    if event.get("sender_email"):
        parts.append(f"Email: {event['sender_email']}")
    if event.get("subject"):
        parts.append(f"Subject: {event['subject']}")
    if event.get("body"):
        # Truncate body to keep token cost predictable.
        body = event["body"][:1500]
        parts.append(f"Body:\n{body}")
    return "\n".join(parts)


def _extract_classification(response: anthropic.types.Message) -> dict:
    """
    Pull the classify_event tool_use input from the response.
    Raises ValueError if no tool_use block is found (should not happen with tool_choice=any).
    """
    for block in response.content:
        if block.type == "tool_use" and block.name == "classify_event":
            return block.input
    raise ValueError(f"No classify_event tool_use in response: {response.content}")


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
