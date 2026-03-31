"""
Shared connector output model.

All inbound source connectors (email, form, etc.) return a ParsedInbound.
The Gatekeeper consumes ParsedInbound regardless of source.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ParsedInbound(BaseModel):
    """Source-agnostic inbound payload. Consumed by the Gatekeeper (Slice 5)."""

    source: str
    source_id: str | None
    raw_payload: dict[str, Any]
    sender_name: str | None
    sender_email: str | None
    subject: str | None
    body: str | None
    received_at: datetime | None
