"""
Unit tests for the chat agent (Claude reply loop + conversation memory + tool use).

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


def _mock_text_response(
    text: str = "Here's your focus for today.",
    input_tokens: int = 12,
    output_tokens: int = 24,
) -> MagicMock:
    """Simulate a normal end_turn text response from Claude."""
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [MagicMock(text=text)]
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    return response


def _mock_tool_use_response(
    tool_name: str = "set_reminder",
    tool_input: dict | None = None,
    input_tokens: int = 20,
    output_tokens: int = 10,
) -> MagicMock:
    """Simulate a tool_use response from Claude."""
    if tool_input is None:
        tool_input = {
            "scheduled_at": "2026-04-01T15:00:00",
            "reminder_text": "call Marcus",
        }
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = tool_name
    tool_block.input = tool_input

    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [tool_block]
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    return response


_HISTORY_ONE_TURN = [
    {"role": "user", "content": "Hey, what's my focus for today?", "created_at": "2026-04-01T10:00:00Z"},
]


# ─── Basic happy path ─────────────────────────────────────────────────────────

@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=_HISTORY_ONE_TURN)
async def test_happy_path(mock_history, mock_append, mock_callback, mock_send, mock_action):
    """Claude returns end_turn text — reply sent, ChatResult returned."""
    mock_claude = AsyncMock(return_value=_mock_text_response())

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


# ─── Tools are passed to Claude ───────────────────────────────────────────────

@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=_HISTORY_ONE_TURN)
async def test_tools_list_passed_to_claude(
    mock_history, mock_append, mock_callback, mock_send, mock_action
):
    """Claude API call always includes the tools list from the registry."""
    mock_claude = AsyncMock(return_value=_mock_text_response())

    with patch("agents.chat._anthropic") as mock_client:
        mock_client.messages.create = mock_claude
        await run(_parsed())

    call_kwargs = mock_claude.call_args.kwargs
    assert "tools" in call_kwargs
    assert isinstance(call_kwargs["tools"], list)
    assert len(call_kwargs["tools"]) >= 1
    tool_names = [t["name"] for t in call_kwargs["tools"]]
    assert "set_reminder" in tool_names


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


# ─── Conversation history ordering ────────────────────────────────────────────

@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=[])
async def test_empty_history_sends_only_current_message(
    mock_history, mock_append, mock_callback, mock_send, mock_action
):
    """Empty history (get_recent returns []) still produces a valid Claude call."""
    mock_claude = AsyncMock(return_value=_mock_text_response())

    with patch("agents.chat._anthropic") as mock_client:
        mock_client.messages.create = mock_claude
        result = await run(_parsed())

    assert isinstance(result, ChatResult)
    messages = mock_claude.call_args.kwargs["messages"]
    assert isinstance(messages, list)


@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=[
    {"role": "user",      "content": "first message",  "created_at": "2026-04-01T09:00:00Z"},
    {"role": "assistant", "content": "first reply",    "created_at": "2026-04-01T09:00:01Z"},
    {"role": "user",      "content": "second message", "created_at": "2026-04-01T09:01:00Z"},
    {"role": "assistant", "content": "second reply",   "created_at": "2026-04-01T09:01:01Z"},
    {"role": "user",      "content": "Hey, what's my focus for today?", "created_at": "2026-04-01T10:00:00Z"},
])
async def test_history_passed_to_claude_in_chronological_order(
    mock_history, mock_append, mock_callback, mock_send, mock_action
):
    """
    History is passed to Claude oldest-first (chronological order).
    get_recent returns rows already reversed by db layer — verify order is preserved.
    """
    mock_claude = AsyncMock(return_value=_mock_text_response())

    with patch("agents.chat._anthropic") as mock_client:
        mock_client.messages.create = mock_claude
        await run(_parsed())

    messages = mock_claude.call_args.kwargs["messages"]
    assert len(messages) == 5

    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant", "user", "assistant", "user"]

    assert messages[0]["content"] == "first message"
    assert messages[1]["content"] == "first reply"
    assert messages[4]["content"] == "Hey, what's my focus for today?"


# ─── Turn persistence ─────────────────────────────────────────────────────────

@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.get_recent", return_value=_HISTORY_ONE_TURN)
async def test_both_turns_persisted(mock_history, mock_callback, mock_send, mock_action):
    """User turn saved before Claude call; assistant turn saved after reply is sent."""
    mock_claude = AsyncMock(return_value=_mock_text_response(text="Ship Slice 9."))

    with (
        patch("agents.chat._anthropic") as mock_client,
        patch("agents.chat.db_chat.append", return_value={"id": "msg-1"}) as mock_append,
    ):
        mock_client.messages.create = mock_claude
        await run(_parsed())

    assert mock_append.call_count == 2
    assert mock_append.call_args_list[0] == call("+17025551234", "user", "Hey, what's my focus for today?")
    assert mock_append.call_args_list[1] == call("+17025551234", "assistant", "Ship Slice 9.")


@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.get_recent", return_value=_HISTORY_ONE_TURN)
async def test_user_turn_persisted_before_claude_call(
    mock_history, mock_callback, mock_send, mock_action
):
    """User turn is appended before the Claude API is invoked."""
    call_order: list[str] = []

    async def fake_claude(**kwargs):
        call_order.append("claude")
        return _mock_text_response()

    def fake_append(phone, role, content):
        call_order.append(f"append:{role}")
        return {"id": "msg-1"}

    with (
        patch("agents.chat._anthropic") as mock_client,
        patch("agents.chat.db_chat.append", side_effect=fake_append),
    ):
        mock_client.messages.create = AsyncMock(side_effect=fake_claude)
        await run(_parsed())

    assert call_order.index("append:user") < call_order.index("claude")
    assert call_order.index("claude") < call_order.index("append:assistant")


# ─── Tool use — reminder path ─────────────────────────────────────────────────

@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=_HISTORY_ONE_TURN)
@patch("agents.tools.set_reminder.db_tasks.create", return_value={"id": "task-1"})
async def test_reminder_intent_creates_task_and_sends_ack(
    mock_task_create, mock_history, mock_append, mock_callback, mock_send, mock_action
):
    """
    When Claude returns tool_use for set_reminder:
    - scheduled_task row is created
    - ack text is sent via Sendblue
    - ChatResult is returned with the ack as reply
    """
    mock_claude = AsyncMock(return_value=_mock_tool_use_response(
        tool_input={"scheduled_at": "2026-04-01T15:00:00", "reminder_text": "call Marcus"}
    ))

    with patch("agents.chat._anthropic") as mock_client:
        mock_client.messages.create = mock_claude
        result = await run(_parsed(body="remind me at 3pm to call Marcus"))

    assert isinstance(result, ChatResult)
    assert "3:00 PM PT" in result.reply
    mock_task_create.assert_called_once()
    mock_send.assert_called_once()
    assert "3:00 PM PT" in mock_send.call_args.kwargs["content"]


@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.get_recent", return_value=_HISTORY_ONE_TURN)
@patch("agents.tools.set_reminder.db_tasks.create", return_value={"id": "task-1"})
async def test_tool_use_ack_saved_as_assistant_turn(
    mock_task_create, mock_history, mock_callback, mock_send, mock_action
):
    """Ack text from tool handler is persisted as the assistant turn in history."""
    mock_claude = AsyncMock(return_value=_mock_tool_use_response(
        tool_input={"scheduled_at": "2026-04-01T15:00:00", "reminder_text": "call Marcus"}
    ))

    with (
        patch("agents.chat._anthropic") as mock_client,
        patch("agents.chat.db_chat.append", return_value={"id": "msg-1"}) as mock_append,
    ):
        mock_client.messages.create = mock_claude
        await run(_parsed(body="remind me at 3pm to call Marcus"))

    # Second append call is the assistant turn with the ack text
    assert mock_append.call_count == 2
    assistant_call = mock_append.call_args_list[1]
    role = assistant_call[0][1]
    content = assistant_call[0][2]
    assert role == "assistant"
    assert "3:00 PM PT" in content


@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=_HISTORY_ONE_TURN)
async def test_normal_message_uses_end_turn_path(
    mock_history, mock_append, mock_callback, mock_send, mock_action
):
    """A normal (non-reminder) message still gets a full text reply via end_turn path."""
    mock_claude = AsyncMock(return_value=_mock_text_response(text="Focus on Slice 9 today."))

    with patch("agents.chat._anthropic") as mock_client:
        mock_client.messages.create = mock_claude
        result = await run(_parsed())

    assert result.reply == "Focus on Slice 9 today."
    mock_send.assert_called_once_with(
        to_number="+17025551234",
        content="Focus on Slice 9 today.",
        status_callback=None,
    )


@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=_HISTORY_ONE_TURN)
@patch("agents.tools.set_reminder.db_tasks.create", return_value={"id": "task-1"})
async def test_tool_use_action_type_is_tool_use(
    mock_task_create, mock_history, mock_append, mock_callback, mock_send, mock_action
):
    """Action row for tool_use path uses action_type='tool_use', not 'send_reply'."""
    mock_claude = AsyncMock(return_value=_mock_tool_use_response())

    with patch("agents.chat._anthropic") as mock_client:
        mock_client.messages.create = mock_claude
        await run(_parsed(body="remind me at 3pm to call Marcus"))

    action_arg = mock_action.call_args[0][0]
    assert action_arg.action_type == "tool_use"


# ─── Observability ────────────────────────────────────────────────────────────

@patch("agents.chat.db_actions.create", return_value={"id": "action-1"})
@patch("agents.chat.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"})
@patch("agents.chat.build_status_callback_url", return_value=None)
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=_HISTORY_ONE_TURN)
async def test_action_row_written_with_token_counts(
    mock_history, mock_append, mock_callback, mock_send, mock_action
):
    """Action row is written with correct agent, token counts, and non-zero cost."""
    mock_claude = AsyncMock(
        return_value=_mock_text_response(input_tokens=50, output_tokens=100)
    )

    with patch("agents.chat._anthropic") as mock_client:
        mock_client.messages.create = mock_claude
        await run(_parsed())

    mock_action.assert_called_once()
    action_arg = mock_action.call_args[0][0]
    assert action_arg.agent == "chat"
    assert action_arg.token_input == 50
    assert action_arg.token_output == 100
    assert action_arg.usd_cost > Decimal("0")


# ─── Error handling ───────────────────────────────────────────────────────────

@patch("agents.chat.db_health_logs.create", return_value={"id": "log-1"})
@patch("agents.chat.db_chat.append", return_value={"id": "msg-1"})
@patch("agents.chat.db_chat.get_recent", return_value=_HISTORY_ONE_TURN)
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
@patch("agents.chat.db_chat.get_recent", return_value=_HISTORY_ONE_TURN)
async def test_db_append_failure_does_not_crash_reply(
    mock_history, mock_callback, mock_send, mock_action
):
    """DB failures on append are logged but never propagate — reply still goes out."""
    mock_claude = AsyncMock(return_value=_mock_text_response())

    with (
        patch("agents.chat._anthropic") as mock_client,
        patch("agents.chat.db_chat.append", side_effect=Exception("DB connection lost")),
    ):
        mock_client.messages.create = mock_claude
        result = await run(_parsed())

    assert isinstance(result, ChatResult)
    assert result.reply == "Here's your focus for today."
    mock_send.assert_called_once()
