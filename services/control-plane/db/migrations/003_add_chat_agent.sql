-- ─────────────────────────────────────────────────────────────────────────────
-- Migration: 003_add_chat_agent.sql
--
-- Adds 'chat' to the allowed agent values in the actions table.
-- Apply via Supabase SQL Editor before deploying Slice 7.
-- ─────────────────────────────────────────────────────────────────────────────

alter table actions drop constraint if exists actions_agent_check;

alter table actions add constraint actions_agent_check
  check (agent in ('gatekeeper', 'router', 'operator', 'reporter', 'auditor', 'chat'));
