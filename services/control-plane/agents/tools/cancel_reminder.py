"""
cancel_reminder tool — cancel a pending reminder by keyword or time match.

Triggered by phrases like "cancel my 4pm reminder", "cancel the call Marcus reminder".
Loads all pending reminders and cancels the first one whose content or scheduled
time contains the query (case-insensitive substring match). Chronological order
ensures the earliest match wins when multiple reminders share similar text.
"""

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import db.scheduled_tasks as db_tasks
from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register

logger = logging.getLogger(__name__)

_PACIFIC = ZoneInfo("America/Los_Angeles")


def _pt_display(raw: str) -> tuple[str, str]:
    """Return (display_time, time_lower) for a scheduled_at ISO string."""
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    pt = dt.astimezone(_PACIFIC)
    display = pt.strftime("%I:%M %p").lstrip("0") + " PT"
    return display, display.lower()


async def _handle(tool_input: dict, ctx: ToolContext) -> ToolResult:
    query = tool_input["query"].strip().lower()
    pending = await asyncio.to_thread(db_tasks.list_pending, ctx.sender_phone)

    if not pending:
        return ToolResult(ack="You have no pending reminders to cancel.")

    match = None
    for task in pending:
        content_lower = task["content"].lower()
        _, time_lower = _pt_display(task["scheduled_at"])
        if query in content_lower or query in time_lower:
            match = task
            break

    if match is None:
        return ToolResult(ack=f"No pending reminder matching '{tool_input['query']}' found.")

    await asyncio.to_thread(db_tasks.mark_status, match["id"], "cancelled")

    display_time, _ = _pt_display(match["scheduled_at"])
    content = match["content"]
    logger.info("cancel_reminder: cancelled task %s for %s", match["id"], ctx.sender_phone)
    return ToolResult(ack=f"Cancelled: {content} (was due at {display_time}).")


register(
    ToolDefinition(
        name="cancel_reminder",
        description=(
            "Cancel a pending reminder by matching its content or scheduled time. "
            "Use ONLY when the user explicitly asks to cancel or remove a specific reminder: "
            "'cancel my 4pm reminder', 'remove the call Marcus reminder'. "
            "Do NOT use for: listing reminders, deleting notes, or changing digest time."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Text to match against reminder content or time "
                        "(e.g. 'call Marcus', '4pm', '4:00'). Case-insensitive substring match."
                    ),
                },
            },
            "required": ["query"],
        },
        handler=_handle,
    )
)
