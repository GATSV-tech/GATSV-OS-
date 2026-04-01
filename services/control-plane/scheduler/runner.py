"""
Scheduler — proactive outbound task runner.

Polls the scheduled_tasks table every N seconds (config: scheduler_poll_interval_seconds)
for pending tasks whose scheduled_at is in the past, fires them via Sendblue, and marks
each task sent or failed.

Lifecycle: start() is called from the FastAPI lifespan on startup; stop() is called on
shutdown. Both are synchronous — the asyncio task is created and cancelled internally.

Error handling:
- Sendblue failure on a single task → mark failed, log to health_logs, continue to
  next task. One bad task never blocks the rest.
- Unexpected error in _tick() → logged, scheduler keeps running.
- DB failure in mark_status → logged; the task remains pending and will be retried
  on the next tick. This is safe — Sendblue will send a duplicate if the message
  went out but the mark failed. Acceptable for v1 at low volume.
"""

import asyncio
import logging

import db.health_logs as db_health_logs
import db.scheduled_tasks as db_tasks
from config import settings
from connectors.sendblue_send import build_status_callback_url, send_message
from db.schemas import HealthLogCreate

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


async def _tick() -> None:
    """Fire all tasks that are due. Called once per poll interval."""
    due = await asyncio.to_thread(db_tasks.get_due)
    if not due:
        return

    logger.info("scheduler: %d due task(s) to fire", len(due))

    for row in due:
        task_id = row["id"]
        phone = row["sender_phone"]
        content = row["content"]

        try:
            await send_message(
                to_number=phone,
                content=content,
                status_callback=build_status_callback_url(),
            )
            await asyncio.to_thread(db_tasks.mark_status, task_id, "sent")
            logger.info("scheduler: task %s sent to %s", task_id, phone)

        except Exception as exc:
            logger.error(
                "scheduler: task %s failed for %s: %s", task_id, phone, exc, exc_info=True
            )
            try:
                await asyncio.to_thread(db_tasks.mark_status, task_id, "failed")
            except Exception:
                logger.error(
                    "scheduler: could not mark task %s failed — will retry next tick",
                    task_id,
                    exc_info=True,
                )
            try:
                await asyncio.to_thread(
                    db_health_logs.create,
                    HealthLogCreate(
                        service="scheduler",
                        event_type="error",
                        message=f"task {task_id} failed: {exc}",
                        metadata={"task_id": task_id, "sender_phone": phone},
                    ),
                )
            except Exception:
                logger.error(
                    "scheduler: failed to write health_log for task %s", task_id, exc_info=True
                )


async def _loop() -> None:
    logger.info(
        "scheduler: running (poll_interval=%ds)", settings.scheduler_poll_interval_seconds
    )
    while True:
        await asyncio.sleep(settings.scheduler_poll_interval_seconds)
        try:
            await _tick()
        except Exception as exc:
            logger.error("scheduler: unhandled error in _tick: %s", exc, exc_info=True)


def start() -> None:
    """Start the scheduler background task. Call from FastAPI lifespan."""
    global _task
    _task = asyncio.get_event_loop().create_task(_loop())
    logger.info("scheduler: started")


def stop() -> None:
    """Cancel the scheduler background task. Call from FastAPI lifespan on shutdown."""
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
        logger.info("scheduler: stopped")
