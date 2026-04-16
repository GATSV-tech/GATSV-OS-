"""
Notion CRM tool for Bobot.
Manages investor contacts in the AXIS MARKET INVESTORS Notion database.

Supports:
  - add_investor    — create a new investor row
  - find_investor   — search by name or firm
  - update_investor — update status, notes, priority (also stamps Last Contact)
"""

import logging
from datetime import date

import httpx

from agents.tool_registry import ToolContext, ToolDefinition, ToolResult, register
from config import settings

logger = logging.getLogger(__name__)

_BASE = "https://api.notion.com/v1"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def _rich(text: str) -> list:
    return [{"text": {"content": text}}]


def _configured() -> bool:
    return bool(settings.notion_token and settings.notion_crm_db)


# ── add_investor ──────────────────────────────────────────────────────────────

async def _add_investor(tool_input: dict, _ctx: ToolContext) -> ToolResult:
    if not _configured():
        return ToolResult(ack="Notion CRM not configured.")

    name = tool_input["name"].strip()
    firm = tool_input.get("firm", "").strip()
    email = tool_input.get("email", "").strip()
    inv_type = tool_input.get("type", "ANGEL").strip().upper()
    status = tool_input.get("status", "TO CONTACT").strip().upper()
    priority = tool_input.get("priority", "WARM").strip().upper()
    notes = tool_input.get("notes", "").strip()

    props: dict = {
        "INVESTOR NAME": {"title": _rich(name)},
        "STATUS": {"select": {"name": status}},
        "PRIORITY": {"select": {"name": priority}},
        "LAST CONTACT": {"date": {"start": date.today().isoformat()}},
    }
    if firm:
        props["FIRM"] = {"rich_text": _rich(firm)}
    if email:
        props["EMAIL"] = {"email": email}
    if inv_type:
        props["TYPE"] = {"select": {"name": inv_type}}
    if notes:
        props["NOTES"] = {"rich_text": _rich(notes)}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_BASE}/pages",
            headers=_headers(),
            json={"parent": {"database_id": settings.notion_crm_db}, "properties": props},
        )

    if resp.status_code != 200:
        logger.error("add_investor: Notion error %d: %s", resp.status_code, resp.text[:200])
        return ToolResult(ack=f"Notion error {resp.status_code}.")

    detail = f" at {firm}" if firm else ""
    return ToolResult(
        ack=f"Added {name}{detail} to AXIS investor CRM. Status: {status}, Priority: {priority}."
    )


register(ToolDefinition(
    name="add_investor",
    description=(
        "Add a new investor or VC contact to the AXIS MARKET INVESTORS CRM. "
        "Use when the user says things like 'add [name] to investor CRM', "
        "'log a new investor: [name] from [firm]', or 'new investor lead: ...'. "
        "Required: name. Optional: firm, email, "
        "type (VC/ANGEL/FUND/FAMILY OFFICE/STRATEGIC), "
        "priority (HOT/WARM/COLD), "
        "status (TO CONTACT/CONTACTED/MEETING SCHEDULED/DECK SENT/"
        "NDA SIGNED/DUE DILIGENCE/TERM SHEET/CLOSED/PASS), notes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Full name of the investor or fund"},
            "firm": {"type": "string", "description": "Firm or fund name"},
            "email": {"type": "string", "description": "Email address"},
            "type": {"type": "string", "description": "Investor type: VC, ANGEL, FUND, FAMILY OFFICE, STRATEGIC"},
            "priority": {"type": "string", "description": "Priority: HOT, WARM, or COLD"},
            "status": {"type": "string", "description": "Status: TO CONTACT, CONTACTED, MEETING SCHEDULED, DECK SENT, NDA SIGNED, DUE DILIGENCE, TERM SHEET, CLOSED, PASS"},
            "notes": {"type": "string", "description": "Notes about this investor"},
        },
        "required": ["name"],
    },
    handler=_add_investor,
))


# ── find_investor ─────────────────────────────────────────────────────────────

