"""
Unit tests for the scheduler runner.

Sendblue send and all DB calls are mocked — no real HTTP or DB.
Tests exercise _tick() directly; the asyncio loop wrapper is not tested here.
"""

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from scheduler.runner import _tick


def _task_row(
    task_id: str = "task-abc",
    phone: str = "+17025551234",
    content: str = "Reminder: call Marcus",
) -> dict:
    return {"id": task_id, "sender_phone": phone, "content": content}


# ─── No due tasks ─────────────────────────────────────────────────────────────

@patch("scheduler.runner.db_tasks.get_due", return_value=[])
async def test_tick_no_tasks_is_noop(mock_get_due):
    """When get_due returns empty, no sends or DB writes happen."""
    with (
        patch("scheduler.runner.send_message", new_callable=AsyncMock) as mock_send,
        patch("scheduler.runner.db_tasks.mark_status") as mock_mark,
    ):
        await _tick()

    mock_send.assert_not_called()
    mock_mark.assert_not_called()


# ─── Due tasks fired ──────────────────────────────────────────────────────────

@patch("scheduler.runner.db_tasks.get_due", return_value=[
    _task_row("task-1", "+17025551111", "Reminder: call Marcus"),
    _task_row("task-2", "+17025552222", "Reminder: review PR"),
])
async def test_tick_sends_all_due_tasks(mock_get_due):
    """All due tasks are sent and marked 'sent' in order."""
    with (
        patch("scheduler.runner.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"}) as mock_send,
        patch("scheduler.runner.db_tasks.mark_status") as mock_mark,
        patch("scheduler.runner.build_status_callback_url", return_value=None),
    ):
        await _tick()

    assert mock_send.call_count == 2
    assert mock_mark.call_count == 2

    # First task
    assert mock_send.call_args_list[0].kwargs["to_number"] == "+17025551111"
    assert mock_send.call_args_list[0].kwargs["content"] == "Reminder: call Marcus"
    assert mock_mark.call_args_list[0] == call("task-1", "sent")

    # Second task
    assert mock_send.call_args_list[1].kwargs["to_number"] == "+17025552222"
    assert mock_mark.call_args_list[1] == call("task-2", "sent")


@patch("scheduler.runner.db_tasks.get_due", return_value=[
    _task_row("task-1"),
])
async def test_tick_marks_sent_after_successful_send(mock_get_due):
    """Task is marked 'sent' only after Sendblue confirms the send."""
    send_call_order: list[str] = []

    async def fake_send(**kwargs):
        send_call_order.append("send")
        return {"status": "QUEUED"}

    def fake_mark(task_id, status):
        send_call_order.append(f"mark:{status}")

    with (
        patch("scheduler.runner.send_message", side_effect=fake_send),
        patch("scheduler.runner.db_tasks.mark_status", side_effect=fake_mark),
        patch("scheduler.runner.build_status_callback_url", return_value=None),
    ):
        await _tick()

    assert send_call_order == ["send", "mark:sent"]


# ─── Sendblue failure ─────────────────────────────────────────────────────────

@patch("scheduler.runner.db_tasks.get_due", return_value=[
    _task_row("task-1"),
])
@patch("scheduler.runner.db_health_logs.create", return_value={"id": "log-1"})
async def test_tick_marks_failed_on_send_error(mock_hl, mock_get_due):
    """Sendblue failure marks task 'failed' and writes a health_log."""
    with (
        patch("scheduler.runner.send_message", new_callable=AsyncMock, side_effect=Exception("timeout")),
        patch("scheduler.runner.db_tasks.mark_status") as mock_mark,
        patch("scheduler.runner.build_status_callback_url", return_value=None),
    ):
        await _tick()

    mock_mark.assert_called_once_with("task-1", "failed")
    mock_hl.assert_called_once()
    log_arg = mock_hl.call_args[0][0]
    assert log_arg.service == "scheduler"
    assert log_arg.event_type == "error"
    assert "task-1" in log_arg.message


@patch("scheduler.runner.db_tasks.get_due", return_value=[
    _task_row("task-1", "+17025551111", "Reminder: first"),
    _task_row("task-2", "+17025552222", "Reminder: second"),
])
async def test_tick_continues_after_one_task_fails(mock_get_due):
    """A Sendblue failure on one task does not block the remaining tasks."""
    call_count = 0

    async def flaky_send(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("first task blew up")
        return {"status": "QUEUED"}

    with (
        patch("scheduler.runner.send_message", side_effect=flaky_send),
        patch("scheduler.runner.db_tasks.mark_status") as mock_mark,
        patch("scheduler.runner.db_health_logs.create", return_value={"id": "log-1"}),
        patch("scheduler.runner.build_status_callback_url", return_value=None),
    ):
        await _tick()

    assert call_count == 2  # both tasks were attempted
    statuses = [c[0] for c in mock_mark.call_args_list]
    assert ("task-1", "failed") in statuses
    assert ("task-2", "sent") in statuses


# ─── mark_status DB failure ───────────────────────────────────────────────────

@patch("scheduler.runner.db_tasks.get_due", return_value=[
    _task_row("task-1"),
])
async def test_tick_survives_mark_status_failure(mock_get_due):
    """
    If mark_status raises after a successful send, _tick does not crash.
    The task remains pending and will be retried next tick (possible duplicate send).
    """
    with (
        patch("scheduler.runner.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"}),
        patch("scheduler.runner.db_tasks.mark_status", side_effect=Exception("DB down")),
        patch("scheduler.runner.build_status_callback_url", return_value=None),
    ):
        # Should not raise
        await _tick()
