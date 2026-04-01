"""
Unit tests for the chat agent (Claude reply loop + conversation memory).

Claude API, Sendblue send, and all DB calls are mocked — no real HTTP or DB.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from agents.chat import ChatResult, run
from connectors.base import ParsedInbound


# ─── Helpers ──────────────────────────────────────────────────────────────────

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


def _mock_anthropic_response(
    text: str = "Here's your focus for today.",
    input_tokens: int = 12,
    output_tokens: int = 24,
):
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    return response


def _patch_all(
    *,
    history: list[dict] | None = None,
    claude_text: str = "Here's your focus for today.",
    claude_input: int = 12,
    claude_output: int = 24,
):
    """
    Returns a context manager stack that patches all external calls.
    Callers can grab individual mock refs from the returned dict via the
    context manager's __enter__ values — but since we use @patch decorators
    in individual tests, this helper is mainly for constructing common defaults.
    """


# ─── Basic happy path ─────────────────────────────────────────────────────────

@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=[
    {"role": "user", "content": "Hey, what's my focus for today?", "created_at": "2026-03-31T10:00:00Z"},
])
async def test_happy_path(mock_history, mock_append, mock_callback, mock_send, mock_action):
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
    mock_send.assert_called_once_with(
        to_number="+17025551234",
        content="Here's your focus for today.",
        status_callback=None,
    )


# ─── Empty body guard ────────────────────────────────────────────────────────

async def test_none_body_returns_none_without_calling_claude():
    """Empty body short-circuits before any Claude, Sendblue, or DB call."""
    with (
        patch("agents.chat._anthropic") as mock_client,
        patch("agents.chat.send_message", new_callable=AsyncMock) as mock_send,
        patch("agents.chat.db_chat.append") as mock_append,
        patch("agents.chat.db_chat.get_recent") as mock_history,
    ):
        result = await run(_parsed(body=None))

    assert result is None
    mock_client.messages.create.assert_not_called()
    mock_send.assert_not_called()
    mock_append.assert_not_called()
    mock_history.assert_not_called()


# ─── Conversation history ─────────────────────────────────────────────────────

@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=[])
async def test_empty_history_sends_only_current_message(
    mock_history, mock_append, mock_callback, mock_send, mock_action
):
    """When no prior history exists, Claude is called with just the current user turn."""
    mock_claude = AsyncMock(return_value=_mock_anthropic_response())

    with patch("agents.chat._anthropic") as mock_client:
        mock_client.messages.create = mock_claude
        result = await run(_parsed())

    assert isinstance(result, ChatResult)
    call_kwargs = mock_claude.call_args.kwargs
    # Empty history after saving user turn and fetching — edge case where get_recent
    # returns [] should still work (mock returns [] here to simulate that).
    assert isinstance(call_kwargs["messages"], list)


@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=[
    {"role": "user",      "content": "first message",  "created_at": "2026-03-31T09:00:00Z"},
    {"role": "assistant", "content": "first reply",    "created_at": "2026-03-31T09:00:01Z"},
    {"role": "user",      "content": "second message", "created_at": "2026-03-31T09:01:00Z"},
    {"role": "assistant", "content": "second reply",   "created_at": "2026-03-31T09:01:01Z"},
    {"role": "user",      "content": "Hey, what's my focus for today?", "created_at": "2026-03-31T10:00:00Z"},
])
async def test_history_passed_to_claude_in_chronological_order(
    mock_history, mock_append, mock_callback, mock_send, mock_action
):
    """
    History is passed to Claude oldest-first (chronological).
    get_recent returns rows already in chronological order after reversal in db layer.
    Verify the messages list passed to Claude preserves that order.
    """
    mock_claude = AsyncMock(return_value=_mock_anthropic_response())

    with patch("agents.chat._anthropic") as mock_client:
        mock_client.messages.create = mock_claude
        await run(_parsed())

    messages_sent = mock_claude.call_args.kwargs["messages"]
    assert len(messages_sent) == 5

    # Roles must alternate user/assistant/user/assistant/user
    roles = [m["role"] for m in messages_sent]
    assert roles == ["user", "assistant", "user", "assistant", "user"]

    # Content must be in chronological order (oldest first)
    assert messages_sent[0]["content"] == "first message"
    assert messages_sent[1]["content"] == "first reply"
    assert messages_sent[4]["content"] == "Hey, what's my focus for today?"


# ─── Turn persistence ─────────────────────────────────────────────────────────

@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.get_recent", return_value=[
    {"role": "user", "content": "Hey, what's my focus for today?", "created_at": "2026-03-31T10:00:00Z"},
])
async def test_both_turns_persisted(mock_history, mock_callback, mock_send, mock_action):
    """
    User turn is saved before Claude call; assistant turn is saved after reply is sent.
    Both append calls must fire with correct role and content.
    """
    mock_claude = AsyncMock(return_value=_mock_anthropic_response(text="Ship Slice 8."))

    with (
        patch("agents.chat._anthropic") as mock_client,
        patch("agents.chat.db_chat.append", return_value={"id": "msg-1"}) as mock_append,
    ):
        mock_client.messages.create = mock_claude
        await run(_parsed())

    assert mock_append.call_count == 2

    user_call = mock_append.call_args_list[0]
    assert user_call == call("+17025551234", "user", "Hey, what's my focus for today?")

    assistant_call = mock_append.call_args_list[1]
    assert assistant_call == call("+17025551234", "assistant", "Ship Slice 8.")


@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.get_recent", return_value=[
    {"role": "user", "content": "Hey, what's my focus for today?", "created_at": "2026-03-31T10:00:00Z"},
])
async def test_user_turn_persisted_before_claude_call(
    mock_history, mock_callback, mock_send, mock_action
):
    """
    User turn is appended before the Claude API is invoked.
    This ensures we don't lose the user message if generation fails.
    """
    call_order: list[str] = []

    async def fake_claude(**kwargs):
        call_order.append("claude")
        return _mock_anthropic_response()

    def fake_append(phone, role, content):
        call_order.append(f"append:{role}")
        return {"id": "msg-1"}

    with (
        patch("agents.chat._anthropic") as mock_client,
        patch("agents.chat.db_chat.append", side_effect=fake_append),
    ):
        mock_client.messages.create = AsyncMock(side_effect=fake_claude)
        await run(_parsed())

    # user turn must be saved before Claude is called
    assert call_order.index("append:user") < call_order.index("claude")
    # assistant turn must be saved after Claude is called
    assert call_order.index("claude") < call_order.index("append:assistant")


# ─── Observability ───────────────────────────────────────────────────────────

@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=[
    {"role": "user", "content": "Hey, what's my focus for today?", "created_at": "2026-03-31T10:00:00Z"},
])
async def test_action_row_written_with_token_counts(
    mock_history, mock_append, mock_callback, mock_send, mock_action
):
    """Action row is written with correct agent, token counts, and non-zero cost."""
    mock_claude = AsyncMock(
        return_value=_mock_anthropic_response(input_tokens=50, output_tokens=100)
    )

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


# ─── Error handling ───────────────────────────────────────────────────────────

@patch("agents.chat.db_health_logs.create", return_value={"id": "log-1"})
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=[
    {"role": "user", "content": "Hey, what's my focus for today?", "created_at": "2026-03-31T10:00:00Z"},
])
async def test_claude_failure_returns_none_and_logs_error(mock_history, mock_append, mock_hl):
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


@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.get_recent", return_value=[
    {"role": "user", "content": "Hey, what's my focus for today?", "created_at": "2026-03-31T10:00:00Z"},
])
async def test_db_append_failure_does_not_crash_reply(
    mock_history, mock_callback, mock_send, mock_action
):
    """
    DB failures on append are logged but never propagate.
    The reply is still sent and ChatResult is returned.
    """
    mock_claude = AsyncMock(return_value=_mock_anthropic_response())

    with (
        patch("agents.chat._anthropic") as mock_client,
        patch("agents.chat.db_chat.append", side_effect=Exception("DB connection lost")),
    ):
        mock_client.messages.create = mock_claude
        result = await run(_parsed())

    # Reply must still go out despite both append calls failing
    assert isinstance(result, ChatResult)
    assert result.reply == "Here's your focus for today."
    mock_send.assert_called_once()
