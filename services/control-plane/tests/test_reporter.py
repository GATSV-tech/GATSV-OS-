"""
Unit tests for the Reporter agent and report tool.

All external dependencies (Anthropic, Sendblue, Slack, DB) are mocked.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents import reporter


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_anthropic_response(text: str, input_tokens: int = 50, output_tokens: int = 100):
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    return response


_SAMPLE_DIGEST_TEXT = "Ops summary: 5 events, $0.01 cost, 0 open approvals, no errors."

_DEFAULT_DB_RETURNS = {
    "event_counts_by_bucket": {"sales": 3, "support": 2},
    "event_counts_by_status": {"received": 4, "actioned": 1},
    "action_counts": {"send_ack": 2, "daily_digest": 1},
    "cost_totals": {"token_input": 500, "token_output": 200, "usd_cost": 0.0045},
    "open_approvals": [],
    "error_count": 0,
    "recent_errors": [],
}


def _patch_db(**overrides):
    """Return a dict of db patches, optionally overriding specific query results."""
    patches = {**_DEFAULT_DB_RETURNS, **overrides}
    return patches


# ─── generate_digest tests ────────────────────────────────────────────────────

async def test_generate_digest_returns_text():
    """generate_digest returns the Claude-generated plain text."""
    db = _patch_db()
    mock_response = _make_anthropic_response(_SAMPLE_DIGEST_TEXT)
    mock_create = AsyncMock(return_value=mock_response)

    with (
        patch("agents.reporter._anthropic") as mock_anthropic,
        patch("agents.reporter.db_reporter.event_counts_by_bucket", MagicMock(return_value=db["event_counts_by_bucket"])),
        patch("agents.reporter.db_reporter.event_counts_by_status", MagicMock(return_value=db["event_counts_by_status"])),
        patch("agents.reporter.db_reporter.action_counts", MagicMock(return_value=db["action_counts"])),
        patch("agents.reporter.db_reporter.cost_totals", MagicMock(return_value=db["cost_totals"])),
        patch("agents.reporter.db_reporter.open_approvals", MagicMock(return_value=db["open_approvals"])),
        patch("agents.reporter.db_reporter.error_count", MagicMock(return_value=db["error_count"])),
        patch("agents.reporter.db_reporter.recent_errors", MagicMock(return_value=db["recent_errors"])),
    ):
        mock_anthropic.messages.create = mock_create
        result = await reporter.generate_digest(window_hours=24)

    assert result == _SAMPLE_DIGEST_TEXT
    mock_create.assert_awaited_once()


async def test_generate_digest_passes_window_hours_to_data_block():
    """The data block passed to Claude mentions the correct window hours."""
    mock_response = _make_anthropic_response("ok")
    mock_create = AsyncMock(return_value=mock_response)

    db = _patch_db()
    with (
        patch("agents.reporter._anthropic") as mock_anthropic,
        patch("agents.reporter.db_reporter.event_counts_by_bucket", MagicMock(return_value=db["event_counts_by_bucket"])),
        patch("agents.reporter.db_reporter.event_counts_by_status", MagicMock(return_value=db["event_counts_by_status"])),
        patch("agents.reporter.db_reporter.action_counts", MagicMock(return_value=db["action_counts"])),
        patch("agents.reporter.db_reporter.cost_totals", MagicMock(return_value=db["cost_totals"])),
        patch("agents.reporter.db_reporter.open_approvals", MagicMock(return_value=db["open_approvals"])),
        patch("agents.reporter.db_reporter.error_count", MagicMock(return_value=db["error_count"])),
        patch("agents.reporter.db_reporter.recent_errors", MagicMock(return_value=db["recent_errors"])),
    ):
        mock_anthropic.messages.create = mock_create
        await reporter.generate_digest(window_hours=168)

    call_kwargs = mock_create.call_args.kwargs
    user_content = call_kwargs["messages"][0]["content"]
    assert "168h" in user_content


async def test_generate_digest_propagates_anthropic_error():
    """generate_digest raises if the Anthropic call fails."""
    db = _patch_db()
    with (
        patch("agents.reporter._anthropic") as mock_anthropic,
        patch("agents.reporter.db_reporter.event_counts_by_bucket", MagicMock(return_value=db["event_counts_by_bucket"])),
        patch("agents.reporter.db_reporter.event_counts_by_status", MagicMock(return_value=db["event_counts_by_status"])),
        patch("agents.reporter.db_reporter.action_counts", MagicMock(return_value=db["action_counts"])),
        patch("agents.reporter.db_reporter.cost_totals", MagicMock(return_value=db["cost_totals"])),
        patch("agents.reporter.db_reporter.open_approvals", MagicMock(return_value=db["open_approvals"])),
        patch("agents.reporter.db_reporter.error_count", MagicMock(return_value=db["error_count"])),
        patch("agents.reporter.db_reporter.recent_errors", MagicMock(return_value=db["recent_errors"])),
    ):
        mock_anthropic.messages.create = AsyncMock(side_effect=RuntimeError("API down"))
        with pytest.raises(RuntimeError, match="API down"):
            await reporter.generate_digest(window_hours=24)


# ─── send_digest tests ────────────────────────────────────────────────────────

async def test_send_digest_phone_only():
    """send_digest sends via iMessage and writes an action row when phone is set."""
    db = _patch_db()
    mock_response = _make_anthropic_response(_SAMPLE_DIGEST_TEXT, 50, 100)
    mock_send_imessage = AsyncMock()
    mock_action_create = MagicMock()

    with (
        patch("agents.reporter._anthropic") as mock_anthropic,
        patch("agents.reporter.db_reporter.event_counts_by_bucket", MagicMock(return_value=db["event_counts_by_bucket"])),
        patch("agents.reporter.db_reporter.event_counts_by_status", MagicMock(return_value=db["event_counts_by_status"])),
        patch("agents.reporter.db_reporter.action_counts", MagicMock(return_value=db["action_counts"])),
        patch("agents.reporter.db_reporter.cost_totals", MagicMock(return_value=db["cost_totals"])),
        patch("agents.reporter.db_reporter.open_approvals", MagicMock(return_value=db["open_approvals"])),
        patch("agents.reporter.db_reporter.error_count", MagicMock(return_value=db["error_count"])),
        patch("agents.reporter.db_reporter.recent_errors", MagicMock(return_value=db["recent_errors"])),
        patch("agents.reporter.send_message", mock_send_imessage),
        patch("agents.reporter.db_actions.create", mock_action_create),
        patch("agents.reporter.build_status_callback_url", return_value="http://cb"),
    ):
        mock_anthropic.messages.create = AsyncMock(return_value=mock_response)
        await reporter.send_digest(window_hours=24, phone="+15551234567", slack=False)

    mock_send_imessage.assert_awaited_once()
    call_kwargs = mock_send_imessage.call_args.kwargs
    assert call_kwargs["to_number"] == "+15551234567"
    assert call_kwargs["content"] == _SAMPLE_DIGEST_TEXT
    mock_action_create.assert_called_once()
    action_arg = mock_action_create.call_args.args[0]
    assert action_arg.action_type == "reporter_digest"
    assert action_arg.token_input == 50
    assert action_arg.token_output == 100


async def test_send_digest_slack_only():
    """send_digest posts to Slack when slack=True and Slack is configured."""
    db = _patch_db()
    mock_response = _make_anthropic_response("slack report", 30, 80)
    mock_slack_post = MagicMock()
    mock_action_create = MagicMock()

    with (
        patch("agents.reporter._anthropic") as mock_anthropic,
        patch("agents.reporter.db_reporter.event_counts_by_bucket", MagicMock(return_value=db["event_counts_by_bucket"])),
        patch("agents.reporter.db_reporter.event_counts_by_status", MagicMock(return_value=db["event_counts_by_status"])),
        patch("agents.reporter.db_reporter.action_counts", MagicMock(return_value=db["action_counts"])),
        patch("agents.reporter.db_reporter.cost_totals", MagicMock(return_value=db["cost_totals"])),
        patch("agents.reporter.db_reporter.open_approvals", MagicMock(return_value=db["open_approvals"])),
        patch("agents.reporter.db_reporter.error_count", MagicMock(return_value=db["error_count"])),
        patch("agents.reporter.db_reporter.recent_errors", MagicMock(return_value=db["recent_errors"])),
        patch("agents.reporter.slack_connector.post_message", mock_slack_post),
        patch("agents.reporter.db_actions.create", mock_action_create),
        patch("agents.reporter.settings") as mock_settings,
    ):
        mock_settings.slack_bot_token = "xoxb-test"
        mock_settings.slack_ops_channel_id = "C12345"
        mock_anthropic.messages.create = AsyncMock(return_value=mock_response)
        await reporter.send_digest(window_hours=24, phone=None, slack=True)

    mock_slack_post.assert_called_once()
    call_kwargs = mock_slack_post.call_args.kwargs
    assert call_kwargs["channel"] == "C12345"
    assert call_kwargs["text"] == "slack report"


async def test_send_digest_no_target_is_noop():
    """send_digest with no phone and slack=False does nothing."""
    mock_action_create = MagicMock()

    with (
        patch("agents.reporter.db_actions.create", mock_action_create),
        patch("agents.reporter._anthropic") as mock_anthropic,
    ):
        mock_anthropic.messages.create = AsyncMock()
        await reporter.send_digest(window_hours=24, phone=None, slack=False)

    mock_anthropic.messages.create.assert_not_awaited()
    mock_action_create.assert_not_called()


async def test_send_digest_swallows_errors_and_writes_health_log():
    """send_digest catches errors and writes a health_log entry instead of raising."""
    db = _patch_db()
    mock_health_create = MagicMock()

    with (
        patch("agents.reporter._anthropic") as mock_anthropic,
        patch("agents.reporter.db_reporter.event_counts_by_bucket", MagicMock(side_effect=Exception("DB down"))),
        patch("agents.reporter.db_reporter.event_counts_by_status", MagicMock(return_value=db["event_counts_by_status"])),
        patch("agents.reporter.db_reporter.action_counts", MagicMock(return_value=db["action_counts"])),
        patch("agents.reporter.db_reporter.cost_totals", MagicMock(return_value=db["cost_totals"])),
        patch("agents.reporter.db_reporter.open_approvals", MagicMock(return_value=db["open_approvals"])),
        patch("agents.reporter.db_reporter.error_count", MagicMock(return_value=db["error_count"])),
        patch("agents.reporter.db_reporter.recent_errors", MagicMock(return_value=db["recent_errors"])),
        patch("agents.reporter.db_health_logs.create", mock_health_create),
    ):
        mock_anthropic.messages.create = AsyncMock()
        # Should not raise
        await reporter.send_digest(window_hours=24, phone="+15551234567", slack=False)

    mock_health_create.assert_called_once()
    log_arg = mock_health_create.call_args.args[0]
    assert log_arg.service == "reporter"
    assert log_arg.event_type == "error"


# ─── _build_data_block tests ──────────────────────────────────────────────────

def test_build_data_block_includes_all_sections():
    """_build_data_block output contains all expected section headers."""
    since = datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 4, 2, 0, 0, 0, tzinfo=timezone.utc)

    block = reporter._build_data_block(
        window_hours=24,
        since=since,
        until=until,
        event_by_bucket={"sales": 3},
        event_by_status={"received": 3},
        action_counts={"send_ack": 1},
        costs={"token_input": 100, "token_output": 50, "usd_cost": 0.0012},
        open_approvals=[{"id": "appr-1", "summary": "Send email to lead"}],
        error_count=2,
        recent_errors=[{"service": "scheduler", "message": "Task failed"}],
    )

    assert "EVENTS:" in block
    assert "EVENT STATUS:" in block
    assert "ACTIONS:" in block
    assert "COST:" in block
    assert "OPEN APPROVALS:" in block
    assert "ERRORS" in block
    assert "sales: 3" in block
    assert "$0.0012" in block
    assert "Send email to lead" in block
    assert "[scheduler]" in block


def test_build_data_block_handles_empty_data():
    """_build_data_block renders gracefully with all-empty inputs."""
    since = datetime(2026, 4, 1, tzinfo=timezone.utc)
    until = datetime(2026, 4, 2, tzinfo=timezone.utc)

    block = reporter._build_data_block(
        window_hours=1,
        since=since,
        until=until,
        event_by_bucket={},
        event_by_status={},
        action_counts={},
        costs={"token_input": 0, "token_output": 0, "usd_cost": 0.0},
        open_approvals=[],
        error_count=0,
        recent_errors=[],
    )

    assert "EVENTS: 0" in block
    assert "OPEN APPROVALS: 0" in block
    assert "ERRORS" in block


# ─── report tool tests ────────────────────────────────────────────────────────

async def test_report_tool_returns_digest_text():
    """The report tool handler calls generate_digest and returns the result."""
    from agents.tools.report import _handle
    from agents.tool_registry import ToolContext

    ctx = ToolContext(sender_phone="+15559990000")
    mock_generate = AsyncMock(return_value="Here's your ops report.")

    with patch("agents.tools.report.reporter.generate_digest", mock_generate):
        result = await _handle({"window_hours": 24}, ctx)

    assert result.ack == "Here's your ops report."
    mock_generate.assert_awaited_once_with(window_hours=24)


async def test_report_tool_defaults_to_24h():
    """The report tool defaults to 24h if window_hours is not provided."""
    from agents.tools.report import _handle
    from agents.tool_registry import ToolContext

    ctx = ToolContext(sender_phone="+15559990000")
    mock_generate = AsyncMock(return_value="report")

    with patch("agents.tools.report.reporter.generate_digest", mock_generate):
        await _handle({}, ctx)

    mock_generate.assert_awaited_once_with(window_hours=24)


async def test_report_tool_clamps_window_hours():
    """Window hours is clamped to [1, 168]."""
    from agents.tools.report import _handle
    from agents.tool_registry import ToolContext

    ctx = ToolContext(sender_phone="+15559990000")
    mock_generate = AsyncMock(return_value="report")

    with patch("agents.tools.report.reporter.generate_digest", mock_generate):
        await _handle({"window_hours": 9999}, ctx)

    mock_generate.assert_awaited_once_with(window_hours=168)


async def test_report_tool_returns_error_message_on_failure():
    """The report tool returns an error ack string when generate_digest raises."""
    from agents.tools.report import _handle
    from agents.tool_registry import ToolContext

    ctx = ToolContext(sender_phone="+15559990000")

    with patch("agents.tools.report.reporter.generate_digest", AsyncMock(side_effect=Exception("API down"))):
        result = await _handle({"window_hours": 24}, ctx)

    assert "couldn't generate" in result.ack.lower()
