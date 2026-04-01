"""
Scheduler — proactive outbound task runner.

Polls the scheduled_tasks table every N seconds (config: scheduler_poll_interval_seconds)
for pending tasks whose scheduled_at is in the past, fires them via Sendblue.

Duplicate-send prevention: each task is marked 'sent' BEFORE the Sendblue call.
This means a process crash between mark and send produces a missed send (acceptable)
rather than a duplicate send (not acceptable). If the Sendblue call fails after
the optimistic mark, the task is marked 'failed' and will not retry automatically.

Error handling:
- DB failure on claim → log and skip; task stays pending, retried next tick.
- Sendblue failure after claim → mark failed, log to health_logs, continue to next task.
- Unexpected error in _tick() → logged, scheduler keeps running.
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

        # ── Step 1: Claim the task before sending ─────────────────────────
        # Marking 'sent' first prevents re-fire if the process crashes between
        # send and mark. Trade-off: a crash after mark but before send = missed
        # send, not a duplicate. Missed sends are safer than duplicate sends.
        try:
            await asyncio.to_thread(db_tasks.mark_status, task_id, "sent")
        except Exception as exc:
            logger.error(
                "scheduler: could not claim task %s — skipping this tick: %s",
                task_id, exc, exc_info=True,
            )
            continue  # Task stays pending; retried on the next tick.

        # ── Step 2: Send ──────────────────────────────────────────────────
        try:
            await send_message(
                to_number=phone,
                content=content,
                status_callback=build_status_callback_url(),
            )
            logger.info("scheduler: task %s sent to %s", task_id, phone)

        except Exception as exc:
            logger.error(
                "scheduler: task %s send failed for %s: %s", task_id, phone, exc, exc_info=True
            )
            try:
                await asyncio.to_thread(db_tasks.mark_status, task_id, "failed")
            except Exception:
                logger.error(
                    "scheduler: could not mark task %s failed", task_id, exc_info=True
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
