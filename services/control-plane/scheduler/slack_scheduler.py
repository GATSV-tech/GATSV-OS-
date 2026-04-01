"""
Slack scheduler.

Runs two loops:

1. Poll loop (every slack_poll_interval_seconds):
   - Post any unnotified open approvals to the ops channel.
   - Post error alerts for any health_log errors since the last tick.

2. Summary loop:
   - Sleeps until the configured summary time each day (Pacific).
   - Fires post_daily_summary().
   - Recalculates the next target after each send.

Lifecycle: start() is called from the FastAPI lifespan; stop() cancels both
background tasks on shutdown. If SLACK_BOT_TOKEN is not configured, start()
logs a warning and does nothing — Slack is optional in development.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from agents import slack_surface
from config import settings

logger = logging.getLogger(__name__)

_PACIFIC = ZoneInfo("America/Los_Angeles")

_poll_task: asyncio.Task | None = None
_summary_task: asyncio.Task | None = None


# ─── Poll loop ────────────────────────────────────────────────────────────────

async def _poll_loop() -> None:
    logger.info(
        "slack scheduler: poll loop started (interval=%ds)",
        settings.slack_poll_interval_seconds,
    )
    last_error_check = datetime.now(timezone.utc)

    while True:
        await asyncio.sleep(settings.slack_poll_interval_seconds)

        # Post any unnotified approvals (idempotent — only posts notified_at=null rows).
        try:
            posted = await slack_surface.post_approvals_queue()
            if posted:
                logger.info("slack scheduler: posted %d approval(s)", posted)
        except Exception as exc:
            logger.error("slack scheduler: approval poll error: %s", exc, exc_info=True)

        # Alert on errors since last check.
        now = datetime.now(timezone.utc)
        try:
            import db.slack_queries as db_slack
            errors = await asyncio.to_thread(db_slack.recent_errors, last_error_check)
            if errors:
                await slack_surface.post_error_alert(errors)
        except Exception as exc:
            logger.error("slack scheduler: error alert poll failed: %s", exc, exc_info=True)
        finally:
            last_error_check = now


# ─── Summary loop ────────────────────────────────────────────────────────────

def _parse_summary_time() -> tuple[int, int]:
    """Parse slack_summary_time_pt into (hour, minute), defaulting to 08:00."""
    raw = settings.slack_summary_time_pt
    try:
        hour, minute = map(int, raw.split(":"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, AttributeError):
        pass
    logger.warning(
        "slack scheduler: invalid summary time '%s', defaulting to 08:00", raw
    )
    return 8, 0


def _seconds_until_next(hour: int, minute: int) -> float:
    now_pt = datetime.now(_PACIFIC)
    target = now_pt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now_pt:
        target += timedelta(days=1)
    return (target - now_pt).total_seconds()


async def _summary_loop() -> None:
    logger.info("slack scheduler: summary loop started")
    while True:
        hour, minute = _parse_summary_time()
        sleep_secs = _seconds_until_next(hour, minute)
        logger.info(
            "slack scheduler: next summary in %.0fs (%02d:%02d PT)",
            sleep_secs, hour, minute,
        )
        await asyncio.sleep(sleep_secs)
        await slack_surface.post_daily_summary()


# ─── Lifecycle ────────────────────────────────────────────────────────────────

def start() -> None:
    """Start both Slack scheduler tasks. No-op if SLACK_BOT_TOKEN is not set."""
    global _poll_task, _summary_task

    if not settings.slack_bot_token:
        logger.warning(
            "SLACK_BOT_TOKEN is not set — Slack scheduler will not run. "
            "Set this in .env to enable the operator surface."
        )
        return

    if not settings.slack_ops_channel_id:
        logger.warning(
            "SLACK_OPS_CHANNEL_ID is not set — Slack scheduler will not run."
        )
        return

    loop = asyncio.get_event_loop()
    _poll_task = loop.create_task(_poll_loop())
    _summary_task = loop.create_task(_summary_loop())
    logger.info("slack scheduler: started")


def stop() -> None:
    """Cancel Slack scheduler tasks on shutdown."""
    global _poll_task, _summary_task
    for task in (_poll_task, _summary_task):
        if task is not None:
            task.cancel()
    _poll_task = None
    _summary_task = None
    logger.info("slack scheduler: stopped")
