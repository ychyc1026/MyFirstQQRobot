## 1. Domain contracts and persistence

- [x] 1.1 Define typed message envelope, reply-run stages, context sections, reply plan, and error taxonomy.
- [x] 1.2 Add schema migration for reply runs, triggers, context manifests, leases, and delivery evidence.
- [x] 1.3 Implement repository transitions with compare-and-set guards and per-conversation uniqueness.
- [x] 1.4 Test legal/illegal transitions, duplicate triggers, lease expiry, and migration rollback safety.

## 2. Orchestration and context

- [x] 2.1 Implement burst settling and per-conversation run claiming.
- [x] 2.2 Build provenance-aware context assembly from identity, authorization, persona, knowledge, memory, and allowed history.
- [x] 2.3 Enforce private-user and group-chat isolation before rendering prompts.
- [x] 2.4 Add deterministic truncation and token-budget reporting.
- [x] 2.5 Test multiple users, group isolation, conflict/denial, and concurrent arrivals.

## 3. Model and delivery

- [x] 3.1 Connect protected chat gateway through a fake-first reply worker.
- [x] 3.2 Implement natural reply bubble planning with stable per-bubble idempotency keys.
- [x] 3.3 Create outbox items transactionally from a validated reply plan.
- [x] 3.4 Handle model timeout, invalid output, send rejection, and ambiguous delivery without duplicate effects.

## 4. Operations and verification

- [x] 4.1 Add reply-run list/detail/readiness APIs with sanitized stage evidence.
- [x] 4.2 Add dashboard views for pipeline state, blockers, retries, and context provenance.
- [x] 4.3 Add synthetic OneBot fixtures and fake model/transport end-to-end tests.
- [x] 4.4 Verify restart, sleep-like lease expiry, event replay, concurrency, and emergency pause behavior.
- [x] 4.5 Run Ruff, full backend tests, frontend build, and browser visual verification with all real network gates off.
- [x] 4.6 Update operational docs and archive the change only after implementation matches the accepted specs.
