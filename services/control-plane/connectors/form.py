"""
Tally inbound form connector.

Receives raw Tally FORM_RESPONSE webhook payloads and returns a source-agnostic
ParsedInbound for the Gatekeeper to consume.

Never writes to the database. Never raises on missing optional fields.

Tally payload shape (relevant fields):
  {
    "eventId": "...",
    "createdAt": "2026-03-31T10:15:00.000Z",
    "data": {
      "responseId": "...",
      "submittedAt": "2026-03-31T10:15:00.000Z",
      "fields": [
        {"key": "q1", "label": "Name", "type": "INPUT_TEXT", "value": "Sarah Chen"},
        {"key": "q2", "label": "Email", "type": "INPUT_EMAIL", "value": "sarah@acme.com"},
        ...
      ]
    }
  }
"""

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from connectors.base import ParsedInbound

logger = logging.getLogger(__name__)


# ─── Tally payload models ─────────────────────────────────────────────────────

class TallyField(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str = ""
    label: str = ""
    type: str = ""
    value: Any = None


class TallyFormData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    responseId: str = ""
    submittedAt: str = ""
    fields: list[TallyField] = []


class TallyInboundPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    eventId: str = ""
    createdAt: str = ""
    data: TallyFormData = TallyFormData()


# ─── Public parser ────────────────────────────────────────────────────────────

def parse_tally_inbound(raw: dict[str, Any]) -> ParsedInbound:
    """
    Parse a raw Tally FORM_RESPONSE webhook payload into a ParsedInbound.
    Always returns a ParsedInbound — never raises on missing optional fields.
    """
    payload = TallyInboundPayload.model_validate(raw)
    fields = payload.data.fields

    return ParsedInbound(
        source="form",
        source_id=_none_if_empty(payload.data.responseId),
        raw_payload=raw,
        sender_name=_extract_name(fields),
        sender_email=_extract_email(fields),
        subject=None,
        body=_build_body(fields),
        received_at=_parse_date(payload.data.submittedAt or payload.createdAt),
    )


# ─── Private helpers ──────────────────────────────────────────────────────────

def _none_if_empty(value: str) -> str | None:
    stripped = value.strip()
    return stripped if stripped else None


def _extract_email(fields: list[TallyField]) -> str | None:
    """Return the first INPUT_EMAIL field value, lowercased."""
    for field in fields:
        if field.type == "INPUT_EMAIL" and isinstance(field.value, str):
            normalised = field.value.strip().lower()
            if normalised:
                return normalised
    return None


def _extract_name(fields: list[TallyField]) -> str | None:
    """Return the first field whose label contains 'name' (case-insensitive)."""
    for field in fields:
        if "name" in field.label.lower() and isinstance(field.value, str):
            stripped = field.value.strip()
            if stripped:
                return stripped
    return None


def _build_body(fields: list[TallyField]) -> str | None:
    """Concatenate all non-empty field values as 'Label: value' pairs."""
    lines: list[str] = []
    for field in fields:
        if field.value is None:
            continue
        value_str = str(field.value).strip()
        if not value_str:
            continue
        label = field.label.strip() or field.key
        lines.append(f"{label}: {value_str}")
    return "\n".join(lines) if lines else None


def _parse_date(date_str: str) -> datetime | None:
    """Parse ISO 8601 date string (Tally format). Returns None if missing or unparseable."""
    if not date_str or not date_str.strip():
        return None
    try:
        # Python 3.11+ fromisoformat handles Z suffix; runtime is 3.11+
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except Exception:
        logger.warning("Could not parse Tally date field: %r", date_str)
        return None
