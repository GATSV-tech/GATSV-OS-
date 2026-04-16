"""get_weather tool — current weather via wttr.in (no API key required)."""

import httpx

from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register


async def _handle(tool_input: dict, _ctx: ToolContext) -> ToolResult:
    location = tool_input.get("location", "Las Vegas")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"https://wttr.in/{location}",
            params={"format": "3"},
        )

    return ToolResult(ack=resp.text.strip())


register(
    ToolDefinition(
        name="get_weather",
        description=(
            "Get current weather for a location. Use when the user asks "
            "about weather, temperature, or conditions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name. Defaults to Las Vegas if not specified.",
                },
            },
            "required": [],
        },
        handler=_handle,
    )
)
