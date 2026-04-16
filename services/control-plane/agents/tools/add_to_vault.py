"""
add_to_vault tool — push a note to the Lumen Obsidian vault via GitHub API.

Appends a timestamped entry to 02 - Ideas/Inbox.md in GATSV-tech/Lumen-Vault.
Uses GITHUB_VAULT_TOKEN env var. Obsidian Git on Mac auto-pulls the changes.
"""

import base64 as b64
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register
from config import settings

logger = logging.getLogger(__name__)

_PACIFIC = ZoneInfo("America/Los_Angeles")
_REPO = "GATSV-tech/Lumen-Vault"
_INBOX = "02 - Ideas/Inbox.md"
_API = "https://api.github.com"


async def _handle(tool_input: dict, _ctx: ToolContext) -> ToolResult:
    content = tool_input["content"].strip()
    tag = tool_input.get("tag", "idea").strip().lower()

    if not settings.github_vault_token:
        logger.error("add_to_vault: GITHUB_VAULT_TOKEN not configured")
        return ToolResult(ack="Sorry, vault sync is not configured.")

    headers = {
        "Authorization": f"Bearer {settings.github_vault_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    encoded_path = _INBOX.replace(" ", "%20")
    url = f"{_API}/repos/{_REPO}/contents/{encoded_path}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        current = b64.b64decode(data["content"]).decode("utf-8")
        sha = data["sha"]

        timestamp = datetime.now(_PACIFIC).strftime("%Y-%m-%d %H:%M PT")
        new_entry = f"\n- [{timestamp}] **{tag}** — {content}"
        updated = current.rstrip() + new_entry + "\n"
        encoded = b64.b64encode(updated.encode("utf-8")).decode("utf-8")

        put_resp = await client.put(url, headers=headers, json={
            "message": f"bobot: {tag}",
            "content": encoded,
            "sha": sha,
        })
        put_resp.raise_for_status()

    logger.info("add_to_vault: pushed %s note to vault", tag)
    return ToolResult(ack="Added to your vault inbox.")


register(
    ToolDefinition(
        name="add_to_vault",
        description=(
            "Add an idea or note to Jake's Obsidian vault inbox. "
            "Use ONLY when the user says: vault this, add to vault, idea:, obsidian:, "
            "add this to obsidian, save to vault. "
            "Do NOT use for: timed reminders, regular notes, or anything investor/CRM related."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Note content to save.",
                },
                "tag": {
                    "type": "string",
                    "enum": ["idea", "task", "meeting", "reference"],
                    "description": "Tag for the note type. Defaults to idea.",
                },
            },
            "required": ["content"],
        },
        handler=_handle,
    )
)
