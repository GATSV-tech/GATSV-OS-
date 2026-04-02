"""
Read-only queries used by the Reporter agent to generate operational digests.
Synchronous — callers must wrap in asyncio.to_thread.
All datetime arguments must be timezone-aware UTC.
"""

import logging
from datetime import datetime

from db.client import get_client

logger = logging.getLogger(__name__)


def event_counts_by_bucket(since: datetime, until: datetime) -> dict[str, int]:
    """
    Return a mapping of bucket → event count for the given window.
    Unrouted events (bucket=null) are counted under 'unrouted'.
    """
    result = (
        get_client()
        .table("events")
        .select("bucket")
        .gte("created_at", since.isoformat())
        .lt("created_at", until.isoformat())
        .execute()
    )
    counts: dict[str, int] = {}
    for row in result.data:
        bucket = row.get("bucket") or "unrouted"
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def event_counts_by_status(since: datetime, until: datetime) -> dict[str, int]:
    """Return a mapping of status → event count for the given window."""
    result = (
        get_client()
        .table("events")
        .select("status")
        .gte("created_at", since.isoformat())
        .lt("created_at", until.isoformat())
        .execute()
    )
    counts: dict[str, int] = {}
    for row in result.data:
        status = row.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def action_counts(since: datetime, until: datetime) -> dict[str, int]:
    """Return a mapping of action_type → count for the given window."""
    result = (
        get_client()
        .table("actions")
        .select("action_type")
        .gte("created_at", since.isoformat())
        .lt("created_at", until.isoformat())
        .execute()
    )
    counts: dict[str, int] = {}
    for row in result.data:
        atype = row.get("action_type") or "unknown"
        counts[atype] = counts.get(atype, 0) + 1
    return counts


def cost_totals(since: datetime, until: datetime) -> dict:
    """
    Return aggregate cost and token totals for actions in [since, until).
    Returns {"token_input": int, "token_output": int, "usd_cost": float}.
    """
    result = (
        get_client()
        .table("actions")
        .select("token_input, token_output, usd_cost")
        .gte("created_at", since.isoformat())
        .lt("created_at", until.isoformat())
        .execute()
    )
    rows = result.data
    return {
        "token_input": sum(r.get("token_input") or 0 for r in rows),
        "token_output": sum(r.get("token_output") or 0 for r in rows),
        "usd_cost": sum(float(r.get("usd_cost") or 0) for r in rows),
    }


def open_approvals() -> list[dict]:
    """Return all approvals with no decision yet, ordered oldest first."""
    result = (
        get_client()
        .table("approvals")
        .select("id, summary, created_at, context")
        .is_("decision", "null")
        .order("created_at", desc=False)
        .execute()
    )
    return result.data


def error_count(since: datetime) -> int:
    """Return the number of health_log error entries since `since`."""
    result = (
        get_client()
        .table("health_logs")
        .select("id", count="exact")
        .eq("event_type", "error")
        .gte("created_at", since.isoformat())
        .execute()
    )
    return result.count or 0


def recent_errors(since: datetime, limit: int = 5) -> list[dict]:
    """Return up to `limit` recent health_log error rows since `since`."""
    result = (
        get_client()
        .table("health_logs")
        .select("service, message, created_at")
        .eq("event_type", "error")
        .gte("created_at", since.isoformat())
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data
