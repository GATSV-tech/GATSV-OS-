"""
Unit tests for the digest agent.
All external calls (Claude API, Sendblue, DB) are mocked.
"""

from contextlib import ExitStack
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.digest import send_daily_digest


def _mock_claude_response(text: str = "Good morning. Here's your digest.") -> MagicMock:
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage.input_tokens = 80
    response.usage.output_tokens = 40
    return response


_NO_EVENTS: list = []
_NO_TASKS: list = []
_NO_ERRORS: list = []

_SAMPLE_EVENTS = [
    {"source": "email",    "status": "received", "created_at": "2026-04-01T12:00:00Z"},
    {"source": "email",    "status": "received", "created_at": "2026-04-01T13:00:00Z"},
    {"source": "imessage", "status": "received", "created_at": "2026-04-01T14:00:00Z"},
]

_SAMPLE_TASKS = [
    {"content": "Reminder: call Marcus", "scheduled_at": "2026-04-01T22:00:00Z", "status": "pending"},
]

_SAMPLE_ERRORS = [
    {"service": "scheduler", "message": "task abc failed: timeout", "created_at": "2026-04-01T03:00:00Z"},
]

# Fixed patches applied in every test — enter via ExitStack.
_COMMON_PATCHES = [
    ("agents.digest.db_actions.create",          {"return_value": {"id": "action-1"}}),
    ("agents.digest.build_status_callback_url",  {"return_value": None}),
]


def _enter_common(stack: ExitStack, *, events=_NO_EVENTS, tasks=_NO_TASKS, errors=_NO_ERRORS) -> dict:
    """Enter all common patches and return a dict of key mocks."""
    mocks = {}
    mocks["events"]  = stack.enter_context(patch("agents.digest.db_digest.events_in_window",    return_value=events))
    mocks["tasks"]   = stack.enter_context(patch("agents.digest.db_digest.scheduled_for_window", return_value=tasks))
    mocks["errors"]  = stack.enter_context(patch("agents.digest.db_digest.errors_since",         return_value=errors))
    mocks["action"]  = stack.enter_context(patch("agents.digest.db_actions.create",              return_value={"id": "a-1"}))
    mocks["send"]    = stack.enter_context(patch("agents.digest.send_message",                   new_callable=AsyncMock, return_value={"status": "QUEUED"}))
    stack.enter_context(patch("agents.digest.build_status_callback_url",                         return_value=None))
    return mocks


# ─── Happy path ───────────────────────────────────────────────────────────────

async def test_digest_calls_claude_and_sends_message():
    """Happy path: Claude is called, message is sent, action row written."""
    with ExitStack() as stack:
        mocks = _enter_common(stack, events=_SAMPLE_EVENTS, tasks=_SAMPLE_TASKS)
        mock_client = stack.enter_context(patch("agents.digest._anthropic"))
        mock_client.messages.create = AsyncMock(return_value=_mock_claude_response())

        await send_daily_digest("+17025551234")

    mock_client.messages.create.assert_called_once()
    mocks["send"].assert_called_once_with(
        to_number="+17025551234",
        content="Good morning. Here's your digest.",
        status_callback=None,
    )
    mocks["action"].assert_called_once()
    action_arg = mocks["action"].call_args[0][0]
    assert action_arg.action_type == "daily_digest"
    assert action_arg.usd_cost > Decimal("0")


# ─── Data block content ───────────────────────────────────────────────────────

async def test_digest_data_block_includes_event_counts():
    """Events are counted and broken down by source in the Claude prompt."""
    captured: list[dict] = []

    async def capture_call(**kwargs):
        captured.append(kwargs)
        return _mock_claude_response()

    with ExitStack() as stack:
        _enter_common(stack, events=_SAMPLE_EVENTS)
        mock_client = stack.enter_context(patch("agents.digest._anthropic"))
        mock_client.messages.create = AsyncMock(side_effect=capture_call)

        await send_daily_digest("+17025551234")

    data_block = captured[0]["messages"][0]["content"]
    assert "3 event(s)" in data_block
    assert "email: 2" in data_block
    assert "imessage: 1" in data_block


async def test_digest_no_reminders_shows_none_section():
    """When there are no reminders for today, the section still appears with a clear message."""
    captured: list[dict] = []

    async def capture_call(**kwargs):
        captured.append(kwargs)
        return _mock_claude_response()

    with ExitStack() as stack:
        _enter_common(stack, tasks=_NO_TASKS)
        mock_client = stack.enter_context(patch("agents.digest._anthropic"))
        mock_client.messages.create = AsyncMock(side_effect=capture_call)

        await send_daily_digest("+17025551234")

    data_block = captured[0]["messages"][0]["content"]
    assert "TODAY'S REMINDERS" in data_block
    assert "No reminders scheduled for today." in data_block


async def test_digest_errors_included_in_data_block():
    """Errors from health_logs appear in the Claude prompt."""
    captured: list[dict] = []

    async def capture_call(**kwargs):
        captured.append(kwargs)
        return _mock_claude_response()

    with ExitStack() as stack:
        _enter_common(stack, errors=_SAMPLE_ERRORS)
        mock_client = stack.enter_context(patch("agents.digest._anthropic"))
        mock_client.messages.create = AsyncMock(side_effect=capture_call)

        await send_daily_digest("+17025551234")

    data_block = captured[0]["messages"][0]["content"]
    assert "1 error(s)" in data_block
    assert "scheduler" in data_block


async def test_digest_no_events_still_sends():
    """Digest fires even when there are zero events, tasks, and errors."""
    with ExitStack() as stack:
        mocks = _enter_common(stack)
        mock_client = stack.enter_context(patch("agents.digest._anthropic"))
        mock_client.messages.create = AsyncMock(return_value=_mock_claude_response())

        await send_daily_digest("+17025551234")

    mocks["send"].assert_called_once()


# ─── Error handling ───────────────────────────────────────────────────────────

async def test_digest_send_failure_logs_health_log():
    """Sendblue failure is caught, logged to health_logs, and does not raise."""
    with ExitStack() as stack:
        _enter_common(stack)
        mock_client = stack.enter_context(patch("agents.digest._anthropic"))
        mock_client.messages.create = AsyncMock(return_value=_mock_claude_response())
        # Override the send_message mock to raise
        stack.enter_context(patch("agents.digest.send_message", new_callable=AsyncMock, side_effect=Exception("Sendblue down")))
        mock_hl = stack.enter_context(patch("agents.digest.db_health_logs.create", return_value={"id": "log-1"}))

        await send_daily_digest("+17025551234")

    mock_hl.assert_called_once()
    log_arg = mock_hl.call_args[0][0]
    assert log_arg.service == "digest"
    assert log_arg.event_type == "error"


async def test_digest_claude_failure_logs_health_log():
    """Claude API failure is caught and logged — digest does not raise."""
    with ExitStack() as stack:
        _enter_common(stack)
        mock_client = stack.enter_context(patch("agents.digest._anthropic"))
        mock_client.messages.create = AsyncMock(side_effect=Exception("API timeout"))
        mock_hl = stack.enter_context(patch("agents.digest.db_health_logs.create", return_value={"id": "log-1"}))

        await send_daily_digest("+17025551234")

    mock_hl.assert_called_once()
    assert "digest" in mock_hl.call_args[0][0].service
