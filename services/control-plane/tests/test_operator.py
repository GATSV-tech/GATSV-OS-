"""
Unit tests for the Operator agent.
All DB calls and the Anthropic client are mocked at module boundaries.
asyncio.to_thread is exercised for real (mocks are sync, called in thread pool).
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from agents.operator import OperatorResult, run
from agents.router import RouterResult

# ─── Fixtures ────────────────────────────────────────────────────────────────

MOCK_EVENT = {
    "id": "event-uuid-1",
    "source": "email",
    "status": "routed",
    "bucket": "sales",
    "priority": "medium",
    "sender_name": "Marcus Rivera",
    "sender_email": "marcus@brightpath.io",
    "subject": "Interested in your services",
    "body": "Hi, I saw your work and would love to learn more about pricing.",
    "entity_id": "entity-uuid-1",
}

MOCK_EVENT_NO_EMAIL = {**MOCK_EVENT, "sender_email": None}
MOCK_EVENT_NO_ENTITY = {**MOCK_EVENT, "entity_id": None}

MOCK_ACTION = {"id": "action-uuid-1"}
MOCK_MEMORY = {"id": "memory-uuid-1"}
MOCK_APPROVAL = {"id": "approval-uuid-1"}
MOCK_HEALTH_LOG = {"id": "log-uuid-1"}

_RT_SALES = RouterResult(
    event_id="event-uuid-1",
    bucket="sales",
    priority="medium",
    confidence=0.92,
    status="routed",
    duration_ms=50,
)

_RT_SUPPORT = RouterResult(
    event_id="event-uuid-1",
    bucket="support",
    priority="high",
    confidence=0.87,
    status="routed",
    duration_ms=50,
)

_RT_DELIVERY = RouterResult(
    event_id="event-uuid-1",
    bucket="delivery",
    priority="medium",
    confidence=0.80,
    status="routed",
    duration_ms=50,
)

_RT_ERROR = RouterResult(
    event_id="event-uuid-1",
    bucket=None,
    status="error",
    duration_ms=50,
)


def _mock_anthropic_response(actions: list[dict]) -> MagicMock:
    """Build a minimal mock Anthropic response with a plan_actions tool_use block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "plan_actions"
    tool_block.input = {"actions": actions}

    usage = MagicMock()
    usage.input_tokens = 150
    usage.output_tokens = 80

    response = MagicMock()
    response.content = [tool_block]
    response.usage = usage
    return response


def _plan_note_and_ack_low() -> list[dict]:
    return [
        {
            "action_type": "create_entity_note",
            "risk": "low",
            "note_content": "Marcus Rivera inquired about pricing for agency services via email.",
            "reason": "Capture lead interaction context.",
        },
        {
            "action_type": "send_ack",
            "risk": "low",
            "ack_subject": "Thanks for reaching out",
            "ack_body": "Hi Marcus, thanks for your interest. We'll be in touch shortly.",
            "reason": "Acknowledge new lead promptly.",
        },
    ]


def _plan_note_and_ack_high() -> list[dict]:
    return [
        {
            "action_type": "create_entity_note",
            "risk": "low",
            "note_content": "Existing client raised a billing dispute via email.",
            "reason": "Document support interaction.",
        },
        {
            "action_type": "send_ack",
            "risk": "high",
            "ack_subject": "We received your message",
            "ack_body": "Hi, we received your support request and will follow up shortly.",
            "reason": "Acknowledge client issue — requires founder review.",
        },
    ]


# ─── Skip paths ──────────────────────────────────────────────────────────────

async def test_skips_non_routed_status():
    """Operator skips events that didn't route cleanly."""
    result = await run(_RT_ERROR)
    assert result.status == "skipped"
    assert result.event_id == "event-uuid-1"


async def test_skips_delivery_bucket():
    """Operator does not act on delivery events."""
    result = await run(_RT_DELIVERY)
    assert result.status == "skipped"


