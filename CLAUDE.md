# GATSV OS Core – Agent Guide

## Why
This repo contains the first production-minded version of GATSV OS Core (internal codename).
The final customer-facing product name and positioning are not locked yet.

The system is intended to run meaningful parts of a company, not just isolated automations.
It is designed specifically for founder-led service businesses and small agencies with 2–15
people that have recurring inbound work, client delivery, and ongoing coordination needs.
This ICP is intentionally narrow. Do not drift toward generic software for all small businesses.
The ICP should be narrowed further before production sales begin.

## Product Intent
GATSV OS Core v1 includes:
- Intake and triage
- Work routing
- Founder command view
- Human approval checkpoints
- Built-in observability and audit logging

This system should eventually expand into a full company nervous system across marketing,
sales, support, operations, and finance.

Guardrail: this is a production-minded company operating layer with explicit control,
auditability, and upgradeability — not a generic agency automation tool.

## Architecture Direction
- Prefer a custom event-driven control service over hard dependence on n8n
- Supabase is the primary data layer (relational + pgvector for memory)
- All inbound work is normalized into a common event schema before any agent touches it
- Agents act on events and produce auditable actions
- Risky or high-stakes actions require human approval before execution
- Observability is a core product requirement, not optional internal tooling. Every major action
  must produce logs, traces, cost visibility, approval state, and failure visibility. The
  observability layer is designed to be extractable as a future standalone product (GATSV Ops).

## Core Entities
- `events` — normalized inbound work items
- `entities` — contacts, companies, deals
- `actions` — what agents did or proposed doing
- `memories` — persistent context per entity (pgvector)
- `approvals` — human-in-the-loop queue
- `health_logs` — system observability

## Agent Roles
| Agent | Responsibility |
|-------|---------------|
| Gatekeeper | Receives inbound, normalizes to event schema, deduplicates |
| Router | Classifies events into buckets, assigns priority |
| Operator | Executes safe automated actions per routing decision |
| Reporter | Surfaces events to founder dashboard, generates digests |
| Auditor | Logs everything, tracks token/dollar costs, flags anomalies |

## Engineering Rules
- Do not start coding immediately on major features — plan first, wait for approval
- Prefer small, testable increments
- Keep schemas explicit and stable; migrations are permanent decisions
- Every workflow must have a clear trigger, action path, and log trail
- Avoid fragile magic behavior
- Do not introduce unnecessary frameworks if a simple service/module will do
- Favor readable code and clear contracts over clever abstractions
- Every agent action must write to `actions` and `health_logs` — no silent operations

## Workflow Rules
- For major tasks, produce a plan and wait for approval before writing files
- When implementing, identify exact files to create or modify before changing anything
- Add or update docs as architecture evolves
- Preserve a clear handoff trail so a fresh Claude session can continue work cleanly
- Update `docs/handoff/current-state.md` after each implementation slice

## Stack
- **Control plane:** Python + FastAPI (async)
- **Database:** Supabase (PostgreSQL + pgvector)
- **AI layer:** Anthropic Claude SDK (primary), OpenAI as fallback
- **Operator surface v1:** Slack — approvals, alerts, summaries, and command interactions.
  Slack is a strong tactical v1 interface and may remain important longer term, but must be
  treated as an interface layer over the core system, not the system itself.
- **Admin surface v2/vNext:** Lightweight web dashboard (deferred until event loop is proven)
- **Secondary channel option:** Discord (team/channel-based collaboration if needed)
- **Future optional channel:** WhatsApp (personal/mobile continuity, not primary v1 surface)
- Interface surfaces are replaceable. Slack, Discord, and web dashboard all sit above the same
  core event/action/control system. Agent logic must never be coupled to a specific channel.
- **Infra:** VPS + Docker + GitHub
- **Internal dev/ops swarm:** Claude Code + Ruflo

## Product Naming
- **Internal codename:** GATSV OS Core v1
- **Customer-facing name/positioning:** not locked yet — do not hardcode "GATSV OS Core" in
  any user-visible strings, marketing copy, or external APIs
- **ICP reminder:** founder-led service businesses / small agencies, 2–15 people, recurring
  inbound + delivery + coordination. Not "all small businesses."

## Security and Channel Trust
- Do not use Telegram as an operating channel for this project
- Channel decisions must account for real operational trust, not just implementation convenience
- Secrets, tokens, bot credentials, and channel auth flows must be handled conservatively and
  documented clearly
- Channel integrations must be designed so they can be rotated, replaced, or revoked cleanly

## Build Discipline
- Do not let this project drift into generic agency automation software
- Prefer one clean control plane over premature fragmentation
- Keep interfaces replaceable, schemas explicit, and workflows reviewable
- For major work: plan first, show the diff, wait for approval before moving forward
- When building Slack integration: design it to support founder approvals, daily summaries,
  error alerts, and lightweight commands — without assuming Slack is the permanent front end

## Current Objective
Design and build GATSV OS Core v1. See `docs/handoff/current-state.md` for current status.
