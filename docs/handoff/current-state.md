# GATSV OS Core — Session Handoff

## Decided
- Internal codename: GATSV OS Core v1. Final customer-facing product name not locked yet.
- ICP (current): founder-led service businesses and small agencies with 2–15 people that have
  recurring inbound work, client delivery, and ongoing coordination needs. Intentionally narrower
  than "all small businesses." To be narrowed further before production sales.
- v1 scope: intake, routing, founder visibility, approvals, observability
- Custom control layer (FastAPI), not n8n-dependent
- Supabase as primary data + memory layer
- Claude Code + Ruflo as internal build/ops workflow
- Observability is a core product requirement from v1 — not internal tooling. Must produce logs,
  traces, cost visibility, approval state, and failure visibility on every major action. The
  observability layer is designed to be extractable as a future product (GATSV Ops).
- v1 operator surface = Slack (approvals, alerts, summaries, commands). Strong tactical v1
  interface; architecture remains channel-agnostic so surfaces are replaceable.
- Discord = secondary/fallback option for team or channel-based collaboration if needed
- WhatsApp = possible future optional personal/mobile interface, not primary v1 surface
- Telegram = not used as an operating channel for this project
- v2/vNext admin surface = lightweight web dashboard (deferred until event loop is proven)
- First two inbound sources: Postmark inbound email + Tally form webhook
- Architecture plan approved — see `docs/architecture/v1.md`

## Not Decided Yet
- Exact ICP narrowing before production sales
- Exact first external beta client
- Pricing model and go-to-market timing
- Exact Slack interaction pattern for v1 (DMs vs. channels vs. threads vs. hybrid)
- Exact first two inbound sources (Postmark + Tally are current recommendation, not final)
- Exact outbound/provider choices where relevant
- Exact first web dashboard implementation path (v2)
- Auth strategy for multi-tenant (single founder use for now)
- Whether any additional constraints should be added before Slice 1 starts

## Implementation Slices (Status)
- [x] Slice 1: Repo skeleton + Docker + env setup
- [ ] Slice 2: Supabase schema + migrations
- [x] Slice 3: Postmark inbound email connector
- [x] Slice 4: Tally form webhook connector
- [ ] Slice 5: Gatekeeper agent
- [ ] Slice 6: Router agent
- [ ] Slice 7: Operator agent (safe actions only)
- [ ] Slice 8: Approvals flow + Slack integration
- [ ] Slice 9: Reporter agent + daily digest
- [ ] Slice 10: Auditor + health dashboard view

## Avoid
- Turning this into a generic AI agency demo
- Drifting toward generic software for all small businesses
- Building too broad a platform before the event model is stable
- Adding integrations before the core event/action loop is proven
- Skipping the approval layer to ship faster
- Coupling agent logic to any specific channel or interface
- Using Telegram as an operating channel for this project
- Treating security and channel trust as setup details rather than product-level concerns

## Next Task
Slice 5: Gatekeeper agent. Plan first, then implement.

## Last Updated
2026-03-31 — Slice 4 complete: Tally form connector, ParsedEmail → ParsedInbound rename, /inbound/form wired to 202, 18 tests passing
