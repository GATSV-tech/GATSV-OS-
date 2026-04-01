"""
Unit tests for the scheduler runner.

Sendblue send and all DB calls are mocked — no real HTTP or DB.
Tests exercise _tick() directly; the asyncio loop wrapper is not tested here.

Ordering contract (post Bug 1 fix):
  1. mark_status(task_id, "sent")   ← claim BEFORE send
  2. send_message(...)
  If send fails: mark_status(task_id, "failed") + health_log
  If claim fails: skip the send entirely, task stays pending
"""

from unittest.mock import AsyncMock, call, patch

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
    """All due tasks are claimed, sent, and marked 'sent' in order."""
    with (
        patch("scheduler.runner.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"}) as mock_send,
        patch("scheduler.runner.db_tasks.mark_status") as mock_mark,
        patch("scheduler.runner.build_status_callback_url", return_value=None),
    ):
        await _tick()

    assert mock_send.call_count == 2
    # Two claims only (one per task, both successful)
    assert mock_mark.call_count == 2

    assert mock_send.call_args_list[0].kwargs["to_number"] == "+17025551111"
    assert mock_send.call_args_list[0].kwargs["content"] == "Reminder: call Marcus"
    assert mock_mark.call_args_list[0] == call("task-1", "sent")

    assert mock_send.call_args_list[1].kwargs["to_number"] == "+17025552222"
    assert mock_mark.call_args_list[1] == call("task-2", "sent")


@patch("scheduler.runner.db_tasks.get_due", return_value=[_task_row("task-1")])
async def test_tick_claims_before_send(mock_get_due):
    """
    Task is claimed (marked 'sent') BEFORE Sendblue is called.
    This prevents duplicate sends when the process crashes between send and mark.
    """
    call_order: list[str] = []

    async def fake_send(**kwargs):
        call_order.append("send")
        return {"status": "QUEUED"}

    def fake_mark(task_id, status):
        call_order.append(f"mark:{status}")

    with (
        patch("scheduler.runner.send_message", side_effect=fake_send),
        patch("scheduler.runner.db_tasks.mark_status", side_effect=fake_mark),
        patch("scheduler.runner.build_status_callback_url", return_value=None),
    ):
        await _tick()

    # Claim must happen before send
    assert call_order == ["mark:sent", "send"]


# ─── Sendblue failure ─────────────────────────────────────────────────────────

@patch("scheduler.runner.db_tasks.get_due", return_value=[_task_row("task-1")])
@patch("scheduler.runner.db_health_logs.create", return_value={"id": "log-1"})
async def test_tick_marks_failed_on_send_error(mock_hl, mock_get_due):
    """
    Sendblue failure: task is first claimed ('sent'), then marked 'failed' after
    the send error. Health log is written. mark_status is called twice total.
    """
    with (
        patch("scheduler.runner.send_message", new_callable=AsyncMock, side_effect=Exception("timeout")),
        patch("scheduler.runner.db_tasks.mark_status") as mock_mark,
        patch("scheduler.runner.build_status_callback_url", return_value=None),
    ):
        await _tick()

    assert mock_mark.call_count == 2
    assert mock_mark.call_args_list[0] == call("task-1", "sent")   # claim
    assert mock_mark.call_args_list[1] == call("task-1", "failed") # rollback

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
    send_count = 0

    async def flaky_send(**kwargs):
        nonlocal send_count
        send_count += 1
        if send_count == 1:
            raise Exception("first task blew up")
        return {"status": "QUEUED"}

    with (
        patch("scheduler.runner.send_message", side_effect=flaky_send),
        patch("scheduler.runner.db_tasks.mark_status") as mock_mark,
        patch("scheduler.runner.db_health_logs.create", return_value={"id": "log-1"}),
        patch("scheduler.runner.build_status_callback_url", return_value=None),
    ):
        await _tick()

    assert send_count == 2  # both tasks were attempted

    all_calls = [c[0] for c in mock_mark.call_args_list]
    # task-1: claimed "sent", then rolled back to "failed"
    assert ("task-1", "sent") in all_calls
    assert ("task-1", "failed") in all_calls
    # task-2: claimed and sent successfully
    assert ("task-2", "sent") in all_calls


# ─── Claim failure (mark_status fails before send) ────────────────────────────

@patch("scheduler.runner.db_tasks.get_due", return_value=[_task_row("task-1")])
async def test_tick_skips_send_if_claim_fails(mock_get_due):
    """
    If mark_status raises on the claim step, send_message is NOT called.
    Task stays pending and will be retried next tick — no duplicate risk.
    """
    with (
        patch("scheduler.runner.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"}) as mock_send,
        patch("scheduler.runner.db_tasks.mark_status", side_effect=Exception("DB down")),
        patch("scheduler.runner.build_status_callback_url", return_value=None),
    ):
        await _tick()

    mock_send.assert_not_called()


@patch("scheduler.runner.db_tasks.get_due", return_value=[
    _task_row("task-1"),
    _task_row("task-2", "+17025552222", "Reminder: second"),
])
async def test_tick_claim_failure_does_not_block_remaining_tasks(mock_get_due):
    """Claim failure on one task does not prevent processing of subsequent tasks."""
    claim_count = 0

    def flaky_mark(task_id, status):
        nonlocal claim_count
        if task_id == "task-1" and status == "sent":
            claim_count += 1
            raise Exception("DB blip")

    with (
        patch("scheduler.runner.send_message", new_callable=AsyncMock, return_value={"status": "QUEUED"}) as mock_send,
        patch("scheduler.runner.db_tasks.mark_status", side_effect=flaky_mark),
        patch("scheduler.runner.build_status_callback_url", return_value=None),
    ):
        await _tick()

    # task-1 claim failed → no send for task-1; task-2 still processed
    assert mock_send.call_count == 1
    assert mock_send.call_args.kwargs["to_number"] == "+17025552222"
