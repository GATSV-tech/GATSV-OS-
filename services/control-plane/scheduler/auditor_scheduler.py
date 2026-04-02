"""
Auditor scheduler — runs the Auditor agent on a fixed interval.

Checks cost thresholds, error-rate spikes, and stale approvals every
AUDITOR_INTERVAL_SECONDS (default 900 = 15 minutes). Alerts are posted to
Slack by the Auditor agent.

Lifecycle: start() is called from the FastAPI lifespan; stop() cancels the
background task on shutdown. If Slack is not configured, the Auditor will skip
all checks silently — see agents/auditor.py.
"""

import asyncio
import logging
from datetime import datetime, timezone

from agents import auditor
from config import settings

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


async def _loop() -> None:
    logger.info(
        "auditor scheduler: running (interval=%ds)",
        settings.auditor_interval_seconds,
    )
    last_check = datetime.now(timezone.utc)
    while True:
        await asyncio.sleep(settings.auditor_interval_seconds)
        now = datetime.now(timezone.utc)
        try:
            await auditor.run_audit(last_check=last_check)
        except Exception as exc:
            logger.error("auditor scheduler: unhandled error in run_audit: %s", exc, exc_info=True)
        finally:
            last_check = now


def start() -> None:
    """Start the auditor scheduler background task."""
    global _task
    _task = asyncio.get_event_loop().create_task(_loop())
    logger.info("auditor scheduler: started")


def stop() -> None:
    """Cancel the auditor scheduler on shutdown."""
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
        logger.info("auditor scheduler: stopped")
