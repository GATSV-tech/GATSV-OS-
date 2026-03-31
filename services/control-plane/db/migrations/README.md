# Database Migrations

## Overview

GATSV OS Core v1 uses plain SQL migration files applied directly to Supabase.
No ORM, no migration runner — just numbered SQL files applied in order.

## Files

| File | Description |
|------|-------------|
| `001_initial.sql` | Full v1 schema: extensions, tables, triggers, and indexes |

Future changes each get a new numbered file: `002_add_tags_to_events.sql`, etc.
**Never edit an applied migration file.** Add a new one instead.

## How to Apply

### Option A — Supabase SQL Editor (recommended for v1)

1. Open your Supabase project at [supabase.com](https://supabase.com)
2. Navigate to **SQL Editor**
3. Paste the full contents of `001_initial.sql`
4. Click **Run**
5. Confirm with the verification queries below

### Option B — Supabase CLI

```bash
# One-time setup if not already linked
supabase link --project-ref <your-project-ref>

# Apply
supabase db push
```

## Verification

Run these in the Supabase SQL Editor after applying to confirm everything is in place:

```sql
-- Should return 6 rows
select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in ('entities', 'events', 'actions', 'approvals', 'memories', 'health_logs')
order by table_name;

-- Should return 1 row (pgvector extension)
select extname, extversion
from pg_extension
where extname = 'vector';

-- Should return the memories embedding column with type 'vector'
select column_name, udt_name
from information_schema.columns
where table_name = 'memories'
  and column_name = 'embedding';

-- Should return 14+ rows (one per index)
select indexname, tablename
from pg_indexes
where schemaname = 'public'
  and tablename in ('entities', 'events', 'actions', 'approvals', 'memories', 'health_logs')
order by tablename, indexname;
```

## Design Notes

**RLS is intentionally off** for all v1 tables. The control plane accesses Supabase
exclusively via the service key, which bypasses RLS regardless. Multi-tenant RLS
is a v2 concern and will be designed alongside the auth strategy.

**`events.raw_payload` is immutable by convention.** Gatekeeper writes it once from
the source payload; no agent ever modifies it. Agents read and write normalized columns
only. This makes the pipeline fully replayable from source data.

**`memories.embedding` is nullable.** Rows are written first, embeddings populated
asynchronously. Rows without embeddings are valid and queryable on non-vector columns.

**`actions.event_id` is nullable.** System-level actions (startup logs, scheduled
digests) are not tied to a specific event. Agent logic that requires an event_id
enforces that at the application layer, not the DB layer.

**text + CHECK over Postgres ENUM.** All constrained text columns use CHECK constraints
rather than Postgres ENUM types. Adding a new value to an ENUM requires `ALTER TYPE`
and can lock the table; altering a CHECK constraint does not.
