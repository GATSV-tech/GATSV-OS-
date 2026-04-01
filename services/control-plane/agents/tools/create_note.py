"""
create_note tool — save a freeform note from Jake.

Triggered by phrases like "remember that...", "note: ...", "save this: ...".
"""

import asyncio
import logging

import db.notes as db_notes
from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register

logger = logging.getLogger(__name__)


async def _handle(tool_input: dict, ctx: ToolContext) -> ToolResult:
    content = tool_input["content"].strip()
    await asyncio.to_thread(db_notes.create, ctx.sender_phone, content)
    logger.info("create_note: saved note for %s", ctx.sender_phone)
    return ToolResult(ack="Got it — I've saved that note.")


register(
    ToolDefinition(
        name="create_note",
        description=(
            "Save a freeform note for later. Use this when the user says something like "
            "'remember that...', 'note: ...', or 'save this: ...'. "
            "Extract the note content from their message."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The note content to save, extracted from the user's message.",
                },
            },
            "required": ["content"],
        },
        handler=_handle,
    )
)
