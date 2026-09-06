## Why

The project has durable ingestion, identity/privacy controls, context preview, model protection, and an outbox, but these pieces are not yet governed by one explicit end-to-end reply state machine. Enabling real replies before that integration is proven risks duplicate replies, cross-user context leakage, incorrect group behavior, and unrecoverable states after a Windows restart.

## What Changes

- Introduce a typed message envelope and a durable reply-run state machine from inbound event to terminal delivery outcome.
- Serialize reply work per conversation while allowing independent conversations to progress concurrently.
- Build context through explicit identity, authorization, persona, knowledge, memory, and history stages with provenance.
- Keep private-user material out of group context and out of every other user's context.
- Add message-burst settling, reply bubble planning, idempotent outbox creation, restart recovery, and sanitized trace evidence.
- Prove the complete path with fake model and fake OneBot adapters before any real network gate may be enabled.

## Non-goals

- Selecting or configuring the owner's production chat/image API credentials.
- Enabling real QQ replies, Qzone access, Qzone publication, or live historical chat reads.
- Redesigning the dashboard visual language.
- Treating model prompts as the authorization mechanism.

## Capabilities

### New Capabilities

- `reply-orchestration`: durable, typed orchestration and per-conversation serialization.
- `context-assembly`: provenance-aware and privacy-isolated context construction.
- `reply-delivery`: natural bubble planning and exactly-once-effective outbox handoff.

### Modified Capabilities

- `message-ingestion`: inbound persistence will create or wake a durable reply run rather than ending at observation.
- `dashboard-operations`: operators will see reply-run stages, blockers, retries, and correlations.

## Impact

- Backend domain contracts, SQLite schema, message ingestion service, context service, model gateway coordination, outbox dispatcher, and worker lifecycle.
- Dashboard conversation and operations pages.
- Migration requires the existing mandatory preflight backup.
- No real external effect is introduced by applying this change; test and observe modes remain the acceptance environment.

## Risks

- A poorly chosen concurrency boundary can reorder replies or block unrelated users.
- Context provenance errors can leak private material even when the final prompt looks valid.
- Retry behavior can create duplicate QQ messages if idempotency stops at the local outbox.
- Long-running model calls can leave stale runs after sleep, shutdown, or network loss.

## User Decisions Deferred

- Production model providers, model names, API endpoints, and credentials.
- Exact burst-settling delay and default reply bubble style.
- Whether any live history adapter will ever be enabled.
