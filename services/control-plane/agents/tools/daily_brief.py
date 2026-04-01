"""
daily_brief tool — let Jake set or change the daily digest send time.

Triggered by phrases like "send me the daily brief at 8am", "change my digest to 7:30".
Saves the preference to Supabase user_prefs. The digest scheduler reads this pref
at the start of each sleep cycle, so the change takes effect the following morning.
"""

import asyncio
import logging

import db.user_prefs as db_prefs
from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register

logger = logging.getLogger(__name__)

_PREF_KEY = "digest_send_time_pt"


def _parse_and_validate(time_pt: str) -> tuple[int, int]:
    """
    Parse HH:MM (24h) into (hour, minute). Raises ValueError on bad input.
    Claude is instructed to convert natural language to this format before calling.
    """
    parts = time_pt.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"expected HH:MM, got '{time_pt}'")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time out of range: {time_pt}")
    return hour, minute


async def _handle(tool_input: dict, ctx: ToolContext) -> ToolResult:
    raw = tool_input["time_pt"].strip()
    try:
        hour, minute = _parse_and_validate(raw)
    except ValueError as exc:
        return ToolResult(ack=f"Couldn't set that time: {exc}. Please use HH:MM format (e.g. '08:00').")

    await asyncio.to_thread(db_prefs.set_pref, ctx.sender_phone, _PREF_KEY, f"{hour:02d}:{minute:02d}")

    # Build display time (12h)
    period = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    display = f"{display_hour}:{minute:02d} {period} PT"

    logger.info("daily_brief: set digest time to %02d:%02d PT for %s", hour, minute, ctx.sender_phone)
    return ToolResult(ack=f"Got it — your daily digest will send at {display} starting tomorrow.")


register(
    ToolDefinition(
        name="daily_brief",
        description=(
            "Set or change the time the daily digest/brief/summary is sent. "
            "Use ONLY when the user mentions changing when their daily digest, daily brief, "
            "or morning summary fires: 'send me the daily brief at 8am', "
            "'change my digest to 7:30', 'update my morning summary time'. "
            "The trigger phrase must reference the digest/brief/summary — not a one-off task reminder. "
            "Do NOT use for: setting task reminders ('remind me to...') or saving notes."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "time_pt": {
                    "type": "string",
                    "description": (
                        "The desired send time in Pacific time, 24-hour HH:MM format "
                        "(e.g. '08:00' for 8am, '07:30' for 7:30am, '19:00' for 7pm). "
                        "Convert from natural language before passing."
                    ),
                },
            },
            "required": ["time_pt"],
        },
        handler=_handle,
    )
)
