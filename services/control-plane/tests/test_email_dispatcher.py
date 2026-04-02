"""
Unit tests for the email dispatcher and Postmark connector.

All external dependencies (httpx, db, settings) are mocked — no real
network calls or DB access.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from connectors.postmark_send import PostmarkError, send_email


# ─── Postmark connector tests ─────────────────────────────────────────────────

def _mock_http_client(status_code: int = 200, json_body: dict | None = None, text: str = ""):
    """Build a mock httpx.AsyncClient context manager."""
    mock_response = MagicMock()
    mock_response.is_success = status_code < 400
    mock_response.status_code = status_code
    mock_response.text = text
    mock_response.json = MagicMock(return_value=json_body or {})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


async def test_send_email_success():
    """Successful send returns parsed Postmark response."""
    expected = {"MessageID": "abc-123", "ErrorCode": 0, "Message": "OK"}
    mock_client = _mock_http_client(200, json_body=expected)

    with (
        patch("connectors.postmark_send.httpx.AsyncClient", return_value=mock_client),
        patch("connectors.postmark_send.settings") as mock_settings,
    ):
        mock_settings.postmark_server_token = "tok-test"
        mock_settings.postmark_from_email = None

        result = await send_email(
            to_email="client@example.com",
            subject="Thanks for reaching out",
            text_body="We'll be in touch shortly.",
        )

    assert result == expected
    call_kwargs = mock_client.post.call_args.kwargs
    assert call_kwargs["json"]["To"] == "client@example.com"
    assert call_kwargs["json"]["Subject"] == "Thanks for reaching out"
    assert call_kwargs["headers"]["X-Postmark-Server-Token"] == "tok-test"
    assert "From" not in call_kwargs["json"]  # no from_email configured


async def test_send_email_includes_from_when_configured():
    """From address is included when postmark_from_email is set."""
    mock_client = _mock_http_client(200, json_body={"MessageID": "x"})

    with (
        patch("connectors.postmark_send.httpx.AsyncClient", return_value=mock_client),
        patch("connectors.postmark_send.settings") as mock_settings,
    ):
        mock_settings.postmark_server_token = "tok-test"
        mock_settings.postmark_from_email = "hello@gatsv.com"

        await send_email("client@example.com", "Hi", "Body text")

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["From"] == "hello@gatsv.com"


async def test_send_email_explicit_from_overrides_config():
    """Explicitly passed from_email takes precedence over settings."""
    mock_client = _mock_http_client(200, json_body={"MessageID": "x"})

    with (
        patch("connectors.postmark_send.httpx.AsyncClient", return_value=mock_client),
        patch("connectors.postmark_send.settings") as mock_settings,
    ):
        mock_settings.postmark_server_token = "tok-test"
        mock_settings.postmark_from_email = "default@gatsv.com"

        await send_email(
            "client@example.com", "Hi", "Body", from_email="custom@gatsv.com"
        )

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["From"] == "custom@gatsv.com"


async def test_send_email_raises_postmark_error_on_non_2xx():
    """Non-2xx response raises PostmarkError with status and body."""
    mock_client = _mock_http_client(422, text="Invalid To address")

    with (
        patch("connectors.postmark_send.httpx.AsyncClient", return_value=mock_client),
        patch("connectors.postmark_send.settings") as mock_settings,
    ):
        mock_settings.postmark_server_token = "tok-test"
        mock_settings.postmark_from_email = None

        with pytest.raises(PostmarkError) as exc_info:
            await send_email("bad@@@", "Hi", "Body")

    assert exc_info.value.status_code == 422
    assert "Invalid To address" in exc_info.value.body


async def test_send_email_raises_without_token():
    """Missing server token raises RuntimeError before any HTTP call."""
    with patch("connectors.postmark_send.settings") as mock_settings:
        mock_settings.postmark_server_token = None

        with pytest.raises(RuntimeError, match="POSTMARK_SERVER_TOKEN"):
            await send_email("client@example.com", "Hi", "Body")


# ─── email_dispatcher._tick() tests ──────────────────────────────────────────

async def test_dispatcher_tick_sends_pending_action(monkeypatch):
    """_tick() fetches pending actions, sends email, marks dispatched."""
    pending_row = {
        "id": "action-001",
        "action_type": "send_ack",
        "payload": {
            "to_email": "lead@example.com",
            "subject": "Thanks!",
            "body": "We got your message.",
            "transport": "pending_connector",
        },
    }

    mock_get = MagicMock(return_value=[pending_row])
    mock_mark = MagicMock(return_value=None)
    mock_send = AsyncMock(return_value={"MessageID": "m-1"})

    with (
        patch("scheduler.email_dispatcher.db_actions.get_pending_send_acks", mock_get),
        patch("scheduler.email_dispatcher.db_actions.mark_email_dispatched", mock_mark),
        patch("scheduler.email_dispatcher.send_email", mock_send),
    ):
        from scheduler.email_dispatcher import _tick
        await _tick()

    mock_get.assert_called_once()
    # First mark call: claim with success=True
    first_call = mock_mark.call_args_list[0]
    assert first_call.args == ("action-001", True)
    mock_send.assert_awaited_once_with(
        to_email="lead@example.com",
        subject="Thanks!",
        text_body="We got your message.",
    )


async def test_dispatcher_tick_skips_action_without_email(monkeypatch):
    """_tick() skips (and marks failed) rows with no to_email."""
    pending_row = {
        "id": "action-002",
        "action_type": "send_ack",
        "payload": {"transport": "pending_connector"},
    }

    mock_get = MagicMock(return_value=[pending_row])
    mock_mark = MagicMock(return_value=None)
    mock_send = AsyncMock()

    with (
        patch("scheduler.email_dispatcher.db_actions.get_pending_send_acks", mock_get),
        patch("scheduler.email_dispatcher.db_actions.mark_email_dispatched", mock_mark),
        patch("scheduler.email_dispatcher.send_email", mock_send),
        patch("scheduler.email_dispatcher.asyncio.get_event_loop"),
    ):
        from scheduler.email_dispatcher import _tick
        await _tick()

    mock_send.assert_not_awaited()
    # mark_email_dispatched called with success=False for missing email
    mark_call = mock_mark.call_args
    assert mark_call.args[1] is False


async def test_dispatcher_tick_marks_failed_on_postmark_error(monkeypatch):
    """_tick() marks action email_failed when Postmark raises PostmarkError."""
    pending_row = {
        "id": "action-003",
        "action_type": "send_ack",
        "payload": {
            "to_email": "lead@example.com",
            "subject": "Hi",
            "body": "Hello",
            "transport": "pending_connector",
        },
    }

    mock_get = MagicMock(return_value=[pending_row])
    mock_mark = MagicMock(return_value=None)
    mock_send = AsyncMock(side_effect=PostmarkError(500, "Internal error"))
    mock_loop = MagicMock()
    mock_loop.create_task = MagicMock()

    with (
        patch("scheduler.email_dispatcher.db_actions.get_pending_send_acks", mock_get),
        patch("scheduler.email_dispatcher.db_actions.mark_email_dispatched", mock_mark),
        patch("scheduler.email_dispatcher.send_email", mock_send),
        patch("scheduler.email_dispatcher.asyncio.get_event_loop", return_value=mock_loop),
    ):
        from scheduler.email_dispatcher import _tick
        await _tick()

    # First mark: claim (success=True); failure mark is scheduled via create_task
    first_call = mock_mark.call_args_list[0]
    assert first_call.args == ("action-003", True)
    mock_loop.create_task.assert_called_once()


async def test_dispatcher_tick_noop_when_no_pending():
    """_tick() does nothing when there are no pending send_acks."""
    mock_get = MagicMock(return_value=[])
    mock_send = AsyncMock()

    with (
        patch("scheduler.email_dispatcher.db_actions.get_pending_send_acks", mock_get),
        patch("scheduler.email_dispatcher.send_email", mock_send),
    ):
        from scheduler.email_dispatcher import _tick
        await _tick()

    mock_send.assert_not_awaited()


async def test_dispatcher_tick_continues_on_db_fetch_error():
    """_tick() logs and returns early if fetching pending rows fails."""
    mock_get = MagicMock(side_effect=Exception("DB unavailable"))
    mock_send = AsyncMock()

    with (
        patch("scheduler.email_dispatcher.db_actions.get_pending_send_acks", mock_get),
        patch("scheduler.email_dispatcher.send_email", mock_send),
    ):
        from scheduler.email_dispatcher import _tick
        # Should not raise — dispatcher is resilient to DB errors.
        await _tick()

    mock_send.assert_not_awaited()
