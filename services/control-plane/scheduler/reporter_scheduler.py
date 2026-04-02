"""
Reporter scheduler — sends a weekly ops digest to both iMessage and Slack.

Sends every Sunday at the configured time (REPORTER_WEEKLY_SEND_TIME_PT,
default "09:00" Pacific). Waits for the next Sunday on each cycle.

Lifecycle: start() is called from the FastAPI lifespan; stop() cancels the
background task on shutdown. If JAKE_PHONE_NUMBER is not set and Slack is not
configured, start() logs a warning and does nothing.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from agents import reporter
from config import settings

logger = logging.getLogger(__name__)

_PACIFIC = ZoneInfo("America/Los_Angeles")
_WEEKLY_WINDOW_HOURS = 168  # 7 days

_task: asyncio.Task | None = None


def _parse_send_time() -> tuple[int, int]:
    """Parse REPORTER_WEEKLY_SEND_TIME_PT into (hour, minute), defaulting to 09:00."""
    raw = settings.reporter_weekly_send_time_pt
    try:
        hour, minute = map(int, raw.split(":"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, AttributeError):
        pass
    logger.warning(
        "reporter_scheduler: invalid send time '%s', defaulting to 09:00", raw
    )
    return 9, 0


def _seconds_until_next_sunday(hour: int, minute: int) -> float:
    """Return seconds until the next Sunday at (hour, minute) Pacific."""
    now_pt = datetime.now(_PACIFIC)
    # Monday=0, Sunday=6
    days_until_sunday = (6 - now_pt.weekday()) % 7
    target = now_pt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    target += timedelta(days=days_until_sunday)
    # If today is Sunday and the time has already passed, advance to next Sunday.
    if target <= now_pt:
        target += timedelta(days=7)
    return (target - now_pt).total_seconds()


async def _loop() -> None:
    logger.info("reporter scheduler: started")
    while True:
        hour, minute = _parse_send_time()
        sleep_secs = _seconds_until_next_sunday(hour, minute)
        next_dt = datetime.now(_PACIFIC).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        logger.info(
            "reporter scheduler: next weekly digest in %.0fs (Sunday %02d:%02d PT)",
            sleep_secs, hour, minute,
        )
        await asyncio.sleep(sleep_secs)

        phone = settings.jake_phone_number
        slack = bool(settings.slack_bot_token and settings.slack_ops_channel_id)

        if not phone and not slack:
            logger.warning("reporter scheduler: no delivery target — skipping")
            continue

        await reporter.send_digest(
            window_hours=_WEEKLY_WINDOW_HOURS,
            phone=phone or None,
            slack=slack,
        )
        logger.info("reporter scheduler: weekly digest sent phone=%s slack=%s", phone, slack)


def start() -> None:
    """Start the reporter scheduler. No-op if no delivery targets are configured."""
    global _task

    phone = settings.jake_phone_number
    slack = bool(settings.slack_bot_token and settings.slack_ops_channel_id)

    if not phone and not slack:
        logger.warning(
            "reporter scheduler: JAKE_PHONE_NUMBER not set and Slack not configured "
            "— reporter scheduler will not run."
        )
        return

    _task = asyncio.get_event_loop().create_task(_loop())
    logger.info("reporter scheduler: started")


def stop() -> None:
    """Cancel the reporter scheduler on shutdown."""
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
        logger.info("reporter scheduler: stopped")
