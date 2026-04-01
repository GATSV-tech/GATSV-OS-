"""
Tool registry for the chat agent.

Each tool is a ToolDefinition that registers itself at import time via register().
The chat agent asks for the API-ready tools list via get_api_tools() and dispatches
Claude's tool_use responses via dispatch(). No tool names or schemas are hardcoded
in chat.py.

To add a new tool:
  1. Create agents/tools/your_tool.py — define the handler and call register().
  2. Import it in agents/tools/__init__.py.
  That's it. chat.py requires no changes.
"""

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext:
    """Caller-supplied context passed to every tool handler."""
    sender_phone: str


@dataclass(frozen=True)
class ToolResult:
    """Value returned by every tool handler."""
    ack: str  # Text to send to the user as the reply to their message.


ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


_registry: dict[str, ToolDefinition] = {}


def register(defn: ToolDefinition) -> None:
    """
    Register a tool definition. Called at module import time from each tool file.
    Raises if the same name is registered twice (catches copy-paste errors).
    """
    if defn.name in _registry:
        raise ValueError(f"Tool '{defn.name}' is already registered.")
    _registry[defn.name] = defn
    logger.debug("tool_registry: registered '%s'", defn.name)


def get_api_tools() -> list[dict[str, Any]]:
    """Return the tools list in the format the Claude API expects."""
    return [
        {
            "name": d.name,
            "description": d.description,
            "input_schema": d.input_schema,
        }
        for d in _registry.values()
    ]


async def dispatch(tool_name: str, tool_input: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Dispatch a tool_use response from Claude to the registered handler."""
    if tool_name not in _registry:
        raise ValueError(f"Unknown tool: '{tool_name}'")
    return await _registry[tool_name].handler(tool_input, ctx)
