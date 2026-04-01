"""
Operator agent.

Receives a RouterResult and executes safe automated actions for sales and
support events. All other buckets (delivery, founder_review, noise) are
skipped without any LLM call.

Action types (v1):
  create_entity_note — Write a summary note to the memories table for the entity.
                       Always low-risk. Always executes immediately.
  send_ack           — Draft an acknowledgment message for the sender.
                       Risk level is decided by Haiku per bucket:
                         sales   → low-risk  → action row written as "executed"
                                               (email transport is a future connector)
                         support → high-risk → action row written as "pending_approval"
                                               + approval row queued for founder review

Risk model:
  low  — executes immediately; action row status = "executed"
  high — queued for founder review; action row status = "pending_approval" +
         a corresponding approvals row with the full context

No external network calls are made by this agent. Email transport is out of scope
here; the Operator decides and logs. A future outbound connector will pick up
pending send_ack actions and send them. This keeps the Operator decoupled from
transport mechanics.

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
import db.approvals as db_approvals
import db.events as db_events
import db.health_logs as db_health_logs
import db.memories as db_memories
from agents.router import RouterResult
from db.schemas import ActionCreate, ApprovalCreate, EventUpdate, HealthLogCreate, MemoryCreate

logger = logging.getLogger(__name__)

# Patchable at module level — mocked in tests.
_anthropic = anthropic.AsyncAnthropic()

_MODEL = "claude-haiku-4-5-20251001"

_COST_PER_INPUT_TOKEN = Decimal("0.8") / Decimal("1_000_000")
_COST_PER_OUTPUT_TOKEN = Decimal("4") / Decimal("1_000_000")

# Buckets this agent acts on. All others are skipped.
_ACTIVE_BUCKETS = {"sales", "support"}

_PLAN_ACTIONS_TOOL: dict[str, Any] = {
    "name": "plan_actions",
    "description": (
        "Produce a list of actions to take on this inbound event. "
        "Only include actions that are clearly warranted. "
        "Return an empty list if no action is needed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action_type": {
                            "type": "string",
                            "enum": ["create_entity_note", "send_ack"],
                        },
                        "risk": {
                            "type": "string",
                            "enum": ["low", "high"],
                            "description": (
                                "low: safe to execute automatically. "
                                "high: requires founder review before execution."
                            ),
                        },
                        "note_content": {
                            "type": "string",
                            "description": (
                                "Required for create_entity_note. "
                                "2–4 sentence factual summary of this contact interaction. "
                                "Written in third person, present-tense facts only."
                            ),
                        },
                        "ack_subject": {
                            "type": "string",
                            "description": "Required for send_ack. Email subject line.",
                        },
                        "ack_body": {
                            "type": "string",
                            "description": (
                                "Required for send_ack. "
                                "Short, professional acknowledgment (3–5 sentences). "
                                "No commitments or pricing. Warm but neutral tone."
                            ),
                        },
                        "reason": {
                            "type": "string",
                            "description": "One sentence explaining why this action is warranted.",
                        },
                    },
                    "required": ["action_type", "risk", "reason"],
                },
            }
        },
        "required": ["actions"],
    },
}

_SYSTEM_PROMPT = """\
You are an operations assistant for a founder-led service business.
You receive inbound events that have already been classified by a router.
Your job: produce a minimal action plan. Only include actions that are clearly warranted.

Rules:
- create_entity_note: always low-risk. Write a factual 2–4 sentence note about this contact
  and their intent. Use only information present in the event — do not speculate.
- send_ack (sales): low-risk. Draft a brief, warm acknowledgment. No pricing, no commitments.
- send_ack (support): high-risk. Draft a careful acknowledgment. Mark as requiring founder review.
- Do not plan send_ack if there is no sender email to respond to.
- Do not over-plan. Fewer targeted actions > many vague ones.

