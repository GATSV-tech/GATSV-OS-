"""
Postmark inbound email connector.

Receives raw Postmark inbound webhook payloads and returns a source-agnostic
ParsedEmail for the Gatekeeper to consume.

Never writes to the database. Never raises on missing optional fields.
"""

import logging
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


# ─── Postmark payload models ──────────────────────────────────────────────────

class PostmarkFromFull(BaseModel):
    model_config = ConfigDict(extra="ignore")

    Email: str = ""
    Name: str = ""
    MailboxHash: str = ""


class PostmarkInboundPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    MessageID: str = ""
    FromFull: PostmarkFromFull = PostmarkFromFull()
    Subject: str = ""
    TextBody: str = ""
    HtmlBody: str = ""
    Date: str = ""
    OriginalRecipient: str = ""
    ReplyTo: str = ""


# ─── Output model ─────────────────────────────────────────────────────────────

class ParsedEmail(BaseModel):
    """Source-agnostic email representation. Consumed by the Gatekeeper (Slice 5)."""

    source: str = "email"
    source_id: str | None
    raw_payload: dict[str, Any]
    sender_name: str | None
    sender_email: str | None
    subject: str | None
    body: str | None
    received_at: datetime | None


# ─── Public parser ────────────────────────────────────────────────────────────

def parse_postmark_inbound(raw: dict[str, Any]) -> ParsedEmail:
    """
    Parse a raw Postmark inbound webhook payload into a ParsedEmail.
    Always returns a ParsedEmail — never raises on missing optional fields.
    """
    payload = PostmarkInboundPayload.model_validate(raw)

    return ParsedEmail(
        source="email",
        source_id=_clean_message_id(payload.MessageID),
        raw_payload=raw,
        sender_name=_none_if_empty(payload.FromFull.Name),
        sender_email=_normalise_email(payload.FromFull.Email),
        subject=_none_if_empty(payload.Subject),
        body=_extract_body(payload.TextBody, payload.HtmlBody),
        received_at=_parse_date(payload.Date),
    )


# ─── Private helpers ──────────────────────────────────────────────────────────

def _none_if_empty(value: str) -> str | None:
    """Return None if value is empty or whitespace-only."""
    stripped = value.strip()
    return stripped if stripped else None


def _normalise_email(email: str) -> str | None:
    """Lowercase and strip. Return None if empty."""
    normalised = email.strip().lower()
    return normalised if normalised else None


def _clean_message_id(message_id: str) -> str | None:
    """Strip angle brackets: <id@host> → id@host. Return None if empty."""
    cleaned = message_id.strip().lstrip("<").rstrip(">").strip()
    return cleaned if cleaned else None


def _extract_body(text_body: str, html_body: str) -> str | None:
    """Prefer TextBody. Fall back to stripped HtmlBody. Return None if both empty."""
    if text_body and text_body.strip():
        return text_body.strip()
    if html_body and html_body.strip():
        stripped = _strip_html(html_body)
        return stripped if stripped else None
    return None


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    no_tags = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", no_tags).strip()


def _parse_date(date_str: str) -> datetime | None:
    """Parse RFC 2822 date string. Returns None if missing or unparseable."""
    if not date_str or not date_str.strip():
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        logger.warning("Could not parse Postmark Date field: %r", date_str)
        return None
