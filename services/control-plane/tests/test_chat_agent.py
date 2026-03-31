"""
Unit tests for the chat agent (Claude reply loop).
Claude API and Sendblue send are mocked — no real HTTP calls, no DB.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.chat import ChatResult, run
from connectors.base import ParsedInbound


def _parsed(**kwargs) -> ParsedInbound:
    defaults = {
        "source": "imessage",
        "source_id": "msg_handle_abc123",
        "raw_payload": {},
        "sender_name": None,
        "sender_email": None,
        "sender_phone": "+17025551234",
        "subject": None,
        "body": "Hey, what's my focus for today?",
        "received_at": None,
    }
    defaults.update(kwargs)
    return ParsedInbound(**defaults)


def _mock_anthropic_response(text: str = "Here's your focus for today.", input_tokens: int = 12, output_tokens: int = 24):
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    return response


@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
async def test_happy_path(mock_callback, mock_send, mock_action):
    """Claude is called, reply is sent, ChatResult returned."""
    mock_claude = AsyncMock(return_value=_mock_anthropic_response())

    with patch("agents.chat._anthropic") as mock_client:
        mock_client.messages.create = mock_claude
        result = await run(_parsed())

    assert isinstance(result, ChatResult)
    assert result.reply == "Here's your focus for today."
    assert result.token_input == 12
    assert result.token_output == 24
    assert result.duration_ms >= 0
    mock_claude.assert_called_once()
    mock_send.assert_called_once_with(
        to_number="+17025551234",
        content="Here's your focus for today.",
        status_callback=None,
    )


@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
async def test_reply_content_matches_claude_response(mock_callback, mock_send, mock_action):
    """The reply text sent to Sendblue is exactly what Claude returned."""
    expected_reply = "Focus on shipping Slice 7 today."
    mock_claude = AsyncMock(return_value=_mock_anthropic_response(text=expected_reply))

    with patch("agents.chat._anthropic") as mock_client:
        mock_client.messages.create = mock_claude
        result = await run(_parsed())

    assert result.reply == expected_reply
    assert mock_send.call_args.kwargs["content"] == expected_reply


@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
async def test_action_row_written_with_token_counts(mock_callback, mock_send, mock_action):
    """Action row is written with correct agent, token counts, and non-zero cost."""
    mock_claude = AsyncMock(return_value=_mock_anthropic_response(input_tokens=50, output_tokens=100))

    with patch("agents.chat._anthropic") as mock_client:
        mock_client.messages.create = mock_claude
        await run(_parsed())

    mock_action.assert_called_once()
    action_arg = mock_action.call_args[0][0]
    assert action_arg.agent == "chat"
    assert action_arg.action_type == "send_reply"
    assert action_arg.token_input == 50
    assert action_arg.token_output == 100
    assert action_arg.usd_cost > Decimal("0")


async def test_none_body_returns_none_without_calling_claude():
    """Empty body short-circuits before any Claude or Sendblue call."""
    with (
        patch("agents.chat._anthropic") as mock_client,
        patch("agents.chat.send_message", new_callable=AsyncMock) as mock_send,
    ):
        result = await run(_parsed(body=None))

    assert result is None
    mock_client.messages.create.assert_not_called()
    mock_send.assert_not_called()


@patch("agents.chat.db_health_logs.create", return_value={"id": "log-1"})
async def test_claude_failure_returns_none_and_logs_error(mock_hl):
    """Claude API failure is caught, logged to health_logs, and returns None."""
    mock_claude = AsyncMock(side_effect=Exception("API connection timeout"))

    with (
        patch("agents.chat._anthropic") as mock_client,
        patch("agents.chat.send_message", new_callable=AsyncMock) as mock_send,
    ):
        mock_client.messages.create = mock_claude
        result = await run(_parsed())

    assert result is None
    mock_send.assert_not_called()
    mock_hl.assert_called_once()
    log_arg = mock_hl.call_args[0][0]
    assert log_arg.event_type == "error"
    assert "API connection timeout" in log_arg.message
