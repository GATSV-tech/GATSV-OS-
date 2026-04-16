"""
create_note tool — append a quick capture note to the Lumen vault inbox.

Triggered by phrases like "remember that...", "note: ...", "save this: ...".
Reads 02 - Ideas/Inbox.md from GATSV-tech/Lumen-Vault via GitHub API,
prepends the new entry (newest at top), and writes it back.
Obsidian Git on the Mac pulls the change automatically.
"""

import base64
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register
from config import settings

logger = logging.getLogger(__name__)

_PACIFIC = ZoneInfo("America/Los_Angeles")
_GITHUB_API = "https://api.github.com"


async def _handle(tool_input: dict, _ctx: ToolContext) -> ToolResult:
    content = tool_input["content"].strip()

    if not settings.github_token:
        logger.error("create_note: GITHUB_TOKEN not configured")
        return ToolResult(ack="Couldn't save — vault not configured. Tell Jake to set GITHUB_TOKEN.")

    headers = {
        "Authorization": f"token {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # URL-encode spaces in the path
    encoded_path = settings.lumen_inbox_path.replace(" ", "%20")
    url = f"{_GITHUB_API}/repos/{settings.lumen_vault_repo}/contents/{encoded_path}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Fetch current file to get SHA and existing content.
        get_resp = await client.get(url, headers=headers)
        if get_resp.status_code != 200:
            logger.error("create_note: failed to fetch inbox file %d: %s", get_resp.status_code, get_resp.text)
            return ToolResult(ack="Couldn't reach the vault right now. Try again in a moment.")

        file_data = get_resp.json()
        sha = file_data["sha"]
        current_content = base64.b64decode(file_data["content"]).decode()

        # 2. Build the new entry and prepend it below the divider.
        now = datetime.now(_PACIFIC)
        timestamp = now.strftime("%Y-%m-%d %H:%M PT")
        new_entry = f"**{timestamp}** — {content}\n\n"

        # Insert after the "---\n\n<!-- New ideas go here..." comment block.
        divider = "---\n\n<!-- New ideas go here, newest at top -->\n"
        if divider in current_content:
            updated_content = current_content.replace(divider, divider + new_entry, 1)
        else:
            # Fallback: just prepend after the first ---
            updated_content = current_content + new_entry

        encoded = base64.b64encode(updated_content.encode()).decode()

        # 3. Write the updated file back.
        put_resp = await client.put(url, headers=headers, json={
            "message": f"note: {content[:60]}{'...' if len(content) > 60 else ''}",
            "content": encoded,
            "sha": sha,
        })

    if put_resp.status_code not in (200, 201):
        logger.error("create_note: GitHub API write error %d: %s", put_resp.status_code, put_resp.text)
        return ToolResult(ack="Couldn't save to vault. I'll retry next time.")

    logger.info("create_note: appended note to %s", settings.lumen_inbox_path)
    return ToolResult(ack="Added to your vault inbox.")


register(
    ToolDefinition(
        name="create_note",
        description=(
            "Save a freeform note for the user. "
            "Use ONLY when the user is saving information with no time component: "
            "'note: ...', 'remember that...', 'save this:', 'write this down', 'keep track of'. "
            "Do NOT use for: timed reminders ('remind me at X to...') "
            "or digest/schedule changes ('send me the brief at X', 'change digest time')."
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
