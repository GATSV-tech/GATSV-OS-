"""send_slack tool — send a message to the Slack ops channel."""

import httpx

from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register
from config import settings


async def _handle(tool_input: dict, _ctx: ToolContext) -> ToolResult:
    msg = tool_input["message"]

    if not settings.slack_bot_token or not settings.slack_ops_channel_id:
        return ToolResult(ack="Slack not configured.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://slack.com/api/chat.postMessage",
            json={"channel": settings.slack_ops_channel_id, "text": msg},
            headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
        )

    data = resp.json()
    if data.get("ok"):
        return ToolResult(ack="Sent to Slack.")
    return ToolResult(ack=f"Slack send failed: {data.get('error', 'unknown')}")


register(
    ToolDefinition(
        name="send_slack",
        description=(
            "Send a message to Jake's Slack ops channel. Use when the user "
            "asks to post or log something to Slack."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The message to send."},
            },
            "required": ["message"],
        },
        handler=_handle,
    )
)
