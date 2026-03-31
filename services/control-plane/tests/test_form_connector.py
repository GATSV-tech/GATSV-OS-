"""
Unit tests for the Tally inbound form connector.
Pure unit tests — no HTTP client, no database.
"""

import json
from pathlib import Path

from connectors.base import ParsedInbound
from connectors.form import parse_tally_inbound

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_full_payload():
    """All fields are correctly extracted from a complete Tally payload."""
    raw = _load("tally_form.json")
    result = parse_tally_inbound(raw)

    assert isinstance(result, ParsedInbound)
    assert result.source == "form"
    assert result.source_id == "resp_abc789xyz"
    assert result.sender_email == "marcus.rivera@brightpath.io"
    assert result.sender_name == "Marcus Rivera"
    assert result.subject is None
    assert result.body is not None
    assert "Brightpath Agency" in result.body
    assert result.received_at is not None
    assert result.raw_payload == raw


def test_email_is_lowercased():
    """Email field value is normalised to lowercase."""
    raw = _load("tally_form.json")
    raw["data"]["fields"][1]["value"] = "Marcus.Rivera@BRIGHTPATH.IO"
    result = parse_tally_inbound(raw)
    assert result.sender_email == "marcus.rivera@brightpath.io"


def test_missing_email_returns_none():
    """When no INPUT_EMAIL field is present, sender_email is None."""
    raw = _load("tally_form.json")
    raw["data"]["fields"] = [f for f in raw["data"]["fields"] if f["type"] != "INPUT_EMAIL"]
    result = parse_tally_inbound(raw)
    assert result.sender_email is None


def test_missing_date_returns_none():
    """Empty submittedAt and createdAt produce received_at=None without raising."""
    raw = _load("tally_form.json")
    raw["createdAt"] = ""
    raw["data"]["submittedAt"] = ""
    result = parse_tally_inbound(raw)
    assert result.received_at is None


def test_body_includes_all_non_empty_fields():
    """Body concatenates all field label/value pairs that have non-empty values."""
    raw = _load("tally_form.json")
    result = parse_tally_inbound(raw)
    assert result.body is not None
    assert "Full Name: Marcus Rivera" in result.body
    assert "Company: Brightpath Agency" in result.body
    assert "Team Size: 6" in result.body
