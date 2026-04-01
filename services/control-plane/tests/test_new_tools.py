"""
Unit tests for create_note, list_reminders, cancel_reminder, and daily_brief tools.
Tests dispatch through the tool registry to verify registration is correct.
All DB calls are mocked.
"""

from unittest.mock import patch

import pytest

# Importing agents.tools triggers all register() calls.
import agents.tools  # noqa: F401
from agents.tool_registry import ToolContext, dispatch


def _ctx(phone: str = "+17025551234") -> ToolContext:
    return ToolContext(sender_phone=phone)


_PENDING_TASKS = [
    {"id": "task-1", "content": "Reminder: call Marcus", "scheduled_at": "2026-04-01T22:00:00Z"},
    {"id": "task-2", "content": "Reminder: review PR", "scheduled_at": "2026-04-02T01:00:00Z"},
]


# ─── create_note ──────────────────────────────────────────────────────────────

async def test_create_note_saves_and_acks():
    """Note is saved to DB and a confirmation ack is returned."""
    with patch("agents.tools.create_note.db_notes.create", return_value={"id": "note-1"}) as mock_create:
        result = await dispatch("create_note", {"content": "Buy better coffee"}, _ctx())

    mock_create.assert_called_once_with("+17025551234", "Buy better coffee")
    assert "saved" in result.ack.lower()


async def test_create_note_strips_whitespace():
    """Leading/trailing whitespace in content is stripped before saving."""
    with patch("agents.tools.create_note.db_notes.create", return_value={"id": "note-1"}) as mock_create:
        await dispatch("create_note", {"content": "  Ship Slice 10  "}, _ctx())

    assert mock_create.call_args[0][1] == "Ship Slice 10"


# ─── list_reminders ───────────────────────────────────────────────────────────

async def test_list_reminders_no_pending():
    """Returns a clear 'no reminders' message when the list is empty."""
    with patch("agents.tools.list_reminders.db_tasks.list_pending", return_value=[]):
        result = await dispatch("list_reminders", {}, _ctx())

    assert "no pending reminders" in result.ack.lower()


async def test_list_reminders_formats_list():
    """Pending reminders are formatted as a numbered list with times."""
    with patch("agents.tools.list_reminders.db_tasks.list_pending", return_value=_PENDING_TASKS):
        result = await dispatch("list_reminders", {}, _ctx())

    assert "1." in result.ack
    assert "2." in result.ack
    assert "call Marcus" in result.ack
    assert "review PR" in result.ack
    assert "PT" in result.ack


async def test_list_reminders_strips_reminder_prefix():
    """'Reminder: ' prefix is stripped from content for cleaner display."""
    with patch("agents.tools.list_reminders.db_tasks.list_pending", return_value=_PENDING_TASKS):
        result = await dispatch("list_reminders", {}, _ctx())

    assert "Reminder:" not in result.ack


# ─── cancel_reminder ─────────────────────────────────────────────────────────

async def test_cancel_reminder_no_pending():
    """Returns a clear message when there are no reminders to cancel."""
    with patch("agents.tools.cancel_reminder.db_tasks.list_pending", return_value=[]):
        result = await dispatch("cancel_reminder", {"query": "call Marcus"}, _ctx())

    assert "no pending" in result.ack.lower()


async def test_cancel_reminder_matches_content():
    """Reminder is cancelled when query matches reminder content."""
    with (
        patch("agents.tools.cancel_reminder.db_tasks.list_pending", return_value=_PENDING_TASKS),
        patch("agents.tools.cancel_reminder.db_tasks.mark_status") as mock_mark,
    ):
        result = await dispatch("cancel_reminder", {"query": "call Marcus"}, _ctx())

    mock_mark.assert_called_once_with("task-1", "cancelled")
    assert "Cancelled" in result.ack
    assert "call Marcus" in result.ack


