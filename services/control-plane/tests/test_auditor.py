"""
Unit tests for the Auditor agent.

All external dependencies (Slack, DB) are mocked — no real network or DB calls.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from agents import auditor


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_settings(
    slack_bot_token="xoxb-test",
    slack_ops_channel_id="C12345",
    cost_alert_threshold_cents=500,
    auditor_error_rate_threshold=5,
    auditor_stale_approval_minutes=60,
):
    mock = MagicMock()
    mock.slack_bot_token = slack_bot_token
    mock.slack_ops_channel_id = slack_ops_channel_id
    mock.cost_alert_threshold_cents = cost_alert_threshold_cents
    mock.auditor_error_rate_threshold = auditor_error_rate_threshold
    mock.auditor_stale_approval_minutes = auditor_stale_approval_minutes
    return mock


_LAST_CHECK = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)


# ─── run_audit orchestration tests ────────────────────────────────────────────

async def test_run_audit_skips_when_slack_not_configured():
    """run_audit is a no-op when Slack credentials are missing."""
    mock_check_cost = AsyncMock()

    with (
        patch("agents.auditor.settings", _make_settings(slack_bot_token=None)),
        patch("agents.auditor._check_cost", mock_check_cost),
    ):
        await auditor.run_audit(last_check=_LAST_CHECK)

    mock_check_cost.assert_not_awaited()


async def test_run_audit_calls_all_three_checks():
    """run_audit calls _check_cost, _check_errors, and _check_stale_approvals."""
    mock_cost = AsyncMock()
    mock_errors = AsyncMock()
    mock_stale = AsyncMock()

    with (
        patch("agents.auditor.settings", _make_settings()),
        patch("agents.auditor._check_cost", mock_cost),
        patch("agents.auditor._check_errors", mock_errors),
        patch("agents.auditor._check_stale_approvals", mock_stale),
    ):
        await auditor.run_audit(last_check=_LAST_CHECK)

    mock_cost.assert_awaited_once()
    mock_errors.assert_awaited_once_with(_LAST_CHECK)
    mock_stale.assert_awaited_once()


# ─── _check_cost tests ────────────────────────────────────────────────────────

async def test_check_cost_no_alert_when_under_threshold():
    """_check_cost does not post to Slack when cost is below threshold."""
    mock_slack = MagicMock()
    mock_db_cost = MagicMock(return_value=2.0)  # $2.00 < $5.00 threshold

    with (
        patch("agents.auditor.settings", _make_settings(cost_alert_threshold_cents=500)),
        patch("agents.auditor.db_audit.cost_since_midnight_utc", mock_db_cost),
        patch("agents.auditor.slack_connector.post_message", mock_slack),
    ):
        await auditor._check_cost()

    mock_slack.assert_not_called()


async def test_check_cost_posts_alert_when_over_threshold():
    """_check_cost posts a Slack alert when daily cost exceeds threshold."""
    mock_slack = MagicMock()
    mock_db_cost = MagicMock(return_value=7.50)  # $7.50 > $5.00 threshold
    mock_action_create = MagicMock()

    with (
        patch("agents.auditor.settings", _make_settings(cost_alert_threshold_cents=500)),
        patch("agents.auditor.db_audit.cost_since_midnight_utc", mock_db_cost),
        patch("agents.auditor.slack_connector.post_message", mock_slack),
        patch("agents.auditor.db_actions.create", mock_action_create),
    ):
        await auditor._check_cost()

    mock_slack.assert_called_once()
    call_kwargs = mock_slack.call_args.kwargs
    assert "C12345" == call_kwargs["channel"]
    assert "$7.50" in call_kwargs["text"]

    mock_action_create.assert_called_once()
    action = mock_action_create.call_args.args[0]
    assert action.action_type == "auditor_cost_alert"
    assert action.agent == "auditor"


async def test_check_cost_disabled_when_threshold_zero():
    """_check_cost is a no-op when cost_alert_threshold_cents=0."""
    mock_db_cost = MagicMock(return_value=100.0)
    mock_slack = MagicMock()

    with (
        patch("agents.auditor.settings", _make_settings(cost_alert_threshold_cents=0)),
        patch("agents.auditor.db_audit.cost_since_midnight_utc", mock_db_cost),
        patch("agents.auditor.slack_connector.post_message", mock_slack),
    ):
        await auditor._check_cost()

    mock_db_cost.assert_not_called()
    mock_slack.assert_not_called()


async def test_check_cost_handles_db_error_gracefully():
    """_check_cost catches DB errors and writes a health_log."""
    mock_db_cost = MagicMock(side_effect=Exception("DB unavailable"))
    mock_health_create = MagicMock()
    mock_slack = MagicMock()

    with (
        patch("agents.auditor.settings", _make_settings(cost_alert_threshold_cents=500)),
        patch("agents.auditor.db_audit.cost_since_midnight_utc", mock_db_cost),
        patch("agents.auditor.slack_connector.post_message", mock_slack),
        patch("agents.auditor.db_health_logs.create", mock_health_create),
    ):
        # Should not raise
        await auditor._check_cost()

    mock_slack.assert_not_called()
    mock_health_create.assert_called_once()
    log = mock_health_create.call_args.args[0]
    assert log.event_type == "error"
    assert "cost check failed" in log.message


# ─── _check_errors tests ──────────────────────────────────────────────────────

async def test_check_errors_no_alert_when_under_threshold():
    """_check_errors does not post when error count is below threshold."""
    mock_slack = MagicMock()
    mock_db_errors = MagicMock(return_value=[{"service": "gk", "message": "e1"}] * 3)

    with (
        patch("agents.auditor.settings", _make_settings(auditor_error_rate_threshold=5)),
        patch("agents.auditor.db_audit.error_rows", mock_db_errors),
        patch("agents.auditor.slack_connector.post_message", mock_slack),
    ):
        await auditor._check_errors(_LAST_CHECK)

    mock_slack.assert_not_called()


async def test_check_errors_posts_alert_when_over_threshold():
    """_check_errors posts a Slack alert when errors >= threshold."""
    errors = [{"service": "scheduler", "message": f"err {i}", "created_at": "2026-04-01T12:00:00"} for i in range(6)]
    mock_slack = MagicMock()
    mock_db_errors = MagicMock(return_value=errors)
    mock_action_create = MagicMock()

    with (
        patch("agents.auditor.settings", _make_settings(auditor_error_rate_threshold=5)),
        patch("agents.auditor.db_audit.error_rows", mock_db_errors),
        patch("agents.auditor.slack_connector.post_message", mock_slack),
        patch("agents.auditor.db_actions.create", mock_action_create),
    ):
        await auditor._check_errors(_LAST_CHECK)

    mock_slack.assert_called_once()
    call_kwargs = mock_slack.call_args.kwargs
    assert "6 errors" in call_kwargs["text"]

    action = mock_action_create.call_args.args[0]
    assert action.action_type == "auditor_error_alert"
    assert action.payload["error_count"] == 6


async def test_check_errors_disabled_when_threshold_zero():
    """_check_errors is a no-op when auditor_error_rate_threshold=0."""
    mock_db_errors = MagicMock(return_value=[])
    mock_slack = MagicMock()

    with (
        patch("agents.auditor.settings", _make_settings(auditor_error_rate_threshold=0)),
        patch("agents.auditor.db_audit.error_rows", mock_db_errors),
        patch("agents.auditor.slack_connector.post_message", mock_slack),
    ):
        await auditor._check_errors(_LAST_CHECK)

    mock_db_errors.assert_not_called()
    mock_slack.assert_not_called()


# ─── _check_stale_approvals tests ─────────────────────────────────────────────

async def test_check_stale_approvals_no_alert_when_none():
    """_check_stale_approvals does not post when there are no stale approvals."""
    mock_slack = MagicMock()
    mock_db_stale = MagicMock(return_value=[])

    with (
        patch("agents.auditor.settings", _make_settings(auditor_stale_approval_minutes=60)),
        patch("agents.auditor.db_audit.stale_approvals", mock_db_stale),
        patch("agents.auditor.slack_connector.post_message", mock_slack),
    ):
        await auditor._check_stale_approvals()

    mock_slack.assert_not_called()


async def test_check_stale_approvals_posts_alert_when_stale():
    """_check_stale_approvals posts a Slack alert for stale approvals."""
    stale = [
        {"id": "appr-1", "summary": "Send email to Jake", "created_at": "2026-04-01T10:00:00Z"},
        {"id": "appr-2", "summary": "Reply to lead", "created_at": "2026-04-01T09:00:00Z"},
    ]
    mock_slack = MagicMock()
    mock_db_stale = MagicMock(return_value=stale)
    mock_action_create = MagicMock()

    with (
        patch("agents.auditor.settings", _make_settings(auditor_stale_approval_minutes=60)),
        patch("agents.auditor.db_audit.stale_approvals", mock_db_stale),
        patch("agents.auditor.slack_connector.post_message", mock_slack),
        patch("agents.auditor.db_actions.create", mock_action_create),
    ):
        await auditor._check_stale_approvals()

    mock_slack.assert_called_once()
    call_kwargs = mock_slack.call_args.kwargs
    assert "2 stale" in call_kwargs["text"]

    action = mock_action_create.call_args.args[0]
    assert action.action_type == "auditor_stale_approval_alert"
    assert action.payload["stale_count"] == 2


async def test_check_stale_approvals_disabled_when_minutes_zero():
    """_check_stale_approvals is a no-op when auditor_stale_approval_minutes=0."""
    mock_db_stale = MagicMock()
    mock_slack = MagicMock()

    with (
        patch("agents.auditor.settings", _make_settings(auditor_stale_approval_minutes=0)),
        patch("agents.auditor.db_audit.stale_approvals", mock_db_stale),
        patch("agents.auditor.slack_connector.post_message", mock_slack),
    ):
        await auditor._check_stale_approvals()

    mock_db_stale.assert_not_called()
    mock_slack.assert_not_called()


async def test_check_stale_approvals_handles_error_gracefully():
    """_check_stale_approvals catches errors and writes a health_log."""
    mock_db_stale = MagicMock(side_effect=Exception("Timeout"))
    mock_health_create = MagicMock()

    with (
        patch("agents.auditor.settings", _make_settings(auditor_stale_approval_minutes=60)),
        patch("agents.auditor.db_audit.stale_approvals", mock_db_stale),
        patch("agents.auditor.slack_connector.post_message", MagicMock()),
        patch("agents.auditor.db_health_logs.create", mock_health_create),
    ):
        await auditor._check_stale_approvals()

    mock_health_create.assert_called_once()
    log = mock_health_create.call_args.args[0]
    assert log.event_type == "error"
    assert "stale approval check failed" in log.message