@pytest.mark.parametrize("bucket", ["founder_review", "noise"])
async def test_skips_other_buckets(bucket):
    """Operator skips founder_review and noise without any LLM call."""
    rt = RouterResult(
        event_id="event-uuid-1",
        bucket=bucket,
        priority="low",
        confidence=0.7,
        status="routed",
        duration_ms=5,
    )
    with patch("agents.operator._anthropic") as mock_client:
        result = await run(rt)
    assert result.status == "skipped"
    mock_client.messages.create.assert_not_called()


# ─── Sales bucket — both actions low-risk ────────────────────────────────────

@patch("agents.operator.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.operator.db_events.update", return_value=MOCK_EVENT)
@patch("agents.operator.db_approvals.create", return_value=MOCK_APPROVAL)
@patch("agents.operator.db_memories.create", return_value=MOCK_MEMORY)
@patch("agents.operator.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.operator.db_events.get_by_id", return_value=MOCK_EVENT)
async def test_sales_executes_note_and_ack(
    mock_get, mock_action, mock_memory, mock_approval, mock_ev_update, mock_hl
):
    """Sales event: create_entity_note and send_ack both execute immediately."""
    mock_response = _mock_anthropic_response(_plan_note_and_ack_low())
    with patch("agents.operator._anthropic") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        result = await run(_RT_SALES)

    assert result.status == "actioned"
    assert "create_entity_note" in result.actions_executed
    assert "send_ack" in result.actions_executed
    assert result.actions_queued == []

    # memory row was written
    mock_memory.assert_called_once()
    memory_arg = mock_memory.call_args[0][0]
    assert memory_arg.entity_id == "entity-uuid-1"
    assert memory_arg.memory_type == "note"

    # approval was NOT created for low-risk send_ack
    mock_approval.assert_not_called()

    # event updated to actioned
    mock_ev_update.assert_called_once()
    update_arg = mock_ev_update.call_args[0][1]
    assert update_arg.status == "actioned"


# ─── Support bucket — send_ack goes to approval ──────────────────────────────

@patch("agents.operator.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.operator.db_events.update", return_value=MOCK_EVENT)
@patch("agents.operator.db_approvals.create", return_value=MOCK_APPROVAL)
@patch("agents.operator.db_memories.create", return_value=MOCK_MEMORY)
@patch("agents.operator.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.operator.db_events.get_by_id", return_value={**MOCK_EVENT, "bucket": "support"})
async def test_support_queues_ack_for_approval(
    mock_get, mock_action, mock_memory, mock_approval, mock_ev_update, mock_hl
):
    """Support event: note executes, send_ack goes to approval queue."""
    mock_response = _mock_anthropic_response(_plan_note_and_ack_high())
    with patch("agents.operator._anthropic") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        result = await run(_RT_SUPPORT)

    assert result.status == "actioned"
    assert "create_entity_note" in result.actions_executed
    assert "send_ack" in result.actions_queued
    assert "send_ack" not in result.actions_executed

    # approval row was created
    mock_approval.assert_called_once()
    approval_arg = mock_approval.call_args[0][0]
    assert approval_arg.requested_by == "operator"
    assert "marcus@brightpath.io" in approval_arg.summary
    assert approval_arg.context["to_email"] == "marcus@brightpath.io"


# ─── No sender email — send_ack silently skipped ────────────────────────────

@patch("agents.operator.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.operator.db_events.update", return_value=MOCK_EVENT)
@patch("agents.operator.db_approvals.create", return_value=MOCK_APPROVAL)
@patch("agents.operator.db_memories.create", return_value=MOCK_MEMORY)
@patch("agents.operator.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.operator.db_events.get_by_id", return_value=MOCK_EVENT_NO_EMAIL)
async def test_send_ack_skipped_when_no_sender_email(
    mock_get, mock_action, mock_memory, mock_approval, mock_ev_update, mock_hl
):
    """send_ack is silently skipped when the event has no sender_email."""
    mock_response = _mock_anthropic_response(_plan_note_and_ack_low())
    with patch("agents.operator._anthropic") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        result = await run(_RT_SALES)

    assert result.status == "actioned"
    # send_ack was in plan but silently dropped — not in executed or queued
    assert "send_ack" not in result.actions_executed
    assert "send_ack" not in result.actions_queued


