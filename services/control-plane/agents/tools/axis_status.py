"""axis_status tool — check AXIS MARKET backend health."""

import httpx

from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register

_BASE = "https://axis-market-production-26c1.up.railway.app"


async def _handle(tool_input: dict, _ctx: ToolContext) -> ToolResult:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"{_BASE}/api/health")
            state = "up" if resp.status_code == 200 else "down"
            return ToolResult(ack=f"AXIS MARKET is {state}.")
        except Exception as exc:
            return ToolResult(ack=f"AXIS MARKET unreachable: {exc}")


register(
    ToolDefinition(
        name="axis_status",
        description=(
            "Check AXIS MARKET backend health. Use when the user asks "
            "if AXIS is up, running, or having issues."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_handle,
    )
)
