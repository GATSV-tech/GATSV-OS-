"""post_to_discord tool — post a message to the #clawbot Discord channel."""

import httpx

from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register
from config import settings


async def _handle(tool_input: dict, _ctx: ToolContext) -> ToolResult:
    msg = tool_input["message"]

    if not settings.discord_webhook_url:
        return ToolResult(ack="Discord not configured.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            settings.discord_webhook_url,
            json={"content": msg},
        )

    if resp.status_code in (200, 204):
        return ToolResult(ack="Posted to Discord.")
    return ToolResult(ack=f"Failed to post to Discord: HTTP {resp.status_code}")


register(
    ToolDefinition(
        name="post_to_discord",
        description=(
            "Post a message to Jake's Discord #clawbot channel. Use when the user "
            "asks to send, post, or log something to Discord."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The message to post."},
            },
            "required": ["message"],
        },
        handler=_handle,
    )
)
