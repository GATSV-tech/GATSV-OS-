"""
Unit tests for the Sendblue iMessage inbound connector.
Pure unit tests — no HTTP client, no database.
"""

import json
from pathlib import Path

from connectors.base import ParsedInbound
from connectors.imessage import parse_sendblue_inbound

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_full_payload():
    """All fields are correctly extracted from a complete Sendblue payload."""
    raw = _load("sendblue_inbound.json")
    result = parse_sendblue_inbound(raw)

    assert isinstance(result, ParsedInbound)
    assert result.source == "imessage"
    assert result.source_id == "msg_handle_abc123xyz"
    assert result.sender_phone == "+17025551234"
    assert result.body is not None
    assert "AI operations" in result.body
    assert result.received_at is not None
    assert result.raw_payload == raw


def test_sender_email_and_name_are_always_none():
    """iMessage contacts have no email or display name from Sendblue."""
    raw = _load("sendblue_inbound.json")
    result = parse_sendblue_inbound(raw)
    assert result.sender_email is None
    assert result.sender_name is None


def test_phone_is_normalised():
    """Phone number is stripped of whitespace, E.164 format preserved."""
    raw = _load("sendblue_inbound.json")
    raw["from_number"] = "  +17025551234  "
    result = parse_sendblue_inbound(raw)
    assert result.sender_phone == "+17025551234"


def test_empty_content_returns_none_body():
    """Empty content field produces body=None."""
    raw = _load("sendblue_inbound.json")
    raw["content"] = ""
    result = parse_sendblue_inbound(raw)
    assert result.body is None


def test_missing_date_returns_none():
    """Empty date_sent produces received_at=None without raising."""
    raw = _load("sendblue_inbound.json")
    raw["date_sent"] = ""
    result = parse_sendblue_inbound(raw)
    assert result.received_at is None
