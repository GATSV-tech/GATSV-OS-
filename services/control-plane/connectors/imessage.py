"""
Sendblue iMessage inbound connector.

Receives raw Sendblue RECEIVE webhook payloads and returns a source-agnostic
ParsedInbound for the Gatekeeper to consume.

Never writes to the database. Never raises on missing optional fields.

Sendblue inbound webhook payload shape:
  {
    "from_number":     "+15551234567",
    "to_number":       "+13053369541",
    "content":         "Hey, got your message!",
    "media_url":       "https://storage.sendblue.co/...",
    "service":         "iMessage",
    "group_id":        null,
    "date_sent":       "2026-03-31T14:45:00Z",
    "message_handle":  "msg_handle_abc123"
  }

Register your webhook URL as:
    https://yourdomain.com/inbound/imessage?token=YOUR_SECRET
"""

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from connectors.base import ParsedInbound

logger = logging.getLogger(__name__)


# ─── Sendblue payload model ───────────────────────────────────────────────────

class SendblueInboundPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    from_number: str = ""
    to_number: str = ""
    content: str = ""
    media_url: str | None = None
    service: str = ""           # "iMessage" | "SMS" | "RCS"
    group_id: str | None = None
    date_sent: str = ""
    message_handle: str = ""    # stable message ID — used as source_id for dedup
    is_outbound: bool = False
    status: str = ""            # REGISTERED | PENDING | QUEUED | ACCEPTED | SENT | DELIVERED | DECLINED | ERROR
    number: str = ""            # canonical number field (mirrors from_number on inbound)


# ─── Public parser ────────────────────────────────────────────────────────────

def parse_sendblue_inbound(raw: dict[str, Any]) -> ParsedInbound:
    """
    Parse a raw Sendblue RECEIVE webhook payload into a ParsedInbound.
    Always returns a ParsedInbound — never raises on missing optional fields.
    """
    payload = SendblueInboundPayload.model_validate(raw)

    return ParsedInbound(
        source="imessage",
        source_id=_none_if_empty(payload.message_handle),
        raw_payload=raw,
        sender_name=None,           # Sendblue provides no display name, only number
        sender_email=None,
        sender_phone=_normalise_phone(payload.from_number),
        subject=None,
        body=_none_if_empty(payload.content),
        received_at=_parse_date(payload.date_sent),
    )


# ─── Private helpers ──────────────────────────────────────────────────────────

def _none_if_empty(value: str) -> str | None:
    stripped = value.strip()
    return stripped if stripped else None


def _normalise_phone(phone: str) -> str | None:
    """Strip whitespace. Return None if empty. Preserve E.164 format."""
    stripped = phone.strip()
    return stripped if stripped else None


def _parse_date(date_str: str) -> datetime | None:
    """Parse ISO 8601 date string (Sendblue format). Returns None if missing or unparseable."""
    if not date_str or not date_str.strip():
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except Exception:
        logger.warning("Could not parse Sendblue date_sent field: %r", date_str)
        return None
