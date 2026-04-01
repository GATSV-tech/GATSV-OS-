"""
set_reminder tool — lets Jake set timed reminders via iMessage.

Claude calls this tool when it detects a reminder intent in the message.
The handler saves a scheduled_task row and returns a Pacific-time ack string.
"""

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import db.scheduled_tasks as db_tasks
from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register
from db.schemas import ScheduledTaskCreate

logger = logging.getLogger(__name__)

_PACIFIC = ZoneInfo("America/Los_Angeles")


async def _handle(tool_input: dict, ctx: ToolContext) -> ToolResult:
    raw_dt = tool_input["scheduled_at"]
    reminder_text = tool_input["reminder_text"].strip()

    scheduled_at = datetime.fromisoformat(raw_dt)
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=_PACIFIC).astimezone(timezone.utc)
    else:
        heduled_at = scheduled_at.astimezone(timezone.utc)

    await asyncio.to_thread(
        db_tasks.create,
        ScheduledTaskCreate(
            sender_phone=ctx.sender_phone,
            content=f"Reminder: {reminder_text}",
            scheduled_at=scheduled_at,
        ),
    )

    pt = scheduled_at.astimezone(_PACIFIC)
    display_time = pt.strftime("%I:%M %p").lstrip("0")

    logger.info(
        "set_reminder: saved task for %s at %s UTC (%s PT)",
        ctx.sender_phone,
        scheduled_at.isoformat(),
        display_time,
    )
    return ToolResult(ack=f"Got it — I'll remind you at {display_time} PT.")


register(
    ToolDefinition(
        name="set_reminder",
        description=(
            "Schedule a reminder message to be sent to the user at a specific time. "
            "Use this when the user asks to be reminded about something at a specific time. "
            "Pass the time in Pacific time exactly as the user stated it."
        ),
        input_schema={
            "type": "ject",
            "properties": {
                "scheduled_at": {
                    "type": "string",
                    "description": (
                        "The Pacific time datetime to send the reminder, in ISO 8601 format "
                        "(e.g. '2026-04-01T15:00:00'). Pass the time exactly as the user "
                        "stated it in Pacific time — do NOT convert to UTC."
                    ),
                },
                "reminder_text": {
                    "type": "string",
                    "description": "The reminder content, concise.",
                },
            },
            "required": ["scheduled_at", "reminder_text"],
        },
        handler=_handle,
    )
)
