"""
Pydantic models for all GATSV OS Core v1 database tables.

One Create model (fields needed to insert) and one full model (complete row
as returned from DB) per table. Where agents need to update a subset of fields,
an Update model is provided too.

Used by all DB modules and agents — never instantiate the Supabase client here.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


# ─── Entity ──────────────────────────────────────────────────────────────────

class EntityCreate(BaseModel):
    type: str  # 'contact' | 'company'
    name: str | None = None
    email: str | None = None
    company: str | None = None
    metadata: dict[str, Any] = {}


class Entity(EntityCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime


# ─── Event ───────────────────────────────────────────────────────────────────

class EventCreate(BaseModel):
    source: str
    source_id: str | None = None
    raw_payload: dict[str, Any]
    schema_version: str = "v1"
    status: str = "received"
    # Normalized fields — populated by Gatekeeper
    sender_name: str | None = None
    sender_email: str | None = None
    subject: str | None = None
    body: str | None = None
    received_at: datetime | None = None
    entity_id: str | None = None


class EventUpdate(BaseModel):
    """Fields agents are permitted to update after initial creation."""
    sender_name: str | None = None
    sender_email: str | None = None
    subject: str | None = None
    body: str | None = None
    received_at: datetime | None = None
    bucket: str | None = None       # 'sales' | 'delivery' | 'support' | 'founder_review' | 'noise'
    priority: str | None = None     # 'high' | 'medium' | 'low'
    confidence: float | None = None
    status: str | None = None       # 'received' | 'normalized' | 'routed' | 'actioned' | 'closed'
    entity_id: str | None = None


class Event(EventCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    bucket: str | None = None
    priority: str | None = None
    confidence: float | None = None
    status: str
    created_at: datetime
    updated_at: datetime


# ─── Action ──────────────────────────────────────────────────────────────────

class ActionCreate(BaseModel):
    agent: str          # 'gatekeeper' | 'router' | 'operator' | 'reporter' | 'auditor'
    action_type: str
    payload: dict[str, Any] = {}
    status: str = "executed"        # default: auto-executed; set 'pending_approval' before approval
    requires_approval: bool = False
    event_id: str | None = None     # null for system-level actions
    approved_by: str | None = None
    token_input: int = 0
    token_output: int = 0
    usd_cost: Decimal = Decimal("0")
    error: str | None = None
    duration_ms: int | None = None
    executed_at: datetime | None = None


class Action(ActionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


# ─── Approval ────────────────────────────────────────────────────────────────

class ApprovalCreate(BaseModel):
    action_id: str
    requested_by: str   # which agent is requesting approval
    summary: str        # plain English — must be self-contained, no lookups needed
    context: dict[str, Any] = {}
    options: list[str] = ["approve", "reject", "modify"]
    event_id: str | None = None     # denormalized from action for query convenience
    expires_at: datetime | None = None


class ApprovalDecision(BaseModel):
    """Applied when the founder makes a decision."""
    decision: str       # 'approved' | 'rejected' | 'modified'
    modification: str | None = None
    decided_by: str
    decided_at: datetime


class Approval(ApprovalCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    decision: str | None = None
    modification: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    notified_at: datetime | None = None
    created_at: datetime


# ─── Memory ──────────────────────────────────────────────────────────────────

class MemoryCreate(BaseModel):
    entity_id: str
    memory_type: str    # 'interaction' | 'preference' | 'context' | 'note'
    content: str
    embedding: list[float] | None = None    # nullable; populated async
    source_event_id: str | None = None


class Memory(MemoryCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


# ─── HealthLog ───────────────────────────────────────────────────────────────

class HealthLogCreate(BaseModel):
    service: str
    event_type: str     # 'startup' | 'shutdown' | 'error' | 'warning' | 'metric' | 'info'
    message: str
    metadata: dict[str, Any] = {}


class HealthLog(HealthLogCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