async def _find_investor(tool_input: dict, _ctx: ToolContext) -> ToolResult:
    if not _configured():
        return ToolResult(ack="Notion CRM not configured.")

    query = tool_input["query"].strip()

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_BASE}/databases/{settings.notion_crm_db}/query",
            headers=_headers(),
            json={"page_size": 50},
        )

    if resp.status_code != 200:
        return ToolResult(ack=f"Notion error {resp.status_code}.")

    results = resp.json().get("results", [])
    matches = []
    ql = query.lower()

    for page in results:
        props = page.get("properties", {})
        name = "".join(
            p.get("plain_text", "")
            for p in props.get("INVESTOR NAME", {}).get("title", [])
        )
        firm = "".join(
            p.get("plain_text", "")
            for p in props.get("FIRM", {}).get("rich_text", [])
        )

        if ql not in name.lower() and ql not in firm.lower():
            continue

        status = (props.get("STATUS", {}).get("select") or {}).get("name", "—")
        priority = (props.get("PRIORITY", {}).get("select") or {}).get("name", "—")
        email = props.get("EMAIL", {}).get("email") or "—"
        notes = "".join(
            p.get("plain_text", "")
            for p in props.get("NOTES", {}).get("rich_text", [])
        )

        line = f"• {name} | {firm or '—'} | {status} | {priority} | {email}"
        if notes:
            line += f"\n  Notes: {notes[:120]}"
        matches.append(line)

    if not matches:
        return ToolResult(ack=f"No investors found matching '{query}'.")

    return ToolResult(
        ack=f"Found {len(matches)} match(es) for '{query}':\n" + "\n".join(matches)
    )


register(ToolDefinition(
    name="find_investor",
    description=(
        "Search the AXIS MARKET INVESTORS CRM for a contact by name or firm. "
        "Use when the user says things like 'find Sarah Guo in investor CRM', "
        "'what stage is a16z at?', or 'look up Ribbit Capital'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Investor name or firm to search for"},
        },
        "required": ["query"],
    },
    handler=_find_investor,
))


# ── update_investor ───────────────────────────────────────────────────────────

async def _update_investor(tool_input: dict, _ctx: ToolContext) -> ToolResult:
    if not _configured():
        return ToolResult(ack="Notion CRM not configured.")

    name = tool_input["name"].strip()
    status = tool_input.get("status", "").strip().upper()
    notes = tool_input.get("notes", "").strip()
    priority = tool_input.get("priority", "").strip().upper()

    # Find the page first.
    async with httpx.AsyncClient(timeout=15.0) as client:
        search_resp = await client.post(
            f"{_BASE}/databases/{settings.notion_crm_db}/query",
            headers=_headers(),
            json={"page_size": 100},
        )

    if search_resp.status_code != 200:
        return ToolResult(ack=f"Notion error {search_resp.status_code}.")

    results = search_resp.json().get("results", [])
    ql = name.lower()
    page_id = None
    matched_name = ""

    for page in results:
        props = page.get("properties", {})
        pname = "".join(
            p.get("plain_text", "")
            for p in props.get("INVESTOR NAME", {}).get("title", [])
        )
        pfirm = "".join(
            p.get("plain_text", "")
            for p in props.get("FIRM", {}).get("rich_text", [])
        )
        if ql in pname.lower() or ql in pfirm.lower():
            page_id = page["id"]
            matched_name = pname
            break

    if not page_id:
        return ToolResult(
            ack=f"No investor found matching '{name}'. Use add_investor to create one."
        )

    updates: dict = {
        "LAST CONTACT": {"date": {"start": date.today().isoformat()}},
    }
    if status:
        updates["STATUS"] = {"select": {"name": status}}
    if notes:
        updates["NOTES"] = {"rich_text": _rich(notes)}
    if priority:
        updates["PRIORITY"] = {"select": {"name": priority}}

    async with httpx.AsyncClient(timeout=15.0) as client:
        update_resp = await client.patch(
            f"{_BASE}/pages/{page_id}",
            headers=_headers(),
            json={"properties": updates},
        )

    if update_resp.status_code != 200:
        return ToolResult(ack=f"Update failed {update_resp.status_code}.")

    changes = []
    if status:
        changes.append(f"status → {status}")
    if notes:
        changes.append("notes updated")
    if priority:
        changes.append(f"priority → {priority}")
    changes.append("last contact → today")

    return ToolResult(ack=f"Updated {matched_name}: {', '.join(changes)}.")


register(ToolDefinition(
    name="update_investor",
    description=(
        "Update an existing investor's status, notes, or priority in the AXIS MARKET INVESTORS CRM. "
        "Also stamps Last Contact to today. "
        "Use when the user says things like 'update Sarah Guo to Contacted', "
        "'move a16z to Meeting Scheduled', 'add note to Ribbit: they want the deck', "
        "or 'mark Naval as HOT'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Investor name or firm to find and update"},
            "status": {"type": "string", "description": "New status: TO CONTACT, CONTACTED, MEETING SCHEDULED, DECK SENT, NDA SIGNED, DUE DILIGENCE, TERM SHEET, CLOSED, PASS"},
            "notes": {"type": "string", "description": "New or updated notes"},
            "priority": {"type": "string", "description": "Priority: HOT, WARM, or COLD"},
        },
        "required": ["name"],
    },
    handler=_update_investor,
))
