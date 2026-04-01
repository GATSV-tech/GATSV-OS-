-- ─────────────────────────────────────────────────────────────────────────────
-- Migration: 005_add_scheduled_tasks.sql
--
-- Creates the scheduled_tasks table for proactive outbound notifications.
-- The scheduler polls this table every N seconds for pending tasks whose
-- scheduled_at is in the past, then fires them via Sendblue.
--
-- NOTE: This table is designed for a single-instance scheduler. If multiple
-- instances run concurrently in the future, use SELECT ... FOR UPDATE SKIP LOCKED
-- to prevent duplicate fires. For v1 single-instance deployment this is not needed.
--
-- Apply via Supabase SQL Editor before deploying Slice 9.
-- ─────────────────────────────────────────────────────────────────────────────

create table scheduled_tasks (
  id           uuid        primary key default gen_random_uuid(),
  sender_phone text        not null,
  content      text        not null,
  scheduled_at timestamptz not null,
  status       text        not null default 'pending'
               check (status in ('pending', 'sent', 'failed', 'cancelled')),
  created_at   timestamptz not null default now()
);

-- Primary query path: find all pending tasks due for firing
create index scheduled_tasks_due_idx
  on scheduled_tasks (scheduled_at)
  where status = 'pending';
