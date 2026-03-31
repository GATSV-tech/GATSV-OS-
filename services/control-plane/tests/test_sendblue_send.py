"""
Unit tests for the Sendblue outbound connector.
httpx is mocked — no real HTTP calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from connectors.sendblue_send import SendblueError, send_message


def _mock_client(status_code: int = 200, json_body: dict | None = None, text: str = ""):
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


async def test_send_message_success():
    """Successful send returns the parsed Sendblue response dict."""
    expected = {"message_handle": "handle-123", "status": "QUEUED"}
    mock_client = _mock_client(200, json_body=expected)

    with (
        patch("connectors.sendblue_send.httpx.AsyncClient", return_value=mock_client),
        patch("connectors.sendblue_send.settings") as mock_settings,
    ):
        mock_settings.sendblue_api_key = "key"
        mock_settings.sendblue_api_secret = "secret"
        mock_settings.sendblue_from_number = "+13053369541"

        result = await send_message("+17025551234", "Hello")

    assert result == expected
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert call_kwargs.kwargs["json"]["number"] == "+17025551234"
    assert call_kwargs.kwargs["json"]["content"] == "Hello"
    assert "status_callback" not in call_kwargs.kwargs["json"]


async def test_send_message_includes_status_callback():
    """status_callback is included in the payload when provided."""
    mock_client = _mock_client(200, json_body={"status": "QUEUED"})

    with (
        patch("connectors.sendblue_send.httpx.AsyncClient", return_value=mock_client),
        patch("connectors.sendblue_send.settings") as mock_settings,
    ):
        mock_settings.sendblue_api_key = "key"
        mock_settings.sendblue_api_secret = "secret"
        mock_settings.sendblue_from_number = "+13053369541"

        await send_message("+17025551234", "Hello", status_callback="https://example.com/status")

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["status_callback"] == "https://example.com/status"


async def test_send_message_raises_on_non_2xx():
    """Non-2xx response raises SendblueError with status code and body."""
    mock_client = _mock_client(429, text="rate limited")

    with (
        patch("connectors.sendblue_send.httpx.AsyncClient", return_value=mock_client),
        patch("connectors.sendblue_send.settings") as mock_settings,
    ):
        mock_settings.sendblue_api_key = "key"
        mock_settings.sendblue_api_secret = "secret"
        mock_settings.sendblue_from_number = "+13053369541"

        with pytest.raises(SendblueError) as exc_info:
            await send_message("+17025551234", "Hello")

    assert exc_info.value.status_code == 429
    assert "rate limited" in exc_info.value.body


async def test_send_message_raises_without_credentials():
    """Missing API credentials raise RuntimeError before any HTTP call is made."""
    with patch("connectors.sendblue_send.settings") as mock_settings:
        mock_settings.sendblue_api_key = None
        mock_settings.sendblue_api_secret = None
        mock_settings.sendblue_from_number = "+13053369541"

        with pytest.raises(RuntimeError, match="SENDBLUE_API_KEY"):
            await send_message("+17025551234", "Hello")
