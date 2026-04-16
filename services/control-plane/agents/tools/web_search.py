"""web_search tool — search the web via Brave Search API."""

import logging

import httpx

from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register
from config import settings

logger = logging.getLogger(__name__)


async def _handle(tool_input: dict, _ctx: ToolContext) -> ToolResult:
    query = tool_input["query"]

    if not settings.brave_api_key:
        return ToolResult(ack="Web search not configured.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 5},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": settings.brave_api_key,
            },
        )

    results = resp.json().get("web", {}).get("results", [])
    if not results:
        return ToolResult(ack="No results found.")

    lines = [
        f"{r['title']}\n{r.get('description', '')}\n{r['url']}"
        for r in results[:3]
    ]
    return ToolResult(ack="\n\n".join(lines))


register(
    ToolDefinition(
        name="web_search",
        description=(
            "Search the web for current information. Use when the user asks about "
            "news, facts, prices, people, or anything requiring a lookup."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
            },
            "required": ["query"],
        },
        handler=_handle,
    )
)
