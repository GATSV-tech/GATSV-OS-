"""
list_reminders tool — show Jake all pending reminders.

Triggered by phrases like "what reminders do I have", "show my reminders".
"""

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import db.scheduled_tasks as db_tasks
from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register

logger = logging.getLogger(__name__)

_PACIFIC = ZoneInfo("America/Los_Angeles")


def _format_scheduled_at(raw: str) -> str:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    pt = dt.astimezone(_PACIFIC)
    return pt.strftime("%I:%M %p").lstrip("0") + " PT"


async def _handle(tool_input: dict, ctx: ToolContext) -> ToolResult:
    pending = await asyncio.to_thread(db_tasks.list_pending, ctx.sender_phone)

    if not pending:
        return ToolResult(ack="You have no pending reminders.")

    lines = []
    for i, task in enumerate(pending, 1):
        # Strip the "Reminder: " prefix stored by set_reminder for cleaner display
        content = task["content"].removeprefix("Reminder: ")
        time_str = _format_scheduled_at(task["scheduled_at"])
        lines.append(f"{i}. {content} — {time_str}")

    return ToolResult(ack="Your pending reminders:\n" + "\n".join(lines))


register(
    ToolDefinition(
        name="list_reminders",
        description=(
            "List the user's pending reminders. "
            "Use ONLY when the user asks to see their reminders: "
            "'what reminders do I have', 'show my reminders', 'list my reminders'. "
            "Do NOT use for notes, digest settings, or cancelling a reminder."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_handle,
    )
)
