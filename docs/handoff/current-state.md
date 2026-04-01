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
- [ ] Slice 9: Proactive outbound — scheduled reminders and timed notifications
- [ ] Slice 10: Daily summaries and digest

## Next Task
Slice 9: Proactive outbound — scheduled reminders and timed notifications.

## Last Updated
2026-03-31 — Slice 8 complete: conversation memory wired into chat agent.
New table chat_messages (sender_phone, role, content). User turn saved before
Claude call to prevent loss on generation failure. History fetched newest-first
then reversed to chronological order. Assistant turn saved after reply sent.
DB failures on append are logged but never crash the reply. Window size
configurable via CHAT_HISTORY_LIMIT env var (default 20). Files changed:
db/migrations/004_add_chat_messages.sql, db/chat_messages.py, agents/chat.py,
config.py, tests/test_chat_agent.py. 51 tests passing (2 pre-existing failures
in test_health.py unrelated to this slice).
