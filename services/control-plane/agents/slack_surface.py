"""
Slack operator surface.

Three outbound responsibilities:
  1. post_approvals_queue()   — post all unnotified open approvals to the ops channel
                                with approve/reject Block Kit buttons. Marks each
                                notified_at after posting.
  2. post_daily_summary()     — post a structured operational summary (events, costs,
                                open approvals count) to the ops channel.
  3. post_error_alert(errors) — post a warning block for one or more error log entries.

Each function:
  - Requires SLACK_BOT_TOKEN and SLACK_OPS_CHANNEL_ID to be configured.
    If either is absent, logs a warning and returns without raising.
  - Writes an action row and health_log for every outcome.
  - Never raises — callers (the scheduler) rely on this.

All Supabase calls are synchronous and wrapped in asyncio.to_thread.
Slack HTTP calls are synchronous (httpx) and also wrapped in asyncio.to_thread.

Action attribution: approval-related actions → agent="operator";
summary/alert actions → agent="reporter".
These are the closest-fit existing agents in the schema check constraint.
"""

import asyncio
import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db.actions as db_actions
import db.approvals as db_approvals
import db.health_logs as db_health_logs
import db.slack_queries as db_slack
import connectors.slack as slack_connector
from config import settings
from db.schemas import ActionCreate, HealthLogCreate

logger = logging.getLogger(__name__)

_PACIFIC = ZoneInfo("America/Los_Angeles")


# ─── Approvals queue ─────────────────────────────────────────────────────────

async def post_approvals_queue() -> int:
    """
    Fetch all unnotified open approvals and post each to the ops channel.
    Returns the number of approvals posted.
    Silently skips posting if Slack is not configured.
    Errors on individual approvals are logged and do not block the rest.
    """
    if not _slack_configured():
        return 0

    try:
        approvals = await asyncio.to_thread(db_approvals.list_unnotified)
    except Exception as exc:
        logger.error("slack_surface: failed to load unnotified approvals: %s", exc, exc_info=True)
        return 0

    posted = 0
    for approval in approvals:
        try:
            await _post_one_approval(approval)
            posted += 1
        except Exception as exc:
            logger.error(
                "slack_surface: failed to post approval %s: %s",
                approval.get("id"), exc, exc_info=True,
            )
            await _write_health_log(
                service="slack_surface",
                event_type="error",
                message=f"failed to post approval {approval.get('id')}: {exc}",
                metadata={"approval_id": approval.get("id")},
            )

    if posted:
        logger.info("slack_surface: posted %d approval(s) to Slack", posted)
    return posted


async def _post_one_approval(approval: dict) -> None:
    approval_id = approval["id"]
    ctx = approval.get("context", {})
    summary = approval.get("summary", "Approval required")

    blocks = _build_approval_blocks(approval_id, summary, ctx)

    await asyncio.to_thread(
        slack_connector.post_message,
        channel=settings.slack_ops_channel_id,
        text=f"⏳ Approval required: {summary[:80]}",
        blocks=blocks,
    )

    await asyncio.to_thread(db_approvals.mark_notified, approval_id)

    await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="operator",
            action_type="slack_post_approval",
            payload={
                "approval_id": approval_id,
                "channel": settings.slack_ops_channel_id,
                "summary_preview": summary[:120],
            },
        ),
    )


def _build_approval_blocks(approval_id: str, summary: str, ctx: dict) -> list[dict]:
    """Build Block Kit blocks for an approval request."""
    fields = []
    if ctx.get("to_email"):
        fields.append({"type": "mrkdwn", "text": f"*To:*\n{ctx['to_email']}"})
    if ctx.get("bucket"):
        fields.append({"type": "mrkdwn", "text": f"*Bucket:*\n{ctx['bucket']}"})
    if ctx.get("priority"):
        fields.append({"type": "mrkdwn", "text": f"*Priority:*\n{ctx['priority']}"})
    if ctx.get("event_subject"):
        fields.append({"type": "mrkdwn", "text": f"*Subject:*\n{ctx['event_subject']}"})

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "⏳ Approval Required", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{summary}*"},
        },
    ]

    if fields:
        blocks.append({"type": "section", "fields": fields[:4]})  # Slack max 10, keep concise

    if ctx.get("body"):
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Draft message:*\n```{ctx['body'][:300]}```",
            },
        })

    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "✅ Approve", "emoji": True},
                "style": "primary",
                "action_id": f"approve_{approval_id}",
                "value": approval_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "❌ Reject", "emoji": True},
                "style": "danger",
                "action_id": f"reject_{approval_id}",
                "value": approval_id,
            },
        ],
    })

    return blocks


