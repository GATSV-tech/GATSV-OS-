"""
Email dispatcher — outbound send_ack delivery loop.

Polls the actions table every N seconds (config: email_poll_interval_seconds)
for send_ack rows with payload.transport = 'pending_connector', sends each via
Postmark, then marks the row as email_sent or email_failed.

Duplicate-send prevention: the row is marked BEFORE the Postmark call.
A crash between mark and send produces a missed send (acceptable) rather than
a duplicate (not acceptable). If Postmark fails after the mark, the row is
marked email_failed and will not retry automatically.

Error handling:
- DB failure on fetch → log and skip; rows stay pending, retried next tick.
- DB failure on mark → log; Postmark call is skipped for that row to avoid duplicate sends.
- Postmark failure after mark → mark email_failed, log to health_logs, continue.
- Unexpected error in _tick() → logged, dispatcher keeps running.

Lifecycle: start() is called from the FastAPI lifespan; stop() cancels the
background task on shutdown. If POSTMARK_SERVER_TOKEN is not configured,
start() logs a warning and does nothing.
"""

import asyncio
import logging

import db.actions as db_actions
import db.health_logs as db_health_logs
from config import settings
from connectors.postmark_send import PostmarkError, send_email
from db.schemas import HealthLogCreate

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


async def _tick() -> None:
    """Dispatch all pending send_ack emails. Called once per poll interval."""
    try:
        rows = await asyncio.to_thread(db_actions.get_pending_send_acks)
    except Exception as exc:
        logger.error("email_dispatcher: failed to fetch pending send_acks: %s", exc, exc_info=True)
        return

    if not rows:
        return

    logger.info("email_dispatcher: %d pending send_ack(s) to dispatch", len(rows))

    for row in rows:
        action_id = row["id"]
        payload = row.get("payload") or {}
        to_email = payload.get("to_email", "")
        subject = payload.get("subject", "(no subject)")
        body = payload.get("body", "")

        if not to_email:
            logger.warning(
                "email_dispatcher: action %s has no to_email — skipping", action_id
            )
            # Mark failed so it doesn't loop forever.
            try:
                await asyncio.to_thread(
                    db_actions.mark_email_dispatched,
                    action_id,
                    False,
                    "no to_email in payload",
                )
            except Exception as mark_exc:
                logger.error(
                    "email_dispatcher: could not mark action %s failed: %s",
                    action_id, mark_exc, exc_info=True,
                )
            continue

        # ── Step 1: Claim the row before sending ────────────────────────
        # Marking first prevents re-send if the process crashes mid-flight.
        # If this DB call fails, skip and retry next tick — row stays pending.
        try:
            await asyncio.to_thread(
                db_actions.mark_email_dispatched, action_id, True
            )
        except Exception as exc:
            logger.error(
                "email_dispatcher: could not claim action %s — skipping: %s",
                action_id, exc, exc_info=True,
            )
            continue

        # ── Step 2: Send via Postmark ────────────────────────────────────
        try:
            await send_email(to_email=to_email, subject=subject, text_body=body)
            logger.info(
                "email_dispatcher: action %s dispatched to %s", action_id, to_email
            )
        except PostmarkError as exc:
            logger.error(
                "email_dispatcher: action %s Postmark error %s: %s",
                action_id, exc.status_code, exc.body,
            )
            _mark_failed_and_log(action_id, to_email, str(exc))
        except Exception as exc:
            logger.error(
                "email_dispatcher: action %s unexpected error: %s",
                action_id, exc, exc_info=True,
            )
            _mark_failed_and_log(action_id, to_email, str(exc))


def _mark_failed_and_log(action_id: str, to_email: str, error: str) -> None:
    """
    Best-effort: mark the action as email_failed and write a health_log.
    Called from a sync context inside _tick(); wraps with asyncio.create_task
    to avoid blocking the event loop.
    """
    async def _do() -> None:
        try:
            await asyncio.to_thread(
                db_actions.mark_email_dispatched, action_id, False, error
            )
        except Exception as exc:
            logger.error(
                "email_dispatcher: could not mark action %s failed: %s",
                action_id, exc, exc_info=True,
            )
        try:
            await asyncio.to_thread(
                db_health_logs.create,
                HealthLogCreate(
                    service="email_dispatcher",
                    event_type="error",
                    message=f"action {action_id} send failed: {error}",
                    metadata={"action_id": action_id, "to_email": to_email},
                ),
            )
        except Exception as exc:
            logger.error(
                "email_dispatcher: could not write health_log for action %s: %s",
                action_id, exc, exc_info=True,
            )

    asyncio.get_event_loop().create_task(_do())


async def _loop() -> None:
    logger.info(
        "email_dispatcher: running (poll_interval=%ds)",
        settings.email_poll_interval_seconds,
    )
    while True:
        await asyncio.sleep(settings.email_poll_interval_seconds)
        try:
            await _tick()
        except Exception as exc:
            logger.error("email_dispatcher: unhandled error in _tick: %s", exc, exc_info=True)


def start() -> None:
    """Start the email dispatcher background task. No-op if POSTMARK_SERVER_TOKEN is not set."""
    global _task

    if not settings.postmark_server_token:
        logger.warning(
            "POSTMARK_SERVER_TOKEN is not set — email dispatcher will not run. "
            "Set this in .env to enable outbound email delivery."
        )
        return

    _task = asyncio.get_event_loop().create_task(_loop())
    logger.info("email_dispatcher: started")


def stop() -> None:
    """Cancel the email dispatcher background task on shutdown."""
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
        logger.info("email_dispatcher: stopped")
