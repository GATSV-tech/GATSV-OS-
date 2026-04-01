"""
Daily digest scheduler.

Sleeps until the configured send time each day (Pacific), fires the digest,
then recalculates the next target. Send time is read fresh each cycle so a
daily_brief tool call takes effect the following morning without a restart.

Lifecycle: start(phone) is called from the FastAPI lifespan; stop() cancels
the background task on shutdown. If JAKE_PHONE_NUMBER is not configured,
start() logs a warning and does nothing.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db.user_prefs as db_prefs
from agents import digest as digest_agent
from config import settings

logger = logging.getLogger(__name__)

_PACIFIC = ZoneInfo("America/Los_Angeles")
_PREF_KEY = "digest_send_time_pt"

_task: asyncio.Task | None = None


async def _get_send_time(phone: str) -> tuple[int, int]:
    """
    Read the send time for phone from user_prefs, falling back to config default.
    Returns (hour, minute) in 24h Pacific time.
    """
    pref = await asyncio.to_thread(db_prefs.get_pref, phone, _PREF_KEY)
    raw = pref if pref else settings.digest_send_time_pt
    try:
        hour, minute = map(int, raw.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return hour, minute
    except (ValueError, AttributeError):
        logger.warning(
            "digest scheduler: invalid send time '%s', falling back to 07:00", raw
        )
        return 7, 0


def _seconds_until_next(hour: int, minute: int) -> float:
    """Seconds until the next occurrence of HH:MM Pacific time."""
    now_pt = datetime.now(_PACIFIC)
    target = now_pt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now_pt:
        target += timedelta(days=1)
    return (target - now_pt).total_seconds()


async def _loop(phone: str) -> None:
    logger.info("digest scheduler: started for %s", phone)
    while True:
        hour, minute = await _get_send_time(phone)
        sleep_secs = _seconds_until_next(hour, minute)
        logger.info(
            "digest scheduler: next send in %.0fs (%02d:%02d PT)", sleep_secs, hour, minute
        )
        await asyncio.sleep(sleep_secs)
        await digest_agent.send_daily_digest(phone)


def start(phone: str) -> None:
    """Start the digest scheduler for the given phone number."""
    global _task
    _task = asyncio.get_event_loop().create_task(_loop(phone))
    logger.info("digest scheduler: task created")


def stop() -> None:
    """Cancel the digest scheduler task on shutdown."""
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
        logger.info("digest scheduler: stopped")