# ─── Interaction handling (button callbacks) ─────────────────────────────────

async def handle_approval_action(
    action_id: str,
    approval_id: str,
    decided_by: str,
) -> dict:
    """
    Process an approve or reject button click from Slack.

    action_id must be 'approve_<uuid>' or 'reject_<uuid>'.
    decided_by is the Slack user ID of the founder who clicked.

    Returns {"ok": True, "decision": str} on success.
    Returns {"ok": False, "error": str} if the approval is not found or already decided.
    Never raises.
    """
    try:
        return await _apply_decision(action_id, approval_id, decided_by)
    except Exception as exc:
        logger.error(
            "slack_surface: handle_approval_action failed approval_id=%s: %s",
            approval_id, exc, exc_info=True,
        )
        await _write_health_log(
            service="slack_surface",
            event_type="error",
            message=f"approval action failed: {exc}",
            metadata={"approval_id": approval_id, "action_id": action_id},
        )
        return {"ok": False, "error": str(exc)}


async def _apply_decision(action_id: str, approval_id: str, decided_by: str) -> dict:
    from db.schemas import ApprovalDecision

    if action_id.startswith("approve_"):
        decision = "approved"
        new_action_status = "approved"
    elif action_id.startswith("reject_"):
        decision = "rejected"
        new_action_status = "rejected"
    else:
        return {"ok": False, "error": f"unknown action_id format: {action_id}"}

    # Fetch the approval to get its action_id FK.
    approval = await asyncio.to_thread(db_approvals.get_by_id, approval_id)
    if approval is None:
        return {"ok": False, "error": f"approval not found: {approval_id}"}
    if approval.get("decision") is not None:
        return {"ok": False, "error": f"approval already decided: {approval['decision']}"}

    decided_at = datetime.now(timezone.utc)

    # Update approval row.
    await asyncio.to_thread(
        db_approvals.update_decision,
        approval_id,
        ApprovalDecision(
            decision=decision,
            decided_by=decided_by,
            decided_at=decided_at,
        ),
    )

    # Update the linked action row.
    linked_action_id = approval.get("action_id")
    if linked_action_id:
        await asyncio.to_thread(
            db_actions.update_status,
            linked_action_id,
            new_action_status,
            decided_by,
        )

    # Audit action row for the decision itself.
    await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="operator",
            action_type=f"approval_{decision}",
            payload={
                "approval_id": approval_id,
                "linked_action_id": linked_action_id,
                "decided_by": decided_by,
            },
        ),
    )

    # Notify the channel about the decision.
    if _slack_configured():
        try:
            icon = "✅" if decision == "approved" else "❌"
            text = f"{icon} Approval *{decision}* by <@{decided_by}>"
            await asyncio.to_thread(
                slack_connector.post_message,
                channel=settings.slack_ops_channel_id,
                text=text,
            )
        except Exception as exc:
            logger.warning(
                "slack_surface: decision ack post failed (non-fatal): %s", exc
            )

    await _write_health_log(
        service="slack_surface",
        event_type="info",
        message=f"approval {decision}",
        metadata={
            "approval_id": approval_id,
            "linked_action_id": linked_action_id,
            "decided_by": decided_by,
        },
    )

    logger.info(
        "slack_surface: approval %s %s by %s",
        approval_id, decision, decided_by,
    )
    return {"ok": True, "decision": decision}


# ─── Daily summary ───────────────────────────────────────────────────────────

async def post_daily_summary() -> None:
    """
    Post an operational summary for the last 24 hours to the ops channel.
    Silently skips if Slack is not configured. Never raises.
    """
    if not _slack_configured():
        return

    start = time.monotonic()
    try:
        await _send_daily_summary()
    except Exception as exc:
        logger.error("slack_surface: daily summary failed: %s", exc, exc_info=True)
        await _write_health_log(
            service="slack_surface",
            event_type="error",
            message=f"daily summary failed: {exc}",
            metadata={},
        )


