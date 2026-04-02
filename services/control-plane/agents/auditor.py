"""
Auditor agent — monitors cost, error rate, and stale approvals.

Runs every AUDITOR_INTERVAL_SECONDS (default 15 minutes) via the auditor
scheduler. When thresholds are breached, posts Block Kit alerts to Slack.
Never raises — all errors are logged and written to health_logs.

Alert conditions:
  1. Daily cost exceeds COST_ALERT_THRESHOLD_CENTS.
  2. More than AUDITOR_ERROR_RATE_THRESHOLD errors in the last interval.
  3. Any approval has been open longer than AUDITOR_STALE_APPROVAL_MINUTES.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db.actions as db_actions
import db.audit_queries as db_audit
import db.health_logs as db_health_logs
import connectors.slack as slack_connector
from config import settings
from db.schemas import ActionCreate, HealthLogCreate

logger = logging.getLogger(__name__)

_PACIFIC = ZoneInfo("America/Los_Angeles")


async def run_audit(last_check: datetime) -> None:
    """
    Run a full audit pass and post Slack alerts for any threshold breaches.

    last_check: the UTC timestamp of the previous auditor tick. Used to scope
    the error-rate window.

    Errors are caught per-check so one failure doesn't block the others.
    """
    if not _slack_configured():
        logger.debug("auditor: Slack not configured — skipping alert checks")
        return

    await asyncio.gather(
        _check_cost(),
        _check_errors(last_check),
        _check_stale_approvals(),
        return_exceptions=True,  # log individually below
    )


async def _check_cost() -> None:
    """Alert if today's accumulated USD cost exceeds COST_ALERT_THRESHOLD_CENTS."""
    threshold_cents = settings.cost_alert_threshold_cents
    if threshold_cents <= 0:
        return

    try:
        midnight_utc = _midnight_utc_today()
        spent_usd = await asyncio.to_thread(db_audit.cost_since_midnight_utc, midnight_utc)
        spent_cents = spent_usd * 100

        logger.debug("auditor: cost_check spent=%.2f¢ threshold=%d¢", spent_cents, threshold_cents)

        if spent_cents < threshold_cents:
            return

        today_pt = datetime.now(_PACIFIC).strftime("%B %-d")
        text = (
            f"💸 *Daily cost alert* — ${spent_usd:.2f} spent today ({today_pt}), "
            f"threshold is ${threshold_cents / 100:.2f}"
        )

        await asyncio.to_thread(
            slack_connector.post_message,
            channel=settings.slack_ops_channel_id,
            text=text,
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
                }
            ],
        )

        await asyncio.to_thread(
            db_actions.create,
            ActionCreate(
                agent="auditor",
                action_type="auditor_cost_alert",
                payload={
                    "spent_usd": round(spent_usd, 6),
                    "threshold_cents": threshold_cents,
                    "date_pt": today_pt,
                },
            ),
        )

        logger.info(
            "auditor: cost alert posted spent=$%.4f threshold=$%.2f",
            spent_usd, threshold_cents / 100,
        )

    except Exception as exc:
        logger.error("auditor: _check_cost failed: %s", exc, exc_info=True)
        await _write_health_log("error", f"cost check failed: {exc}", {})


async def _check_errors(last_check: datetime) -> None:
    """Alert if the number of new errors since last_check exceeds the threshold."""
    threshold = settings.auditor_error_rate_threshold
    if threshold <= 0:
        return

    try:
        errors = await asyncio.to_thread(db_audit.error_rows, last_check)
        count = len(errors)

        logger.debug("auditor: error_check new_errors=%d threshold=%d", count, threshold)

        if count < threshold:
            return

        services = sorted({e.get("service", "?") for e in errors})
        services_str = ", ".join(services)
        error_lines = "\n".join(
            f"• `[{e.get('service', '?')}]` {(e.get('message') or '')[:100]}"
            for e in errors[:5]
        )
        if count > 5:
            error_lines += f"\n_...and {count - 5} more_"

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🚨 Error spike: {count} errors (threshold: {threshold})",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Services:* {services_str}\n\n{error_lines}",
                },
            },
        ]

        await asyncio.to_thread(
            slack_connector.post_message,
            channel=settings.slack_ops_channel_id,
            text=f"🚨 Error spike: {count} errors since last check. Services: {services_str}",
            blocks=blocks,
        )

        await asyncio.to_thread(
            db_actions.create,
            ActionCreate(
                agent="auditor",
                action_type="auditor_error_alert",
                payload={
                    "error_count": count,
                    "threshold": threshold,
                    "services": services,
                },
            ),
        )

        logger.info("auditor: error alert posted count=%d threshold=%d", count, threshold)

    except Exception as exc:
        logger.error("auditor: _check_errors failed: %s", exc, exc_info=True)
        await _write_health_log("error", f"error check failed: {exc}", {})


async def _check_stale_approvals() -> None:
    """Alert if any open approval has been pending longer than AUDITOR_STALE_APPROVAL_MINUTES."""
    stale_minutes = settings.auditor_stale_approval_minutes
    if stale_minutes <= 0:
        return

    try:
        stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
        stale = await asyncio.to_thread(db_audit.stale_approvals, stale_cutoff)
        count = len(stale)

        logger.debug("auditor: stale_check stale_count=%d threshold=%dm", count, stale_minutes)

        if count == 0:
            return

        lines = []
        for appr in stale[:5]:
            summary = (appr.get("summary") or "no summary")[:80]
            created_at = appr.get("created_at", "")
            lines.append(f"• {summary} _(pending since {created_at[:16].replace('T', ' ')} UTC)_")
        if count > 5:
            lines.append(f"• _...and {count - 5} more_")

        text_lines = "\n".join(lines)
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"⏰ {count} stale approval{'s' if count > 1 else ''} (>{stale_minutes}m)",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text_lines},
            },
        ]

        await asyncio.to_thread(
            slack_connector.post_message,
            channel=settings.slack_ops_channel_id,
            text=f"⏰ {count} stale approval(s) awaiting decision (pending >{stale_minutes}m)",
            blocks=blocks,
        )

        await asyncio.to_thread(
            db_actions.create,
            ActionCreate(
                agent="auditor",
                action_type="auditor_stale_approval_alert",
                payload={
                    "stale_count": count,
                    "stale_minutes": stale_minutes,
                    "approval_ids": [a.get("id") for a in stale[:10]],
                },
            ),
        )

        logger.info("auditor: stale approval alert posted count=%d", count)

    except Exception as exc:
        logger.error("auditor: _check_stale_approvals failed: %s", exc, exc_info=True)
        await _write_health_log("error", f"stale approval check failed: {exc}", {})


def _midnight_utc_today() -> datetime:
    """Return midnight UTC for the current Pacific calendar day."""
    today_pt = datetime.now(_PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    return today_pt.astimezone(timezone.utc)


def _slack_configured() -> bool:
    if not settings.slack_bot_token:
        logger.warning("auditor: SLACK_BOT_TOKEN not set — skipping audit alerts")
        return False
    if not settings.slack_ops_channel_id:
        logger.warning("auditor: SLACK_OPS_CHANNEL_ID not set — skipping audit alerts")
        return False
    return True


async def _write_health_log(event_type: str, message: str, metadata: dict) -> None:
    try:
        await asyncio.to_thread(
            db_health_logs.create,
            HealthLogCreate(
                service="auditor",
                event_type=event_type,
                message=message,
                metadata=metadata,
            ),
        )
    except Exception as exc:
        logger.error("auditor: failed to write health_log: %s", exc, exc_info=True)
