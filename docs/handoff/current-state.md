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
- [ ] Slice 10: Daily summaries and digest

## Next Task
Slice 10: Daily summaries and digest.

## Last Updated
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