async def _send_daily_summary() -> None:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    until = datetime.now(timezone.utc)

    events, costs, open_count = await asyncio.gather(
        asyncio.to_thread(db_slack.events_summary, since, until),
        asyncio.to_thread(db_slack.actions_cost, since, until),
        asyncio.to_thread(db_slack.open_approvals_count),
    )

    total_events = len(events)
    by_bucket = Counter(e.get("bucket") or "unrouted" for e in events)
    actioned = sum(1 for e in events if e.get("status") == "actioned")

    today_pt = datetime.now(_PACIFIC).strftime("%A, %B %-d")
    usd = costs["usd_cost"]

    # Build block summary lines.
    bucket_lines = "\n".join(
        f"  • {bucket}: {cnt}" for bucket, cnt in sorted(by_bucket.items())
    ) or "  • none"

    text = (
        f"*Daily Ops Summary — {today_pt}*\n\n"
        f"*Events (last 24h):* {total_events} total, {actioned} actioned\n"
        f"{bucket_lines}\n\n"
        f"*Open approvals:* {open_count}\n"
        f"*Agent cost:* ${usd:.4f}"
    )

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📊 Daily Ops Summary — {today_pt}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Events (24h):*\n{total_events} total, {actioned} actioned"},
                {"type": "mrkdwn", "text": f"*Open approvals:*\n{open_count}"},
                {"type": "mrkdwn", "text": f"*Agent cost (24h):*\n${usd:.4f}"},
                {"type": "mrkdwn", "text": f"*By bucket:*\n{', '.join(f'{b}:{c}' for b,c in sorted(by_bucket.items())) or 'none'}"},
            ],
        },
        {"type": "divider"},
    ]

    await asyncio.to_thread(
        slack_connector.post_message,
        channel=settings.slack_ops_channel_id,
        text=text,
        blocks=blocks,
    )

    await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="reporter",
            action_type="slack_daily_summary",
            payload={
                "events_total": total_events,
                "actioned": actioned,
                "open_approvals": open_count,
                "usd_cost_24h": str(round(usd, 6)),
            },
        ),
    )

    await _write_health_log(
        service="slack_surface",
        event_type="info",
        message="daily summary posted",
        metadata={"events_total": total_events, "open_approvals": open_count},
    )

    logger.info(
        "slack_surface: daily summary posted events=%d actioned=%d open_approvals=%d cost=$%.4f",
        total_events, actioned, open_count, usd,
    )


# ─── Error alerts ─────────────────────────────────────────────────────────────

async def post_error_alert(errors: list[dict]) -> None:
    """
    Post a Slack alert for a batch of error log entries.
    errors: list of health_log rows with keys: service, message, created_at.
    Silently skips if Slack is not configured or the list is empty. Never raises.
    """
    if not errors or not _slack_configured():
        return

    try:
        await _send_error_alert(errors)
    except Exception as exc:
        logger.error("slack_surface: error alert failed: %s", exc, exc_info=True)
        # No health_log here — writing to health_logs during a health_log alert
        # could loop. Just log.


async def _send_error_alert(errors: list[dict]) -> None:
    count = len(errors)
    services = sorted({e.get("service", "unknown") for e in errors})
    services_str = ", ".join(services)

    # Build blocks — cap at 5 errors to keep message concise.
    error_lines = "\n".join(
        f"• `[{e.get('service', '?')}]` {e.get('message', '')[:100]}"
        for e in errors[:5]
    )
    if count > 5:
        error_lines += f"\n_...and {count - 5} more_"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🚨 System Error{'s' if count > 1 else ''} ({count})",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Services affected:* {services_str}\n\n{error_lines}",
            },
        },
    ]

    await asyncio.to_thread(
        slack_connector.post_message,
        channel=settings.slack_ops_channel_id,
        text=f"🚨 {count} system error(s) from: {services_str}",
        blocks=blocks,
    )

    await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="reporter",
            action_type="slack_error_alert",
            payload={"error_count": count, "services": services},
        ),
    )

    logger.info("slack_surface: error alert posted count=%d services=%s", count, services)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _slack_configured() -> bool:
    if not settings.slack_bot_token:
        logger.warning("slack_surface: SLACK_BOT_TOKEN not set — skipping Slack post")
        return False
    if not settings.slack_ops_channel_id:
        logger.warning("slack_surface: SLACK_OPS_CHANNEL_ID not set — skipping Slack post")
        return False
    return True


async def _write_health_log(
    service: str,
    event_type: str,
    message: str,
    metadata: dict,
) -> None:
    try:
        await asyncio.to_thread(
            db_health_logs.create,
            HealthLogCreate(
                service=service,
                event_type=event_type,
                message=message,
                metadata=metadata,
            ),
        )
    except Exception as exc:
        logger.error("slack_surface: failed to write health_log: %s", exc, exc_info=True)
