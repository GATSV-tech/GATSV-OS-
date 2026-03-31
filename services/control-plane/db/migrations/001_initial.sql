-- ─────────────────────────────────────────────────────────────────────────────
-- GATSV OS Core v1 — Initial Schema
-- Migration: 001_initial.sql
--
-- Apply via Supabase SQL Editor (paste + run) or `supabase db push`.
-- See db/migrations/README.md for full instructions.
--
-- RLS NOTE: Row Level Security is intentionally disabled on all tables in v1.
-- The control plane accesses Supabase exclusively via the service key, which
-- bypasses RLS regardless. RLS will be revisited in v2 when multi-tenant
-- access patterns are defined.
--
-- Order: extensions → utility functions → entities → events → actions
--        → approvals → memories → health_logs → indexes
-- ─────────────────────────────────────────────────────────────────────────────


-- ─── Extensions ──────────────────────────────────────────────────────────────

create extension if not exists vector;


-- ─── Utility: auto-update updated_at ─────────────────────────────────────────

create or replace function update_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;


-- ─── entities ────────────────────────────────────────────────────────────────
-- People and companies that appear in inbound work.
-- Created first — referenced by events and memories.

create table entities (
  id            uuid        primary key default gen_random_uuid(),
  type          text        not null check (type in ('contact', 'company')),
  name          text,
  email         text        unique,
  company       text,
  metadata      jsonb       not null default '{}',
  first_seen_at timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  created_at    timestamptz not null default now()
);

-- No updated_at on entities — last_seen_at carries that semantic more precisely.


-- ─── events ──────────────────────────────────────────────────────────────────
-- The central table. Every piece of inbound work becomes a row here.
-- raw_payload is immutable — never modified after Gatekeeper writes it.
-- Agents read and write normalized columns only.

create table events (
  id             uuid         primary key default gen_random_uuid(),
  source         text         not null,
  source_id      text,
  raw_payload    jsonb        not null,
  schema_version text         not null default 'v1',

  -- Normalized fields (populated by Gatekeeper)
  sender_name    text,
  sender_email   text,
  subject        text,
  body           text,
  received_at    timestamptz,

  -- Routing fields (populated by Router)
  bucket         text         check (bucket in ('sales', 'delivery', 'support', 'founder_review', 'noise')),
  priority       text         check (priority in ('high', 'medium', 'low')),
  confidence     numeric(4,3) check (confidence >= 0 and confidence <= 1),

  -- Lifecycle
  status         text         not null default 'received'
                              check (status in ('received', 'normalized', 'routed', 'actioned', 'closed')),

  entity_id      uuid         references entities (id),
  created_at     timestamptz  not null default now(),
  updated_at     timestamptz  not null default now()
);

create trigger events_updated_at
  before update on events
  for each row execute function update_updated_at();

-- Dedup: prevent the same inbound event being processed twice.
-- Partial — only enforced when source_id is present (not all sources provide one).
create unique index events_source_dedup_idx
  on events (source, source_id)
  where source_id is not null;


-- ─── actions ─────────────────────────────────────────────────────────────────
-- Every agent action — automated or pending approval — produces a row here.
-- event_id is nullable for system-level actions not tied to a specific event
-- (e.g., startup health checks, scheduled digests).

create table actions (
  id                uuid         primary key default gen_random_uuid(),
  event_id          uuid         references events (id),
  agent             text         not null
                                 check (agent in ('gatekeeper', 'router', 'operator', 'reporter', 'auditor')),
  action_type       text         not null,
  payload           jsonb        not null default '{}',
  status            text         not null default 'executed'
                                 check (status in ('executed', 'pending_approval', 'approved', 'rejected', 'failed')),
  requires_approval boolean      not null default false,
  approved_by       text,

  -- Observability — required on every row; defaults prevent nulls in rollups
  token_input       int          not null default 0,
  token_output      int          not null default 0,
  usd_cost          numeric(10,6) not null default 0,
  error             text,
  duration_ms       int,

  executed_at       timestamptz,
  created_at        timestamptz  not null default now()
);

-- No updated_at on actions — status transitions are the full history needed.


-- ─── approvals ───────────────────────────────────────────────────────────────
-- Human-in-the-loop queue. One row per approval request.
-- context must carry everything the founder needs to decide — no extra lookups.
-- event_id is denormalized here for query convenience (avoids joining through actions).

create table approvals (
  id           uuid        primary key default gen_random_uuid(),
  action_id    uuid        not null references actions (id),
  event_id     uuid        references events (id),
  requested_by text        not null,
  summary      text        not null,
  context      jsonb       not null default '{}',
  options      jsonb       not null default '["approve", "reject", "modify"]',

  -- Decision (null until founder acts)
  decision     text        check (decision in ('approved', 'rejected', 'modified')),
  modification text,
  decided_by   text,
  decided_at   timestamptz,

  -- Escalation
  expires_at   timestamptz,
  notified_at  timestamptz,

  created_at   timestamptz not null default now()
);


-- ─── memories ────────────────────────────────────────────────────────────────
-- Persistent entity context with vector embeddings.
-- embedding is nullable — populated async after content is written.
-- Memories are write-once; no updated_at.

create table memories (
  id              uuid        primary key default gen_random_uuid(),
  entity_id       uuid        not null references entities (id),
  memory_type     text        not null
                              check (memory_type in ('interaction', 'preference', 'context', 'note')),
  content         text        not null,
  embedding       vector(1536),
  source_event_id uuid        references events (id),
  created_at      timestamptz not null default now()
);


-- ─── health_logs ─────────────────────────────────────────────────────────────
-- Append-only system observability.
-- Every agent write, every error, every metric lands here.
-- No updated_at — rows are never modified after insert.

create table health_logs (
  id         uuid        primary key default gen_random_uuid(),
  service    text        not null,
  event_type text        not null
             check (event_type in ('startup', 'shutdown', 'error', 'warning', 'metric', 'info')),
  message    text        not null,
  metadata   jsonb       not null default '{}',
  created_at timestamptz not null default now()
);


-- ─── Indexes ─────────────────────────────────────────────────────────────────

-- events
create index events_status_idx        on events (status);
create index events_bucket_idx        on events (bucket);
create index events_sender_email_idx  on events (sender_email);
create index events_entity_id_idx     on events (entity_id);
create index events_created_at_idx    on events (created_at desc);

-- actions
create index actions_event_id_idx     on actions (event_id);
create index actions_status_idx       on actions (status);
create index actions_created_at_idx   on actions (created_at desc);

-- approvals
create index approvals_action_id_idx  on approvals (action_id);
-- Partial index on open approvals — this is the hot query path
create index approvals_open_idx       on approvals (created_at)
  where decision is null;

-- memories
create index memories_entity_id_idx   on memories (entity_id);
-- HNSW for cosine similarity search — better query performance than IVFFlat at v1 volumes
create index memories_embedding_idx   on memories using hnsw (embedding vector_cosine_ops);

-- health_logs
create index health_logs_service_idx      on health_logs (service, created_at desc);
create index health_logs_event_type_idx   on health_logs (event_type, created_at desc);
