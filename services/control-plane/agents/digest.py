"""
Digest agent — generates and sends Jake's daily morning summary.

Queries yesterday's events (midnight-to-midnight Pacific), today's scheduled
reminders, and overnight system errors. Passes the structured data to Claude
to generate a plain-text morning message, then sends it via Sendblue.

Called by scheduler/digest.py on a configurable daily schedule.
"""

import asyncio
import logging
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import anthropic

import db.actions as db_actions
import db.digest_queries as db_digest
import db.health_logs as db_health_logs
from connectors.sendblue_send import build_status_callback_url, send_message
from db.schemas import ActionCreate, HealthLogCreate

logger = logging.getLogger(__name__)

_PACIFIC = ZoneInfo("America/Los_Angeles")

# Patchable at the module level — mocked in tests.
_anthropic = anthropic.AsyncAnthropic()

_SYSTEM_PROMPT = (
    "You are Jake's personal assistant. Generate a concise morning digest in plain text. "
    "No markdown. No bullet points — use a simple dash list only if listing items. "
    "Be brief and useful. Start with 'Good morning.' Keep the whole message under 120 words."
)

_COST_PER_INPUT_TOKEN = Decimal("3") / Decimal("1_000_000")
_COST_PER_OUTPUT_TOKEN = Decimal("15") / Decimal("1_000_000")


def _yesterday_window() -> tuple[datetime, datetime]:
    """Return (midnight_yesterday, midnight_today) in UTC for the Pacific calendar day."""
    today_pt = datetime.now(_PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_pt = today_pt - timedelta(days=1)
    # Convert to UTC for DB queries
    return yesterday_pt.astimezone(datetime.now().astimezone().tzinfo.__class__(0)).replace(
        tzinfo=None
    ), today_pt.astimezone(datetime.now().astimezone().tzinfo.__class__(0)).replace(tzinfo=None)


def _midnight_window_utc() -> tuple[datetime, datetime]:
    """
    Return (yesterday_midnight_utc, today_midnight_utc) for the Pacific calendar day.
    """
    from datetime import timezone
    today_pt = datetime.now(_PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_pt = today_pt - timedelta(days=1)
    return yesterday_pt.astimezone(timezone.utc), today_pt.astimezone(timezone.utc)


def _today_window_utc() -> tuple[datetime, datetime]:
    """Return (today_midnight_utc, tomorrow_midnight_utc) for the Pacific calendar day."""
    from datetime import timezone
    today_pt = datetime.now(_PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_pt = today_pt + timedelta(days=1)
    return today_pt.astimezone(timezone.utc), tomorrow_pt.astimezone(timezone.utc)


def _build_data_block(
    yesterday_date: str,
    today_date: str,
    events: list[dict],
    today_tasks: list[dict],
    errors: list[dict],
) -> str:
    """Build the structured data block passed to Claude as the user message."""
    lines = [f"Morning digest data — {today_date}", ""]

    # Yesterday's events
    total = len(events)
    by_source = Counter(e["source"] for e in events)
    breakdown = ", ".join(f"{src}: {cnt}" for src, cnt in sorted(by_source.items()))
    lines.append(f"YESTERDAY ({yesterday_date}):")
    if total == 0:
        lines.append("  No inbound events.")
    else:
        lines.append(f"  {total} event(s) received — {breakdown}")
    lines.append("")

    # Today's reminders
    lines.append(f"TODAY'S REMINDERS ({today_date}):")
    if not today_tasks:
        lines.append("  No reminders scheduled for today.")
    else:
        for task in today_tasks:
            from datetime import timezone
            dt = datetime.fromisoformat(task["scheduled_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            pt = dt.astimezone(_PACIFIC)
            time_str = pt.strftime("%I:%M %p").lstrip("0")
            content = task["content"].removeprefix("Reminder: ")
            status = "" if task["status"] == "pending" else f" [{task['status']}]"
            lines.append(f"  - {content} at {time_str} PT{status}")
    lines.append("")

    # Overnight errors
    lines.append("SYSTEM HEALTH (last 24h):")
    if not errors:
        lines.append("  No errors.")
    else:
        lines.append(f"  {len(errors)} error(s):")
        for err in errors[:5]:  # cap at 5 to keep the prompt bounded
            lines.append(f"  - [{err['service']}] {err['message'][:80]}")
        if len(errors) > 5:
            lines.append(f"  ... and {len(errors) - 5} more.")
    lines.append("")

    lines.append("Write a brief morning message for Jake based on the above data.")
    return "\n".join(lines)


async def send_daily_digest(phone: str) -> None:
    """
    Generate and send the daily digest to phone.
    Errors are logged but never re-raised — the scheduler loop handles retries.
    """
    try:
        await _generate_and_send(phone)
    except Exception as exc:
        logger.error("digest: failed for %s: %s", phone, exc, exc_info=True)
        try:
            await asyncio.to_thread(
                db_health_logs.create,
                HealthLogCreate(
                    service="digest",
                    event_type="error",
                    message=f"digest failed: {exc}",
                    metadata={"phone": phone},
                ),
            )
        except Exception:
            logger.error("digest: failed to write health_log", exc_info=True)


async def _generate_and_send(phone: str) -> None:
    yesterday_start, today_start = _midnight_window_utc()
    today_end = _today_window_utc()[1]

    yesterday_date = yesterday_start.astimezone(_PACIFIC).strftime("%A, %B %-d")
    today_date = today_start.astimezone(_PACIFIC).strftime("%A, %B %-d")

    # Fetch all data concurrently.
    events, today_tasks, errors = await asyncio.gather(
        asyncio.to_thread(db_digest.events_in_window, yesterday_start, today_start),
        asyncio.to_thread(db_digest.scheduled_for_window, phone, today_start, today_end),
        asyncio.to_thread(db_digest.errors_since, yesterday_start),
    )

    data_block = _build_data_block(yesterday_date, today_date, events, today_tasks, errors)
    logger.debug("digest: data block:\n%s", data_block)

    response = await _anthropic.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": data_block}],
    )

    message_text = response.content[0].text
    token_input = response.usage.input_tokens
    token_output = response.usage.output_tokens
    usd_cost = (
        Decimal(token_input) * _COST_PER_INPUT_TOKEN
        + Decimal(token_output) * _COST_PER_OUTPUT_TOKEN
    )

    await send_message(
        to_number=phone,
        content=message_text,
        status_callback=build_status_callback_url(),
    )

    await asyncio.to_thread(
        db_actions.create,
        ActionCreate(
            agent="chat",
            action_type="daily_digest",
            token_input=token_input,
            token_output=token_output,
            usd_cost=usd_cost,
            payload={"phone": phone, "events": len(events), "errors": len(errors)},
        ),
    )

    logger.info(
        "digest: sent to=%s events=%d reminders=%d errors=%d tokens=%d+%d cost=$%.6f",
        phone, len(events), len(today_tasks), len(errors), token_input, token_output, usd_cost,
    )
