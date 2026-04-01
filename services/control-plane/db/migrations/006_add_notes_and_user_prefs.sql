-- ─────────────────────────────────────────────────────────────────────────────
-- Migration: 006_add_notes_and_user_prefs.sql
--
-- notes: freeform notes saved via the create_note iMessage tool.
-- user_prefs: per-user key/value settings (e.g. digest send time).
--             Upserted on (phone_number, key) — one row per preference.
--
-- Apply via Supabase SQL Editor before deploying Slice 10.
-- ─────────────────────────────────────────────────────────────────────────────

-- ─── notes ───────────────────────────────────────────────────────────────────

create table notes (
  id           uuid        primary key default gen_random_uuid(),
  phone_number text        not null,
  content      text        not null,
  created_at   timestamptz not null default now()
);

create index notes_phone_created_idx
  on notes (phone_number, created_at desc);


-- ─── user_prefs ──────────────────────────────────────────────────────────────

create table user_prefs (
  id           uuid        primary key default gen_random_uuid(),
  phone_number text        not null,
  key          text        not null,
  value        text        not null,
  updated_at   timestamptz not null default now(),
  unique (phone_number, key)
);

create index user_prefs_lookup_idx
  on user_prefs (phone_number, key);