Use the plan_actions tool. Return only the tool call — no extra prose.\
"""


class OperatorResult(BaseModel):
    event_id: str
    actions_executed: list[str] = []   # action_types executed immediately
    actions_queued: list[str] = []     # action_types queued for approval
    status: Literal["actioned", "skipped", "error"]
    duration_ms: int


async def run(rt_result: RouterResult) -> OperatorResult:
    """
    Entry point. Errors are caught, logged to health_logs, and returned as
    status="error" — never raises. Callers always receive an OperatorResult.
    """
    start = time.monotonic()

    if rt_result.status != "routed" or rt_result.bucket not in _ACTIVE_BUCKETS:
        logger.info(
            "Operator: skipping event_id=%s status=%s bucket=%s",
            rt_result.event_id, rt_result.status, rt_result.bucket,
        )
        return OperatorResult(
            event_id=rt_result.event_id,
            status="skipped",
            duration_ms=_elapsed_ms(start),
        )

    try:
        return await _act(rt_result, start)
    except Exception as exc:
        duration_ms = _elapsed_ms(start)
        logger.error(
            "Operator unrecoverable error event_id=%s: %s",
            rt_result.event_id, exc,
            exc_info=True,
        )
        try:
            await asyncio.to_thread(
                db_health_logs.create,
                HealthLogCreate(
                    service="operator",
                    event_type="error",
                    message=f"unrecoverable error: {exc}",
                    metadata={"event_id": rt_result.event_id},
                ),
            )
        except Exception:
            logger.error("Operator: failed to write error health_log", exc_info=True)
        return OperatorResult(
            event_id=rt_result.event_id,
            status="error",
            duration_ms=duration_ms,
        )


async def _act(rt_result: RouterResult, start: float) -> OperatorResult:
    event = await asyncio.to_thread(db_events.get_by_id, rt_result.event_id)
    if event is None:
        raise ValueError(f"event not found: {rt_result.event_id}")

    # Call Haiku to produce the action plan.
    user_msg = _build_user_message(event, rt_result)
    response = await _anthropic.messages.create(
        model=_MODEL,
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        tools=[_PLAN_ACTIONS_TOOL],
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": user_msg}],
    )

    plan = _extract_plan(response)
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    usd_cost = (
        Decimal(input_tokens) * _COST_PER_INPUT_TOKEN
        + Decimal(output_tokens) * _COST_PER_OUTPUT_TOKEN
    )

    actions_executed: list[str] = []
    actions_queued: list[str] = []

    for item in plan:
        action_type = item["action_type"]
        risk = item.get("risk", "high")

        if action_type == "create_entity_note":
            executed = await _execute_create_entity_note(item, event)
            if executed:
                actions_executed.append(action_type)

        elif action_type == "send_ack":
            if risk == "low":
                executed = await _execute_send_ack(item, event, rt_result)
                if executed:
                    actions_executed.append(action_type)
            else:
                queued = await _queue_send_ack_for_approval(item, event, rt_result)
                if queued:
                    actions_queued.append(action_type)

    duration_ms = _elapsed_ms(start)

    # Write the planner action row (covers the Haiku call itself).
    await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="operator",
            action_type="plan_actions",
            event_id=rt_result.event_id,
            duration_ms=duration_ms,
            token_input=input_tokens,
            token_output=output_tokens,
            usd_cost=usd_cost,
            payload={
                "bucket": rt_result.bucket,
                "priority": rt_result.priority,
                "plan": plan,
                "actions_executed": actions_executed,
                "actions_queued": actions_queued,
                "model": _MODEL,
            },
        ),
    )

    # Update event to actioned.
    await asyncio.to_thread(
        db_events.update,
        rt_result.event_id,
        EventUpdate(status="actioned"),
    )

    await asyncio.to_thread(
        db_health_logs.create,
        HealthLogCreate(
            service="operator",
            event_type="info",
            message="operator actioned event",
            metadata={
                "event_id": rt_result.event_id,
                "bucket": rt_result.bucket,
                "actions_executed": actions_executed,
                "actions_queued": actions_queued,
                "duration_ms": duration_ms,
                "usd_cost": str(usd_cost),
            },
        ),
    )

    logger.info(
        "Operator: event_id=%s bucket=%s executed=%s queued=%s",
        rt_result.event_id, rt_result.bucket, actions_executed, actions_queued,
    )

    return OperatorResult(
        event_id=rt_result.event_id,
        actions_executed=actions_executed,
        actions_queued=actions_queued,
        status="actioned",
        duration_ms=duration_ms,
    )


async def _execute_create_entity_note(item: dict, event: dict) -> bool:
    """
    Write a memory note for the entity.
    Returns True if the note was written, False if silently skipped.
    """
    entity_id = event.get("entity_id")
    if not entity_id:
        logger.warning(
            "Operator: create_entity_note skipped — no entity_id on event_id=%s",
            event["id"],
        )
        return False

    content = item.get("note_content", "").strip()
    if not content:
        logger.warning(
            "Operator: create_entity_note skipped — empty note_content event_id=%s",
            event["id"],
        )
        return False

    await asyncio.to_thread(
        db_memories.create,
        MemoryCreate(
            entity_id=entity_id,
            memory_type="note",
            content=content,
            source_event_id=event["id"],
        ),
    )

    # Action row for this individual execution.
    await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="operator",
            action_type="create_entity_note",
            event_id=event["id"],
            payload={
                "entity_id": entity_id,
                "note_preview": content[:120],
                "reason": item.get("reason", ""),
            },
        ),
    )
    return True


async def _execute_send_ack(item: dict, event: dict, rt_result: RouterResult) -> bool:
    """
    Log the drafted ack as an executed action.
    Actual email transport is handled by a future outbound connector that reads
    these action rows. The Operator decides and drafts — not delivers.
    Returns True if the action row was written, False if silently skipped.
    """
    sender_email = event.get("sender_email")
    if not sender_email:
        logger.info(
            "Operator: send_ack skipped — no sender_email on event_id=%s",
            event["id"],
        )
        return False

    await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="operator",
            action_type="send_ack",
            event_id=event["id"],
            status="executed",
            payload={
                "to_email": sender_email,
                "subject": item.get("ack_subject", ""),
                "body": item.get("ack_body", ""),
                "bucket": rt_result.bucket,
                "reason": item.get("reason", ""),
                "transport": "pending_connector",  # signals the outbound connector
            },
        ),
    )
    return True


async def _queue_send_ack_for_approval(
    item: dict, event: dict, rt_result: RouterResult
) -> bool:
    """
    Write a pending_approval action row + approval record for founder review.
    The approval context is self-contained — no additional lookups needed.
    Returns True if the approval was queued, False if silently skipped.
    """
    sender_email = event.get("sender_email")
    if not sender_email:
        logger.info(
            "Operator: send_ack (approval) skipped — no sender_email on event_id=%s",
            event["id"],
        )
        return False

    action = await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="operator",
            action_type="send_ack",
            event_id=event["id"],
            status="pending_approval",
            requires_approval=True,
            payload={
                "to_email": sender_email,
                "subject": item.get("ack_subject", ""),
                "body": item.get("ack_body", ""),
                "bucket": rt_result.bucket,
                "reason": item.get("reason", ""),
            },
        ),
    )

    summary = (
        f"Send acknowledgment to {sender_email} "
        f"(bucket: {rt_result.bucket}, priority: {rt_result.priority}). "
        f"{item.get('reason', '')}"
    )

    await asyncio.to_thread(
        db_approvals.create,
        ApprovalCreate(
            action_id=action["id"],
            event_id=event["id"],
            requested_by="operator",
            summary=summary,
            context={
                "to_email": sender_email,
                "subject": item.get("ack_subject", ""),
                "body": item.get("ack_body", ""),
                "sender_name": event.get("sender_name"),
                "event_subject": event.get("subject"),
                "bucket": rt_result.bucket,
                "priority": rt_result.priority,
            },
        ),
    )
    return True


def _build_user_message(event: dict, rt_result: RouterResult) -> str:
    parts = [
        f"Bucket: {rt_result.bucket}",
        f"Priority: {rt_result.priority}",
        f"Source: {event.get('source', 'unknown')}",
    ]
    if event.get("sender_name"):
        parts.append(f"Sender name: {event['sender_name']}")
    if event.get("sender_email"):
        parts.append(f"Sender email: {event['sender_email']}")
    if event.get("subject"):
        parts.append(f"Subject: {event['subject']}")
    if event.get("body"):
        parts.append(f"Body:\n{event['body'][:1500]}")
    return "\n".join(parts)


def _extract_plan(response: anthropic.types.Message) -> list[dict]:
    """Extract the actions list from the plan_actions tool_use block."""
    for block in response.content:
        if block.type == "tool_use" and block.name == "plan_actions":
            return block.input.get("actions", [])
    raise ValueError(f"No plan_actions tool_use in response: {response.content}")


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
