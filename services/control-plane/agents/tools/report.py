"""
report tool — generate an on-demand operational digest for Jake.

Triggered by phrases like "give me a report", "show me the stats",
"how's the system doing", "ops report", "what happened today".
"""

import logging

from agents import reporter
from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register

logger = logging.getLogger(__name__)


async def _handle(tool_input: dict, ctx: ToolContext) -> ToolResult:
    window_hours = int(tool_input.get("window_hours", 24))
    # Clamp to a sane range to prevent accidental massive queries.
    window_hours = max(1, min(window_hours, 168))  # 1h – 7d

    logger.info("report tool: generating digest window_hours=%d for %s", window_hours, ctx.sender_phone)

    try:
        text = await reporter.generate_digest(window_hours=window_hours)
    except Exception as exc:
        logger.error("report tool: generate_digest failed: %s", exc, exc_info=True)
        return ToolResult(ack="Sorry, I couldn't generate the report right now. Check the health logs.")

    return ToolResult(ack=text)


register(
    ToolDefinition(
        name="generate_report",
        description=(
            "Generate an on-demand operational digest summarizing system activity, "
            "costs, events, actions, and open approvals. "
            "Use when the user asks for a report, stats, ops summary, or system status. "
            "Examples: 'give me a report', 'show me the stats', 'how's the system doing', "
            "'what happened today', 'weekly summary'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "window_hours": {
                    "type": "integer",
                    "description": (
                        "Lookback window in hours. Default 24 (last 24h). "
                        "Use 168 for a weekly report, 1 for the last hour."
                    ),
                    "default": 24,
                },
            },
            "required": [],
        },
        handler=_handle,
    )
)
