"""
Read-only queries used by the Auditor agent for cost tracking, error-rate
anomaly detection, and stale approval alerts.
Synchronous — callers must wrap in asyncio.to_thread.
All datetime arguments must be timezone-aware UTC.
"""

import logging
from datetime import datetime

from db.client import get_client

logger = logging.getLogger(__name__)


def cost_rollup(since: datetime, until: datetime) -> dict:
    """
    Return aggregate cost and token totals for all actions in [since, until).
    Returns {"token_input": int, "token_output": int, "usd_cost": float, "row_count": int}.
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
        "row_count": len(rows),
    }


def error_rows(since: datetime) -> list[dict]:
    """
    Return all health_log error entries created at or after `since`.
    Used to detect error-rate spikes since the last auditor tick.
    """
    result = (
        get_client()
        .table("health_logs")
        .select("id, service, message, created_at")
        .eq("event_type", "error")
        .gte("created_at", since.isoformat())
        .order("created_at", desc=False)
        .execute()
    )
    return result.data


def stale_approvals(stale_after: datetime) -> list[dict]:
    """
    Return open approvals whose created_at is before `stale_after`.
    These have been pending a decision longer than the configured threshold.
    """
    result = (
        get_client()
        .table("approvals")
        .select("id, summary, created_at, context")
        .is_("decision", "null")
        .lt("created_at", stale_after.isoformat())
        .order("created_at", desc=False)
        .execute()
    )
    return result.data


def cost_since_midnight_utc(midnight_utc: datetime) -> float:
    """
    Return the total USD cost of all actions since `midnight_utc`.
    Used to check whether daily spend has exceeded the alert threshold.
    """
    result = (
        get_client()
        .table("actions")
        .select("usd_cost")
        .gte("created_at", midnight_utc.isoformat())
        .execute()
    )
    return sum(float(r.get("usd_cost") or 0) for r in result.data)
