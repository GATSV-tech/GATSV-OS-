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
- [ ] Slice 8: Conversation memory — rolling context window persisted in Supabase
- [ ] Slice 9: Proactive outbound — scheduled reminders and timed notifications
- [ ] Slice 10: Daily summaries and digest

## Next Task
Slice 8: Conversation memory — rolling context window persisted in Supabase.

## Last Updated
2026-03-31 — Slice 7 complete: Claude reply loop, connectors/sendblue_send.py (with
status_callback), agents/chat.py (AsyncAnthropic, error → health_log → None),
POST /inbound/imessage/status, db/migrations/003_add_chat_agent.sql, 42 tests passing.