async def test_cancel_reminder_matches_time():
    """Reminder is cancelled when query matches the formatted Pacific time."""
    with (
        patch("agents.tools.cancel_reminder.db_tasks.list_pending", return_value=_PENDING_TASKS),
        patch("agents.tools.cancel_reminder.db_tasks.mark_status") as mock_mark,
    ):
        # task-1 is at 2026-04-01T22:00:00Z = 3:00 PM PT (UTC-7 in April)
        result = await dispatch("cancel_reminder", {"query": "3:00"}, _ctx())

    mock_mark.assert_called_once_with("task-1", "cancelled")


async def test_cancel_reminder_no_match():
    """Returns a 'not found' message when no reminder matches the query."""
    with patch("agents.tools.cancel_reminder.db_tasks.list_pending", return_value=_PENDING_TASKS):
        result = await dispatch("cancel_reminder", {"query": "nonexistent"}, _ctx())

    assert "No pending reminder" in result.ack
    assert "nonexistent" in result.ack


async def test_cancel_reminder_cancels_first_chronological_match():
    """When multiple reminders match, the earliest (first in list) is cancelled."""
    tasks = [
        {"id": "task-early", "content": "Reminder: call someone", "scheduled_at": "2026-04-01T20:00:00Z"},
        {"id": "task-late",  "content": "Reminder: call someone else", "scheduled_at": "2026-04-01T23:00:00Z"},
    ]
    with (
        patch("agents.tools.cancel_reminder.db_tasks.list_pending", return_value=tasks),
        patch("agents.tools.cancel_reminder.db_tasks.mark_status") as mock_mark,
    ):
        await dispatch("cancel_reminder", {"query": "call"}, _ctx())

    mock_mark.assert_called_once_with("task-early", "cancelled")


# ─── daily_brief ──────────────────────────────────────────────────────────────

async def test_daily_brief_saves_pref_and_acks():
    """Send time is saved to user_prefs and a confirmation ack is returned."""
    with patch("agents.tools.daily_brief.db_prefs.set_pref", return_value={"id": "pref-1"}) as mock_set:
        result = await dispatch("daily_brief", {"time_pt": "08:00"}, _ctx())

    mock_set.assert_called_once_with("+17025551234", "digest_send_time_pt", "08:00")
    assert "8:00 AM PT" in result.ack
    assert "tomorrow" in result.ack


async def test_daily_brief_formats_pm_correctly():
    """PM times are formatted correctly in the ack."""
    with patch("agents.tools.daily_brief.db_prefs.set_pref", return_value={"id": "pref-1"}):
        result = await dispatch("daily_brief", {"time_pt": "19:30"}, _ctx())

    assert "7:30 PM PT" in result.ack


async def test_daily_brief_invalid_format_returns_error_ack():
    """Invalid time format returns a user-friendly error ack without crashing."""
    with patch("agents.tools.daily_brief.db_prefs.set_pref") as mock_set:
        result = await dispatch("daily_brief", {"time_pt": "not-a-time"}, _ctx())

    mock_set.assert_not_called()
    assert "Couldn't set that time" in result.ack


async def test_daily_brief_out_of_range_returns_error_ack():
    """Out-of-range time (e.g. 25:00) returns an error ack without crashing."""
    with patch("agents.tools.daily_brief.db_prefs.set_pref") as mock_set:
        result = await dispatch("daily_brief", {"time_pt": "25:00"}, _ctx())

    mock_set.assert_not_called()
    assert "Couldn't set that time" in result.ack


async def test_daily_brief_normalizes_stored_value():
    """Value stored in user_prefs is always zero-padded HH:MM."""
    with patch("agents.tools.daily_brief.db_prefs.set_pref", return_value={"id": "pref-1"}) as mock_set:
        await dispatch("daily_brief", {"time_pt": "07:05"}, _ctx())

    stored_value = mock_set.call_args[0][2]
    assert stored_value == "07:05"
