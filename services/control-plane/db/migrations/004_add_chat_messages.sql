-- ─────────────────────────────────────────────────────────────────────────────
-- Migration: 004_add_chat_messages.sql
--
-- Creates the chat_messages table for the iMessage bot conversation memory.
-- Stores raw turn-by-turn conversation history keyed by sender_phone.
-- Intentionally separate from the memories/vector table — this is the rolling
-- context window, not semantic long-term memory.
--
-- Apply via Supabase SQL Editor before deploying Slice 8.
-- ─────────────────────────────────────────────────────────────────────────────

create table chat_messages (
  id           uuid        primary key default gen_random_uuid(),
  sender_phone text        not null,
  role         text        not null check (role in ('user', 'assistant')),
  content      text        not null,
  created_at   timestamptz not null default now()
);

-- Primary query path: fetch recent messages for a given phone number
create index chat_messages_phone_created_idx
  on chat_messages (sender_phone, created_at desc);
