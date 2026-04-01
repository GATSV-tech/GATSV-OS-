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

    # Parse ISO 8601. Claude is instructed to send UTC; handle both aware and naive.
    scheduled_at = datetime.fromisoformat(raw_dt)
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    else:
        scheduled_at = scheduled_at.astimezone(timezone.utc)

    await asyncio.to_thread(
        db_tasks.create,
        ScheduledTaskCreate(
            sender_phone=ctx.sender_phone,
            content=f"Reminder: {reminder_text}",
            scheduled_at=scheduled_at,
        ),
    )

    # Display time in Pacific — strip leading zero from hour (%-I is Linux-only)
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
            "Convert the requested time to UTC before calling this tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "scheduled_at": {
                    "type": "string",
                    "description": (
                        "The UTC datetime to send the reminder, in ISO 8601 format "
                        "(e.g. '2026-04-01T22:00:00Z'). Convert from the user's "
                        "local time (Pacific) to UTC before passing here."
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
