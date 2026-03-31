"""
Chat agent — the personal iMessage Claude bot reply loop.

Receives a ParsedInbound from an inbound iMessage, calls Claude, and sends
the reply via Sendblue. Single-turn for Slice 7; conversation memory is
wired in Slice 8.

Error policy: Claude API failures and Sendblue send failures are logged to
health_logs and return None. The webhook always returns 202 — Sendblue will
not retry and the user will not receive a duplicate message. Errors are
observable, not silent.
"""

import asyncio
import logging
import time
from decimal import Decimal

import anthropic
from pydantic import BaseModel

import db.actions as db_actions
import db.health_logs as db_health_logs
from connectors.base import ParsedInbound
from connectors.sendblue_send import SendblueError, build_status_callback_url, send_message
from db.schemas import ActionCreate, HealthLogCreate

logger = logging.getLogger(__name__)

# Patchable at the module level — mocked in tests.
_anthropic = anthropic.AsyncAnthropic()

SYSTEM_PROMPT = (
    "You are Jake's personal assistant, reachable via iMessage. "
    "Be direct and concise. "
    "Respond in plain text — no markdown, no bullet points unless asked."
)

# Claude Sonnet 4.6 pricing
_COST_PER_INPUT_TOKEN = Decimal("3") / Decimal("1000000")    # $3 / 1M tokens
_COST_PER_OUTPUT_TOKEN = Decimal("15") / Decimal("1000000")  # $15 / 1M tokens


class ChatResult(BaseModel):
    reply: str
    token_input: int
    token_output: int
    usd_cost: Decimal
    duration_ms: int


async def run(parsed: ParsedInbound) -> ChatResult | None:
    """
    Call Claude with the inbound message and send the reply via Sendblue.
    Returns ChatResult on success, None on any failure (errors are logged).
    Callers must guard: only call when parsed.body is non-empty.
    """
    if not parsed.body:
        return None

    start = time.monotonic()
    try:
        return await _reply(parsed, start)
    except Exception as exc:
        duration_ms = _elapsed_ms(start)
        logger.error("chat.run error source_id=%s: %s", parsed.source_id, exc, exc_info=True)
        try:
            await asyncio.to_thread(
                db_health_logs.create,
                HealthLogCreate(
                    service="chat",
                    event_type="error",
                    message=f"reply failed: {exc}",
                    metadata={
                        "source_id": parsed.source_id,
                        "sender_phone": parsed.sender_phone,
                        "duration_ms": duration_ms,
                    },
                ),
            )
        except Exception:
            logger.error("Failed to write chat error health_log", exc_info=True)
        return None


async def _reply(parsed: ParsedInbound, start: float) -> ChatResult:
    # 1. Call Claude
    response = await _anthropic.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": parsed.body}],
    )

    reply = response.content[0].text
    token_input = response.usage.input_tokens
    token_output = response.usage.output_tokens
    usd_cost = (
        Decimal(token_input) * _COST_PER_INPUT_TOKEN
        + Decimal(token_output) * _COST_PER_OUTPUT_TOKEN
    )

    # 2. Send reply via Sendblue
    await send_message(
        to_number=parsed.sender_phone,
        content=reply,
        status_callback=build_status_callback_url(),
    )

    duration_ms = _elapsed_ms(start)

    # 3. Write action row for observability
    await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="chat",
            action_type="send_reply",
            event_id=None,   # wired to event_id in Slice 8 when memory is added
            token_input=token_input,
            token_output=token_output,
            usd_cost=usd_cost,
            duration_ms=duration_ms,
            payload={"source_id": parsed.source_id, "sender_phone": parsed.sender_phone},
        ),
    )

    logger.info(
        "chat: reply sent to=%s tokens=%d+%d cost=$%.6f duration=%dms",
        parsed.sender_phone, token_input, token_output, usd_cost, duration_ms,
    )

    return ChatResult(
        reply=reply,
        token_input=token_input,
        token_output=token_output,
        usd_cost=usd_cost,
        duration_ms=duration_ms,
    )


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
