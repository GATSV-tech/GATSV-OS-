-- ─────────────────────────────────────────────────────────────────────────────
-- Migration: 002_add_phone.sql
--
-- Adds phone number support to entities for iMessage/SMS contacts.
-- NULL values are excluded from PostgreSQL UNIQUE constraints, so existing
-- rows with phone = NULL are unaffected.
--
-- Apply via Supabase SQL Editor before deploying Slice 6.
-- ─────────────────────────────────────────────────────────────────────────────

alter table entities add column phone text unique;

create index entities_phone_idx on entities (phone);
