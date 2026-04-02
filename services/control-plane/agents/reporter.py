"""
Reporter agent — generates operational digests on demand or on schedule.

Queries the DB for a configurable lookback window, builds a structured data
block, and calls Claude to produce a concise plain-text summary. The summary
can be delivered via:
  - iMessage (Sendblue) — used when the report tool is invoked from chat.py
  - Slack — used by the scheduled weekly digest
  - both  — used by the weekly scheduler

Entry points:
  generate_digest(window_hours)  → str  (plain text, no side effects)
  send_digest(window_hours, phone, slack)  → None  (sends and logs)
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import anthropic

import db.actions as db_actions
import db.health_logs as db_health_logs
import db.reporter_queries as db_reporter
from connectors import slack as slack_connector
from connectors.sendblue_send import build_status_callback_url, send_message
from config import settings
from db.schemas import ActionCreate, HealthLogCreate

logger = logging.getLogger(__name__)

_PACIFIC = ZoneInfo("America/Los_Angeles")

# Patchable at the module level — mocked in tests.
_anthropic = anthropic.AsyncAnthropic()

_SYSTEM_PROMPT = (
    "You are Jake's operations assistant. Generate a concise ops report in plain text. "
    "No markdown headers. Use a simple dash list for breakdowns. "
    "Be direct and factual — Jake is a founder scanning metrics, not reading an essay. "
    "Keep the whole report under 200 words."
)

_COST_PER_INPUT_TOKEN = Decimal("3") / Decimal("1_000_000")
_COST_PER_OUTPUT_TOKEN = Decimal("15") / Decimal("1_000_000")


def _window_utc(window_hours: int) -> tuple[datetime, datetime]:
    """Return (since, until) in UTC for the last `window_hours` hours."""
    until = datetime.now(timezone.utc)
    since = until - timedelta(hours=window_hours)
    return since, until


def _build_data_block(
    window_hours: int,
    since: datetime,
    until: datetime,
    event_by_bucket: dict[str, int],
    event_by_status: dict[str, int],
    action_counts: dict[str, int],
    costs: dict,
    open_approvals: list[dict],
    error_count: int,
    recent_errors: list[dict],
) -> str:
    """Build the structured data block passed to Claude as the user message."""
    since_pt = since.astimezone(_PACIFIC).strftime("%b %-d %-I:%M %p")
    until_pt = until.astimezone(_PACIFIC).strftime("%b %-d %-I:%M %p")

    lines = [f"Ops report — last {window_hours}h ({since_pt} → {until_pt} PT)", ""]

    # Events
    total_events = sum(event_by_bucket.values())
    lines.append(f"EVENTS: {total_events} total")
    if event_by_bucket:
        for bucket, cnt in sorted(event_by_bucket.items()):
            lines.append(f"  - {bucket}: {cnt}")
    else:
        lines.append("  - none")
    lines.append("")

    # Event status breakdown
    lines.append("EVENT STATUS:")
    if event_by_status:
        for status, cnt in sorted(event_by_status.items()):
            lines.append(f"  - {status}: {cnt}")
    else:
        lines.append("  - none")
    lines.append("")

    # Actions
    total_actions = sum(action_counts.values())
    lines.append(f"ACTIONS: {total_actions} total")
    if action_counts:
        for atype, cnt in sorted(action_counts.items()):
            lines.append(f"  - {atype}: {cnt}")
    else:
        lines.append("  - none")
    lines.append("")

    # Cost
    usd = costs.get("usd_cost", 0.0)
    token_in = costs.get("token_input", 0)
    token_out = costs.get("token_output", 0)
    lines.append(f"COST: ${usd:.4f} USD ({token_in} in / {token_out} out tokens)")
    lines.append("")

    # Open approvals
    lines.append(f"OPEN APPROVALS: {len(open_approvals)}")
    for appr in open_approvals[:3]:
        summary = (appr.get("summary") or "no summary")[:80]
        lines.append(f"  - {summary}")
    if len(open_approvals) > 3:
        lines.append(f"  - ... and {len(open_approvals) - 3} more")
    lines.append("")

    # Errors
    lines.append(f"ERRORS (last {window_hours}h): {error_count}")
    for err in recent_errors[:3]:
        service = err.get("service", "?")
        msg = (err.get("message") or "")[:80]
        lines.append(f"  - [{service}] {msg}")
    if error_count > 3:
        lines.append(f"  - ... and {error_count - 3} more")
    lines.append("")

    lines.append("Write a brief ops summary for Jake based on the above data.")
    return "\n".join(lines)


async def generate_digest(window_hours: int = 24) -> str:
    """
    Query the DB and use Claude to generate a plain-text ops digest.
    Returns the digest text. No side effects.

    Raises on DB or API errors — callers should catch and handle.
    """
    since, until = _window_utc(window_hours)

    (
        event_by_bucket,
        event_by_status,
        action_counts,
        costs,
        open_approvals,
        error_count,
        recent_errors,
    ) = await asyncio.gather(
        asyncio.to_thread(db_reporter.event_counts_by_bucket, since, until),
        asyncio.to_thread(db_reporter.event_counts_by_status, since, until),
        asyncio.to_thread(db_reporter.action_counts, since, until),
        asyncio.to_thread(db_reporter.cost_totals, since, until),
        asyncio.to_thread(db_reporter.open_approvals),
        asyncio.to_thread(db_reporter.error_count, since),
        asyncio.to_thread(db_reporter.recent_errors, since),
    )

    data_block = _build_data_block(
        window_hours=window_hours,
        since=since,
        until=until,
        event_by_bucket=event_by_bucket,
        event_by_status=event_by_status,
        action_counts=action_counts,
        costs=costs,
        open_approvals=open_approvals,
        error_count=error_count,
        recent_errors=recent_errors,
    )
    logger.debug("reporter: data block:\n%s", data_block)

    response = await _anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": data_block}],
    )

    return response.content[0].text


async def send_digest(
    window_hours: int = 24,
    phone: str | None = None,
    slack: bool = False,
) -> None:
    """
    Generate and deliver a digest.

    phone: if set, sends via Sendblue iMessage to this number.
    slack: if True, posts to the configured Slack ops channel.

    At least one of phone or slack must be set.
    Errors are logged and written to health_logs but never re-raised.
    """
    if not phone and not slack:
        logger.warning("reporter.send_digest: called with no delivery target — skipping")
        return

    try:
        await _send_digest(window_hours=window_hours, phone=phone, slack=slack)
    except Exception as exc:
        logger.error("reporter: send_digest failed: %s", exc, exc_info=True)
        try:
            await asyncio.to_thread(
                db_health_logs.create,
                HealthLogCreate(
                    service="reporter",
                    event_type="error",
                    message=f"send_digest failed: {exc}",
                    metadata={"window_hours": window_hours, "phone": phone, "slack": slack},
                ),
            )
        except Exception:
            logger.error("reporter: failed to write health_log", exc_info=True)


async def _send_digest(
    window_hours: int,
    phone: str | None,
    slack: bool,
) -> None:
    since, until = _window_utc(window_hours)

    (
        event_by_bucket,
        event_by_status,
        action_counts,
        costs,
        open_approvals,
        error_count,
        recent_errors,
    ) = await asyncio.gather(
        asyncio.to_thread(db_reporter.event_counts_by_bucket, since, until),
        asyncio.to_thread(db_reporter.event_counts_by_status, since, until),
        asyncio.to_thread(db_reporter.action_counts, since, until),
        asyncio.to_thread(db_reporter.cost_totals, since, until),
        asyncio.to_thread(db_reporter.open_approvals),
        asyncio.to_thread(db_reporter.error_count, since),
        asyncio.to_thread(db_reporter.recent_errors, since),
    )

    data_block = _build_data_block(
        window_hours=window_hours,
        since=since,
        until=until,
        event_by_bucket=event_by_bucket,
        event_by_status=event_by_status,
        action_counts=action_counts,
        costs=costs,
        open_approvals=open_approvals,
        error_count=error_count,
        recent_errors=recent_errors,
    )

    response = await _anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": data_block}],
    )

    text = response.content[0].text
    token_input = response.usage.input_tokens
    token_output = response.usage.output_tokens
    usd_cost = (
        Decimal(token_input) * _COST_PER_INPUT_TOKEN
        + Decimal(token_output) * _COST_PER_OUTPUT_TOKEN
    )

    sends: list[asyncio.Task] = []
    if phone:
        sends.append(asyncio.create_task(
            send_message(
                to_number=phone,
                content=text,
                status_callback=build_status_callback_url(),
            )
        ))
    if slack and _slack_configured():
        sends.append(asyncio.create_task(
            asyncio.to_thread(
                slack_connector.post_message,
                channel=settings.slack_ops_channel_id,
                text=text,
            )
        ))

    if sends:
        results = await asyncio.gather(*sends, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("reporter: delivery %d failed: %s", i, result, exc_info=True)

    await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="reporter",
            action_type="reporter_digest",
            token_input=token_input,
            token_output=token_output,
            usd_cost=usd_cost,
            payload={
                "window_hours": window_hours,
                "phone": phone,
                "slack": slack,
                "total_events": sum(event_by_bucket.values()),
                "open_approvals": len(open_approvals),
                "error_count": error_count,
            },
        ),
    )

    logger.info(
        "reporter: digest sent window_hours=%d phone=%s slack=%s tokens=%d+%d cost=$%.6f",
        window_hours, phone, slack, token_input, token_output, usd_cost,
    )


def _slack_configured() -> bool:
    if not settings.slack_bot_token:
        logger.warning("reporter: SLACK_BOT_TOKEN not set — skipping Slack post")
        return False
    if not settings.slack_ops_channel_id:
        logger.warning("reporter: SLACK_OPS_CHANNEL_ID not set — skipping Slack post")
        return False
    return True
