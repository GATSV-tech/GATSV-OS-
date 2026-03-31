"""
Unit tests for the Postmark inbound email connector.
Pure unit tests — no HTTP client, no database.
"""

import json
from pathlib import Path

from connectors.email import ParsedEmail, parse_postmark_inbound

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_full_payload():
    """All fields are correctly extracted from a complete Postmark payload."""
    raw = _load("postmark_email.json")
    result = parse_postmark_inbound(raw)

    assert isinstance(result, ParsedEmail)
    assert result.source == "email"
    assert result.source_id == "abc123def456@smtp.postmarkapp.com"
    assert result.sender_email == "sarah.chen@acmeco.com"
    assert result.sender_name == "Sarah Chen"
    assert result.subject == "Interested in your services"
    assert result.body is not None
    assert "GATSV Systems" in result.body
    assert result.received_at is not None
    assert result.raw_payload == raw


def test_source_id_strips_angle_brackets():
    """MessageID with angle brackets is cleaned: <id@host> → id@host."""
    raw = _load("postmark_email.json")
    raw["MessageID"] = "<abc123def456@smtp.postmarkapp.com>"
    result = parse_postmark_inbound(raw)
    assert result.source_id == "abc123def456@smtp.postmarkapp.com"


def test_body_falls_back_to_html_stripped():
    """When TextBody is empty, HtmlBody is used with tags stripped."""
    raw = _load("postmark_email.json")
    raw["TextBody"] = ""
    raw["HtmlBody"] = "<p>Hello from <strong>HTML</strong></p>"
    result = parse_postmark_inbound(raw)
    assert result.body == "Hello from HTML"


def test_missing_date_returns_none():
    """Empty Date field produces received_at=None without raising."""
    raw = _load("postmark_email.json")
    raw["Date"] = ""
    result = parse_postmark_inbound(raw)
    assert result.received_at is None


def test_empty_sender_name_returns_none():
    """Empty FromFull.Name produces sender_name=None."""
    raw = _load("postmark_email.json")
    raw["FromFull"]["Name"] = ""
    result = parse_postmark_inbound(raw)
    assert result.sender_name is None
