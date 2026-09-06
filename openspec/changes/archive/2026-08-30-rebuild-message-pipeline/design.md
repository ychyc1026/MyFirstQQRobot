## Context

FastAPI receives NapCat reverse-WebSocket events and SQLite is the durable authority. External effects are independently gated. Windows process suspension and restart are normal operating conditions. The current services already own identity, privacy, persona, memory, model protection, quotas, outbox, and audit concerns; orchestration must compose them without bypassing their policies.

## Goals / Non-Goals

**Goals:** deterministic stage transitions, user/group isolation, restart recovery, evidence-backed context, explicit failure states, and fake-adapter end-to-end verification.

**Non-goals:** production network activation, provider selection, autonomous history collection, and prompt-only security.

## Decisions

### Durable reply run

Each accepted conversational message contributes to a `reply_run` identified independently from the inbound event. A run records conversation key, bot QQ, trigger messages, stage, attempt counters, policy snapshot, context manifest, model evidence, outbox IDs, error code, and timestamps.

Terminal states are `completed`, `suppressed`, `failed`, and `cancelled`. Recoverable intermediate states are claimed with leases so a crashed worker cannot own them forever.

### Per-conversation serialization

Only one run may execute context/model/delivery planning for a conversation at a time. Different conversation keys may run concurrently. New messages arriving during the settling window join the pending run; messages arriving after model execution begins create a later run.

### Context manifest before prompt

Context assembly returns typed sections and a manifest, not an opaque string. Every section declares source class, subject QQ, scope, record IDs, policy decision, and truncation. A final renderer may produce the provider prompt only after validation.

Private conversations may load material only for their peer. Group conversations reject all per-user persona, user-understanding, private-memory, and private-history sections.

### Idempotent effect boundary

A successful model result produces a deterministic reply plan. Each bubble receives a stable idempotency key derived from run and sequence. Retries reuse the same outbox items. OneBot response evidence is recorded, but an ambiguous transport failure remains `delivery_unknown` and is not blindly resent.

### Fake-first activation gate

The full worker runs with deterministic fake chat and OneBot adapters. Real reply mode remains blocked until acceptance tests cover restart, duplication, concurrency, isolation, timeout, and ambiguous delivery.

## Failure And Recovery

- Invalid or unsupported messages are suppressed with a reason.
- Context policy denial excludes the section and remains visible in the manifest.
- Model timeout uses bounded retry policy and cannot create an outbox before a validated result.
- Shutdown stops new claims and releases or expires leases safely.
- Ambiguous send results require operator inspection rather than automatic duplication.

## Migration Plan

1. Add reply-run, trigger, context-manifest, and delivery-evidence schema under the mandatory preflight backup.
2. Backfill nothing into active runs; historical messages remain queryable evidence.
3. Start the new worker paused and in fake/observe mode.
4. Run replay tests against recorded synthetic fixtures.
5. Expose readiness blockers; production enablement remains a separate reviewed change.

## Open Questions

- Default settling duration and maximum burst size need owner preference after fake-mode observation.
- Provider-specific token budgets will be decided with model configuration.
- NapCat's effective deduplication support for ambiguous sends must be verified against the selected version.