# ─── No entity_id — create_entity_note skipped ──────────────────────────────

@patch("agents.operator.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.operator.db_events.update", return_value=MOCK_EVENT)
@patch("agents.operator.db_approvals.create", return_value=MOCK_APPROVAL)
@patch("agents.operator.db_memories.create", return_value=MOCK_MEMORY)
@patch("agents.operator.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.operator.db_events.get_by_id", return_value=MOCK_EVENT_NO_ENTITY)
async def test_entity_note_skipped_when_no_entity_id(
    mock_get, mock_action, mock_memory, mock_approval, mock_ev_update, mock_hl
):
    """create_entity_note is silently skipped when the event has no entity_id."""
    mock_response = _mock_anthropic_response(_plan_note_and_ack_low())
    with patch("agents.operator._anthropic") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        result = await run(_RT_SALES)

    assert result.status == "actioned"
    assert "create_entity_note" not in result.actions_executed
    mock_memory.assert_not_called()


# ─── Empty plan ──────────────────────────────────────────────────────────────

@patch("agents.operator.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.operator.db_events.update", return_value=MOCK_EVENT)
@patch("agents.operator.db_approvals.create", return_value=MOCK_APPROVAL)
@patch("agents.operator.db_memories.create", return_value=MOCK_MEMORY)
@patch("agents.operator.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.operator.db_events.get_by_id", return_value=MOCK_EVENT)
async def test_empty_plan_actioned_gracefully(
    mock_get, mock_action, mock_memory, mock_approval, mock_ev_update, mock_hl
):
    """Haiku returns no actions — event is still marked actioned, no crash."""
    mock_response = _mock_anthropic_response([])
    with patch("agents.operator._anthropic") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        result = await run(_RT_SALES)

    assert result.status == "actioned"
    assert result.actions_executed == []
    assert result.actions_queued == []
    mock_memory.assert_not_called()
    mock_approval.assert_not_called()


# ─── Event not found ─────────────────────────────────────────────────────────

@patch("agents.operator.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.operator.db_events.get_by_id", return_value=None)
async def test_returns_error_when_event_not_found(mock_get, mock_hl):
    """Missing event row → status=error, never raises."""
    result = await run(_RT_SALES)
    assert result.status == "error"
    assert result.event_id == "event-uuid-1"
    mock_hl.assert_called_once()


# ─── Plan action row includes token cost ─────────────────────────────────────

@patch("agents.operator.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.operator.db_events.update", return_value=MOCK_EVENT)
@patch("agents.operator.db_approvals.create", return_value=MOCK_APPROVAL)
@patch("agents.operator.db_memories.create", return_value=MOCK_MEMORY)
@patch("agents.operator.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.operator.db_events.get_by_id", return_value=MOCK_EVENT)
async def test_plan_action_row_includes_cost(
    mock_get, mock_action, mock_memory, mock_approval, mock_ev_update, mock_hl
):
    """The plan_actions action row must carry token counts and usd_cost."""
    mock_response = _mock_anthropic_response([])
    with patch("agents.operator._anthropic") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        await run(_RT_SALES)

    # The first call to db_actions.create is the plan_actions row.
    plan_action_arg = mock_action.call_args_list[0][0][0]
    assert plan_action_arg.action_type == "plan_actions"
    assert plan_action_arg.token_input == 150
    assert plan_action_arg.token_output == 80
    assert plan_action_arg.usd_cost > Decimal("0")
    assert plan_action_arg.agent == "operator"
