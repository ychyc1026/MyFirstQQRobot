## 1. Fail-closed ingest contract

- [x] 1.1 Reject empty or mismatched OneBot WebSocket tokens
- [x] 1.2 Record `meta_event` identity without counting heartbeats as ignored conversations
- [x] 1.3 Persist exact-bot messages without NapCat HTTP construction, model calls, or sendable outbox items

## 2. Status and docs

- [x] 2.1 Expose authenticated bot QQ and exact-bot match on status
- [x] 2.2 Update handoff, next-phase, and startup readiness for observe-only live ingest
- [x] 2.3 Run focused pytest plus ruff on touched files
- [x] 2.4 Add an observe-only one-click start script that starts YCH before NapCat and does not enable delivery
