"""
Unit tests for the Router agent.
All DB calls and the Anthropic client are mocked at module boundaries.
asyncio.to_thread is exercised for real (mocks are sync, called in thread pool).
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.gatekeeper import GatekeeperResult
from agents.router import RouterResult, run

# ─── Fixtures ────────────────────────────────────────────────────────────────

MOCK_EVENT = {
    "id": "event-uuid-1",
    "source": "email",
    "source_id": "msg-123",
    "status": "normalized",
    "bucket": None,
    "priority": None,
    "confidence": None,
    "sender_name": "Marcus Rivera",
    "sender_email": "marcus@brightpath.io",
    "subject": "Interested in your services",
    "body": "Hi, I saw your work and would love to learn more about pricing.",
    "entity_id": "entity-uuid-1",
    "received_at": None,
    "created_at": "2026-04-01T00:00:00",
    "updated_at": "2026-04-01T00:00:00",
}

MOCK_UPDATED_EVENT = {**MOCK_EVENT, "bucket": "sales", "priority": "medium", "confidence": 0.92, "status": "routed"}
MOCK_ACTION = {"id": "action-uuid-1"}
MOCK_HEALTH_LOG = {"id": "log-uuid-1"}

_GK_CREATED = GatekeeperResult(
    event_id="event-uuid-1",
    entity_id="entity-uuid-1",
    status="created",
    duration_ms=10,
)

_GK_DUPLICATE = GatekeeperResult(
    event_id="event-uuid-1",
    entity_id="entity-uuid-1",
    status="duplicate",
    duration_ms=5,
)


def _mock_anthropic_response(bucket: str = "sales", priority: str = "medium", confidence: float = 0.92) -> MagicMock:
    """Build a minimal mock Anthropic response with a tool_use block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "classify_event"
    tool_block.input = {
        "bucket": bucket,
        "priority": priority,
        "confidence": confidence,
        "reasoning": "Lead asking about pricing — clear sales signal.",
    }

    usage = MagicMock()
    usage.input_tokens = 120
    usage.output_tokens = 40

    response = MagicMock()
    response.content = [tool_block]
    response.usage = usage
    return response


# ─── Happy path ──────────────────────────────────────────────────────────────

@patch("agents.router.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.router.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.router.db_events.update", return_value=MOCK_UPDATED_EVENT)
@patch("agents.router.db_events.get_by_id", return_value=MOCK_EVENT)
async def test_classifies_new_event(mock_get, mock_update, mock_action, mock_hl):
    """Happy path: new event is classified and event row is updated."""
    mock_response = _mock_anthropic_response()
    with patch("agents.router._anthropic") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        result = await run(_GK_CREATED)

    assert isinstance(result, RouterResult)
    assert result.status == "routed"
    assert result.event_id == "event-uuid-1"
    assert result.bucket == "sales"
    assert result.priority == "medium"
    assert abs(result.confidence - 0.92) < 0.001
    assert result.duration_ms >= 0

    mock_get.assert_called_once_with("event-uuid-1")
    mock_update.assert_called_once()
    mock_action.assert_called_once()
    mock_hl.assert_called_once()


# ─── Skip on duplicate ───────────────────────────────────────────────────────

async def test_skips_duplicate_event():
    """Duplicate events from Gatekeeper are skipped without any DB or LLM calls."""
    with patch("agents.router._anthropic") as mock_client:
        result = await run(_GK_DUPLICATE)

    assert result.status == "skipped"
    assert result.event_id == "event-uuid-1"
    assert result.bucket is None
    mock_client.messages.create.assert_not_called()


async def test_skips_when_event_id_is_none():
    """Gatekeeper result with no event_id (constraint dedup) is skipped."""
    gk = GatekeeperResult(event_id=None, entity_id="entity-uuid-1", status="duplicate", duration_ms=5)
    result = await run(gk)
    assert result.status == "skipped"


# ─── Bucket variants ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bucket,priority", [
    ("delivery", "high"),
    ("support", "low"),
    ("founder_review", "medium"),
    ("noise", "low"),
])
@patch("agents.router.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.router.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.router.db_events.update", return_value=MOCK_UPDATED_EVENT)
@patch("agents.router.db_events.get_by_id", return_value=MOCK_EVENT)
async def test_all_bucket_variants(mock_get, mock_update, mock_action, mock_hl, bucket, priority):
    """Router correctly surfaces whatever bucket/priority Claude returns."""
    mock_response = _mock_anthropic_response(bucket=bucket, priority=priority, confidence=0.85)
    with patch("agents.router._anthropic") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        result = await run(_GK_CREATED)

    assert result.status == "routed"
    assert result.bucket == bucket
    assert result.priority == priority


# ─── Event not found ─────────────────────────────────────────────────────────

@patch("agents.router.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.router.db_events.get_by_id", return_value=None)
async def test_returns_error_when_event_not_found(mock_get, mock_hl):
    """If the event row is missing, Router returns status=error (never raises)."""
    result = await run(_GK_CREATED)

    assert result.status == "error"
    assert result.event_id == "event-uuid-1"
    mock_hl.assert_called_once()


# ─── Action payload contains cost fields ─────────────────────────────────────

@patch("agents.router.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.router.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.router.db_events.update", return_value=MOCK_UPDATED_EVENT)
@patch("agents.router.db_events.get_by_id", return_value=MOCK_EVENT)
async def test_action_includes_token_cost(mock_get, mock_update, mock_action, mock_hl):
    """Action row must include token counts and usd_cost for observability."""
    mock_response = _mock_anthropic_response()
    with patch("agents.router._anthropic") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        await run(_GK_CREATED)

    call_args = mock_action.call_args[0][0]  # ActionCreate instance
    assert call_args.token_input == 120
    assert call_args.token_output == 40
    assert call_args.usd_cost > Decimal("0")
    assert call_args.agent == "router"
    assert call_args.action_type == "classify_event"
