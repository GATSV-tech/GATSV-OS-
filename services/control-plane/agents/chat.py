"""
Chat agent — the personal iMessage Claude bot reply loop.

Receives a ParsedInbound from an inbound iMessage, calls Claude with a
rolling conversation context window, and sends the reply via Sendblue.

Tool use: Claude may return a tool_use response instead of a text reply.
Tool definitions are loaded from the tool registry — chat.py is not aware
of individual tool names or schemas. To add a new tool, register it in
agents/tools/ and import it in agents/tools/__init__.py.

Message ordering:
  1. Persist user turn → always first, so we never lose a user message if
     generation fails downstream.
  2. Load history window (config: chat_history_limit).
  3. Call Claude with full messages list + registered tools.
  4a. end_turn → send Claude's text reply via Sendblue.
  4b. tool_use → dispatch to handler → send ack via Sendblue.
  5. Persist assistant turn (reply text or ack).
  6. Write action row for observability.

Error policy: Claude API failures and Sendblue send failures are logged to
health_logs and return None. DB failures on history append are logged but
never crash the reply. The webhook always returns 202.
"""

import asyncio
import logging
import time
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import anthropic

# Import agents.tools to trigger all register() calls before get_api_tools() is used.
import agents.tools  # noqa: F401
import agents.tool_registry as tool_registry
import db.actions as db_actions
import db.chat_messages as db_chat
import db.health_logs as db_health_logs
from config import settings
from connectors.base import ParsedInbound
from connectors.sendblue_send import build_status_callback_url, send_message
from db.schemas import ActionCreate, HealthLogCreate
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Patchable at the module level — mocked in tests.
_anthropic = anthropic.AsyncAnthropic()

_PACIFIC = ZoneInfo("America/Los_Angeles")

# Claude Sonnet 4.6 pricing
_COST_PER_INPUT_TOKEN = Decimal("3") / Decimal("1000000")    # $3 / 1M tokens
_COST_PER_OUTPUT_TOKEN = Decimal("15") / Decimal("1000000")  # $15 / 1M tokens


def _build_system_prompt() -> str:
    """
    Build the system prompt with the current Pacific time injected.
    Called at the start of each request so relative times ("at 3pm", "in 2 hours")
    are interpreted correctly.
    """
    now_pt = datetime.now(_PACIFIC)
    time_str = now_pt.strftime("%A, %B %-d, %Y at %-I:%M %p PT")
    return (
        f"You are Jake's personal assistant, reachable via iMessage. "
        f"Current time: {time_str}. "
        "Be direct and concise. "
        "Respond in plain text — no markdown, no bullet points unless asked. "
        "When the user asks to be reminded about something at a specific time, "
        "use the set_reminder tool. Convert times to UTC before calling the tool."
    )


class ChatResult(BaseModel):
    reply: str
    token_input: int
    token_output: int
    usd_cost: Decimal
    duration_ms: int


async def run(parsed: ParsedInbound) -> ChatResult | None:
    """
    Persist user turn, call Claude with history + tools, send reply, persist
    assistant turn. Returns ChatResult on success, None on any failure.
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
    phone = parsed.sender_phone

    # 1. Persist user turn first — never lose a user message if generation fails.
    await _append_turn(phone, "user", parsed.body)

    # 2. Load history window (includes the turn we just saved).
    history = await asyncio.to_thread(
        db_chat.get_recent, phone, settings.chat_history_limit
    )
    messages = [{"role": row["role"], "content": row["content"]} for row in history]

    # 3. Call Claude with full context and all registered tools.
    response = await _anthropic.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=_build_system_prompt(),
        messages=messages,
        tools=tool_registry.get_api_tools(),
    )

    token_input = response.usage.input_tokens
    token_output = response.usage.output_tokens
    usd_cost = (
        Decimal(token_input) * _COST_PER_INPUT_TOKEN
        + Decimal(token_output) * _COST_PER_OUTPUT_TOKEN
    )

    # 4. Branch on stop_reason.
    if response.stop_reason == "tool_use":
        sent_text = await _handle_tool_use(response, phone)
    else:
        sent_text = response.content[0].text
        await send_message(
            to_number=phone,
            content=sent_text,
            status_callback=build_status_callback_url(),
        )

    # 5. Persist assistant turn (text reply or tool ack).
    await _append_turn(phone, "assistant", sent_text)

    duration_ms = _elapsed_ms(start)

    # 6. Write action row for observability.
    await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="chat",
            action_type="tool_use" if response.stop_reason == "tool_use" else "send_reply",
            event_id=None,
            token_input=token_input,
            token_output=token_output,
            usd_cost=usd_cost,
            duration_ms=duration_ms,
            payload={"source_id": parsed.source_id, "sender_phone": phone},
        ),
    )

    logger.info(
        "chat: %s to=%s turns_in_context=%d tokens=%d+%d cost=$%.6f duration=%dms",
        response.stop_reason,
        phone,
        len(messages),
        token_input,
        token_output,
        usd_cost,
        duration_ms,
    )

    return ChatResult(
        reply=sent_text,
        token_input=token_input,
        token_output=token_output,
        usd_cost=usd_cost,
        duration_ms=duration_ms,
    )


async def _handle_tool_use(response, phone: str) -> str:
    """
    Find the first tool_use block in the response, dispatch it, send the ack,
    and return the ack text (which will be saved as the assistant turn).
    """
    tool_block = next(
        block for block in response.content if block.type == "tool_use"
    )
    ctx = tool_registry.ToolContext(sender_phone=phone)
    result = await tool_registry.dispatch(tool_block.name, tool_block.input, ctx)

    await send_message(
        to_number=phone,
        content=result.ack,
        status_callback=build_status_callback_url(),
    )
    return result.ack


async def _append_turn(phone: str, role: str, content: str) -> None:
    """
    Persist a conversation turn. DB failures are logged but never propagated —
    a failed history write must not kill the reply loop.
    """
    try:
        await asyncio.to_thread(db_chat.append, phone, role, content)
    except Exception as exc:
        logger.error(
            "chat: failed to persist %s turn for %s: %s", role, phone, exc, exc_info=True
        )


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
