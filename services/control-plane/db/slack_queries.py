"""
Read-only queries used by the Slack daily summary.
Synchronous — callers must wrap in asyncio.to_thread.
All datetime arguments must be timezone-aware UTC.
"""

import logging
from datetime import datetime

from db.client import get_client

logger = logging.getLogger(__name__)


def events_summary(since: datetime, until: datetime) -> list[dict]:
    """
    Return event rows in [since, until) with source, bucket, and status.
    Callers aggregate as needed.
    """
    result = (
        get_client()
        .table("events")
        .select("source, bucket, status, created_at")
        .gte("created_at", since.isoformat())
        .lt("created_at", until.isoformat())
        .execute()
    )
    return result.data


def actions_cost(since: datetime, until: datetime) -> dict:
    """
    Return aggregate token and cost totals for actions created in [since, until).
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
        "token_input": sum(r.get("token_input", 0) or 0 for r in rows),
        "token_output": sum(r.get("token_output", 0) or 0 for r in rows),
        "usd_cost": sum(float(r.get("usd_cost", 0) or 0) for r in rows),
    }


def open_approvals_count() -> int:
    """Return the number of approvals with no decision yet."""
    result = (
        get_client()
        .table("approvals")
        .select("id", count="exact")
        .is_("decision", "null")
        .execute()
    )
    return result.count or 0


def recent_errors(since: datetime) -> list[dict]:
    """Return health_log error rows created at or after `since`."""
    result = (
        get_client()
        .table("health_logs")
        .select("service, message, created_at")
        .eq("event_type", "error")
        .gte("created_at", since.isoformat())
        .order("created_at", desc=False)
        .execute()
    )
    return result.data
