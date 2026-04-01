# GATSV OS — Session Handoff

## Project Focus
Personal iMessage Claude bot. Jake texts it and it replies. It can proactively
send reminders, daily summaries, and timed notifications. The bot runs on the
same FastAPI control plane; Supabase stores conversation history and scheduled
tasks. Sendblue is the iMessage transport layer.

Slices 1–6 built the inbound pipeline and connectors. Slices 7+ are focused
entirely on the bot loop.

Slices 3 (Postmark) and 4 (Tally) are built and tested but not core to the bot;
left in place, not extended.

## Stack
- Control plane: Python + FastAPI (async)
- Database: Supabase (PostgreSQL + pgvector)
- AI: Anthropic Claude SDK (primary)
- iMessage transport: Sendblue
- Infra: VPS + Docker + GitHub

## Implementation Slices (Status)
- [x] Slice 1: Repo skeleton + Docker + env setup
- [x] Slice 2: Supabase schema + migrations
- [x] Slice 3: Postmark inbound email connector
- [x] Slice 4: Tally form webhook connector
- [x] Slice 5: Gatekeeper agent
- [x] Slice 6: Sendblue iMessage connector
- [x] Slice 7: Claude reply loop — inbound iMessage → Claude API → Sendblue outbound reply
- [x] Slice 8: Conversation memory — rolling context window persisted in Supabase
- [x] Slice 9: Proactive outbound — scheduled reminders and timed notifications
- [x] Slice 10: Daily summaries, digest, and four new tools

## Next Task
All 10 slices complete + 3 bugs fixed. Bot is feature-complete for v1. Possible next directions:
- Deploy to VPS (Docker, env vars, Supabase prod connection)
- Wire up Slack operator surface (CLAUDE.md calls this v1 interface for approvals/alerts)
- Add a notes query tool (read back saved notes)
- Build the Reporter agent (digest-style summaries for inbound event pipeline)

## Last Updated
2026-04-01 — Bug fixes: duplicate sends, tool routing, reminder time

**Bug 1 — Duplicate sends** (`scheduler/runner.py`):
Reordered scheduler to claim (mark "sent") BEFORE calling Sendblue. If the
claim fails, skip the send and leave the task pending for retry next tick.
If send fails after a successful claim, mark "failed" and write health_log.
Ordering: mark_status("sent") → send_message() → on error: mark_status("failed").

**Bug 2 — Tool routing confusion** (all 5 tool descriptions):
Added "Use ONLY when..." and "Do NOT use for:..." exclusion clauses to
set_reminder, create_note, daily_brief, list_reminders, cancel_reminder.
Prevents Claude from calling set_reminder for "note: ..." phrases or
calling daily_brief for one-off reminder requests.

**Bug 3 — Reminder time defaulting to ~now** (`agents/chat.py` + `agents/tools/set_reminder.py`):
- System prompt changed from "Current time: ..." to "Current date and time: ..."
  with cross-platform formatting (no %-I/%-d strftime specifiers).
- set_reminder scheduled_at description extended with explicit today-vs-tomorrow
  date resolution rules and a concrete worked example.

Tests: 78 passing (2 pre-existing failures in test_health.py).
test_scheduler.py rewritten to cover new claim-before-send ordering:
new tests test_tick_skips_send_if_claim_fails and
test_tick_claim_failure_does_not_block_remaining_tasks.

## Last Updated (Slice 10)
2026-04-01 — Slice 10 complete: daily digest + 4 new tools

Digest: generates a morning summary (midnight-to-midnight Pacific window) of
yesterday's inbound events, today's scheduled reminders, and overnight system
errors. Claude writes the message. Sends via Sendblue. Fires on a daily asyncio
loop (scheduler/digest.py) — reads send time from user_prefs each cycle so
daily_brief changes take effect the following morning. Requires JAKE_PHONE_NUMBER
env var; skips gracefully if not set.

New tools (all registered via the tool registry):
- create_note: saves freeform notes to notes table. "Remember that..."
- list_reminders: returns formatted list of pending scheduled_tasks
- cancel_reminder: cancels by content/time substring match, acks what was cancelled
- daily_brief: upserts digest_send_time_pt in user_prefs, acks with next-morning time

Bug fixes in set_reminder.py (from linter edit): heduled_at typo and "ject" type.

New tables: notes, user_prefs (migration 006).
System prompt in chat.py generalized — no longer mentions set_reminder by name.

Files changed: db/migrations/006_add_notes_and_user_prefs.sql, db/notes.py,
db/user_prefs.py, db/digest_queries.py, db/scheduled_tasks.py (list_pending),
agents/tools/create_note.py, agents/tools/list_reminders.py,
agents/tools/cancel_reminder.py, agents/tools/daily_brief.py,
agents/tools/__init__.py, agents/chat.py (system prompt), agents/digest.py,
scheduler/digest.py, main.py, config.py, tests/test_digest_agent.py,
tests/test_new_tools.py.

New env vars: JAKE_PHONE_NUMBER (required for digest), DIGEST_SEND_TIME_PT
(default "07:00", overridable per-user via daily_brief tool).
77 tests passing (2 pre-existing failures in test_health.py).

## Last Updated (Slice 9)
2026-04-01 — Slice 9 complete: proactive outbound with tool registry architecture.

New table: scheduled_tasks (sender_phone, content, scheduled_at, status).
Scheduler: asyncio polling loop (no extra dependency), started/stopped in FastAPI
lifespan, polls every SCHEDULER_POLL_INTERVAL_SECONDS (default 60). Per-task
failure isolation — one bad Sendblue send does not block remaining tasks.

Tool registry: agents/tool_registry.py defines ToolDefinition, ToolContext,
ToolResult, register(), get_api_tools(), dispatch(). chat.py is decoupled from
individual tool names. Adding a new tool = new file in agents/tools/ + one import
line in agents/tools/__init__.py.

set_reminder tool: Claude calls it when it detects a reminder intent. Handler
saves scheduled_task row, returns Pacific-time ack ("Got it — I'll remind you
at 3:00 PM PT."). System prompt injects current Pacific time so Claude can
resolve relative times ("at 3pm", "in 2 hours"). Ack is persisted as assistant
turn and sent via Sendblue. Action row uses action_type='tool_use'.

Files changed: db/migrations/005_add_scheduled_tasks.sql, db/scheduled_tasks.py,
db/schemas.py (ScheduledTaskCreate/ScheduledTask), agents/tool_registry.py,
agents/tools/__init__.py, agents/tools/set_reminder.py, agents/chat.py,
scheduler/__init__.py, scheduler/runner.py, main.py, config.py,
tests/test_chat_agent.py, tests/test_scheduler.py.

New env vars: SCHEDULER_POLL_INTERVAL_SECONDS (default 60).
55 tests passing (2 pre-existing failures in test_health.py).
