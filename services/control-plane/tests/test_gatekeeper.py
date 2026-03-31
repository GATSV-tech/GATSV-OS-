"""
Unit tests for the Gatekeeper agent.
All DB calls are mocked at the db module boundary — no Supabase connection needed.
asyncio.to_thread is exercised for real (mocks are sync, called in thread pool).
"""

from unittest.mock import MagicMock, call, patch

import pytest

from agents.gatekeeper import GatekeeperResult, run
from connectors.base import ParsedInbound

# ─── Fixtures ────────────────────────────────────────────────────────────────

MOCK_ENTITY = {"id": "entity-uuid-1", "email": "marcus@brightpath.io", "name": "Marcus Rivera"}
MOCK_EVENT = {"id": "event-uuid-1", "source": "email", "source_id": "msg-123", "entity_id": "entity-uuid-1"}
MOCK_ACTION = {"id": "action-uuid-1"}
MOCK_HEALTH_LOG = {"id": "log-uuid-1"}


def _parsed(**kwargs) -> ParsedInbound:
    defaults = {
        "source": "email",
        "source_id": "msg-123",
        "raw_payload": {"MessageID": "msg-123"},
        "sender_name": "Marcus Rivera",
        "sender_email": "marcus@brightpath.io",
        "subject": "Interested in your services",
        "body": "Hello, I saw your work.",
        "received_at": None,
    }
    defaults.update(kwargs)
    return ParsedInbound(**defaults)


# ─── Tests ───────────────────────────────────────────────────────────────────

@patch("agents.gatekeeper.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.gatekeeper.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.gatekeeper.db_events.create", return_value=MOCK_EVENT)
@patch("agents.gatekeeper.db_entities.upsert_by_contact", return_value=MOCK_ENTITY)
@patch("agents.gatekeeper.db_events.find_by_source", return_value=None)
async def test_new_event_created(mock_find, mock_entity, mock_ev_create, mock_action, mock_hl):
    """Happy path: new entity and event are created, audit trail written."""
    result = await run(_parsed())

    assert isinstance(result, GatekeeperResult)
    assert result.status == "created"
    assert result.event_id == MOCK_EVENT["id"]
    assert result.entity_id == MOCK_ENTITY["id"]
    assert result.duration_ms >= 0

    mock_find.assert_called_once_with("email", "msg-123")
    mock_entity.assert_called_once_with("marcus@brightpath.io", None, "Marcus Rivera")
    mock_ev_create.assert_called_once()
    mock_action.assert_called_once()
    mock_hl.assert_called_once()


@patch("agents.gatekeeper.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.gatekeeper.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.gatekeeper.db_events.create")
@patch("agents.gatekeeper.db_entities.upsert_by_contact")
@patch("agents.gatekeeper.db_events.find_by_source", return_value=MOCK_EVENT)
async def test_select_dedup_returns_duplicate(mock_find, mock_entity, mock_ev_create, mock_action, mock_hl):
    """Existing event found by select — returns duplicate without touching entity or events."""
    result = await run(_parsed())

    assert result.status == "duplicate"
    assert result.event_id == MOCK_EVENT["id"]

    mock_entity.assert_not_called()
    mock_ev_create.assert_not_called()
    # Action is still written to record the dedup decision
    mock_action.assert_called_once()


@patch("agents.gatekeeper.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.gatekeeper.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.gatekeeper.db_events.create", return_value=None)   # None = constraint hit
@patch("agents.gatekeeper.db_entities.upsert_by_contact", return_value=MOCK_ENTITY)
@patch("agents.gatekeeper.db_events.find_by_source", return_value=None)
async def test_constraint_dedup_returns_duplicate(mock_find, mock_entity, mock_ev_create, mock_action, mock_hl):
    """Race-condition: select missed, insert returned None (unique constraint). Returns duplicate."""
    result = await run(_parsed())

    assert result.status == "duplicate"
    assert result.event_id is None        # no event_id available from constraint path
    assert result.entity_id == MOCK_ENTITY["id"]
    mock_action.assert_called_once()


@patch("agents.gatekeeper.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.gatekeeper.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.gatekeeper.db_events.create", return_value=MOCK_EVENT)
@patch("agents.gatekeeper.db_entities.upsert_by_contact", return_value=MOCK_ENTITY)
@patch("agents.gatekeeper.db_events.find_by_source")
async def test_no_source_id_skips_dedup(mock_find, mock_entity, mock_ev_create, mock_action, mock_hl):
    """When source_id is None, dedup select is skipped entirely."""
    result = await run(_parsed(source_id=None))

    assert result.status == "created"
    mock_find.assert_not_called()
    mock_entity.assert_called_once()
    mock_ev_create.assert_called_once()


@patch("agents.gatekeeper.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
@patch("agents.gatekeeper.db_actions.create", return_value=MOCK_ACTION)
@patch("agents.gatekeeper.db_events.create")
@patch("agents.gatekeeper.db_entities.upsert_by_contact", side_effect=RuntimeError("DB connection lost"))
@patch("agents.gatekeeper.db_events.find_by_source", return_value=None)
async def test_unrecoverable_error_propagates(mock_find, mock_entity, mock_ev_create, mock_action, mock_hl):
    """Unrecoverable DB error propagates after writing an error health_log."""
    with pytest.raises(RuntimeError, match="DB connection lost"):
        await run(_parsed())

    mock_ev_create.assert_not_called()
    # health_log written with error event_type
    mock_hl.assert_called_once()
    call_args = mock_hl.call_args[0][0]
    assert call_args.event_type == "error"
    assert "DB connection lost" in call_args.message
