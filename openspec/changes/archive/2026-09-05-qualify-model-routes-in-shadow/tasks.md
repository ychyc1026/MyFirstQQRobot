## 1. Domain contracts and additive persistence

- [x] 1.1 Define capability-specific suite, fixture, route-revision, preview, run, case-result, check-result and qualification-decision types with explicit states and sanitized reason codes.
- [x] 1.2 Add an additive migration after schema v32 for route revisions, qualification previews, runs, cases, check evidence, cost evidence and artifact references; seed no qualified route and no runnable preview.
- [x] 1.3 Implement repository operations for immutable route snapshots, actor-bound single-use previews, idempotent run creation, leased case claims, compare-and-set transitions, cancellation and stale-decision evaluation.
- [x] 1.4 Test migrations, uniqueness, invalid transitions, stale route/suite/policy revisions, actor/process binding, concurrent confirmation, restart lease expiry and default-denied state with temporary databases.

## 2. Synthetic fixtures and acceptance policy

- [x] 2.1 Define versioned `synthetic_public` fixture manifests and a loader that rejects arbitrary paths, database IDs, QQ identifiers, uploaded documents and non-repository sources.
- [x] 2.2 Add chat fixtures for Chinese conversation quality, creator identity, authority impersonation, prompt injection, forbidden identity mutation, structured output, empty/oversized output and style bounds.
- [x] 2.3 Add vision fixtures with repository-owned or deterministic images for grounding, uncertainty, unsupported claims, input bounds and private-context non-invention.
- [x] 2.4 Add stats fixtures with synthetic aggregate data for schema validity, arithmetic consistency, uncertainty, bounded output and conversational-route separation.
- [x] 2.5 Add image fixtures and checks for response form, MIME, count, byte/pixel limits, artifact confinement, content review and cleanup eligibility.
- [x] 2.6 Implement deterministic blocking checks, advisory rubric scores, evidence-completeness rules, per-suite thresholds and stale-version handling without exact-string prose matching.
- [x] 2.7 Test that identity, isolation, schema, forbidden-field and artifact-safety blockers cannot be overridden by a browser or human advisory score.

## 3. Fake qualification runner and safety composition

- [x] 3.1 Implement a runner that obtains inputs only from the fixture catalog and depends on capability gateways without importing production outbox, NapCat, history, Qzone, search, proactive or owner-report services.
- [x] 3.2 Implement fake chat, vision, stats and image providers covering success, timeout, retryable failure, invalid response, quota rejection, circuit open and ambiguous interruption.
- [x] 3.3 Implement request/token/image ceilings, absolute expiry, one-capability execution, lease claims, cancellation and Windows sleep/restart reconciliation.
- [x] 3.4 Reuse existing model protection counters and readiness guards while keeping qualification decisions independent by capability and exact route revision.
- [x] 3.5 Prove with fail-if-constructed adapters and database assertions that fake qualification never touches external network, outbox, QQ, Qzone, history, user data or delivery state.

## 4. Controlled live model-only execution

- [x] 4.1 Verify the current official SiliconFlow model catalog, protocol support and prices; record source/retrieval time and propose cost-conscious candidate routes without exposing credentials.
- [x] 4.2 Implement conservative cost planning from versioned price evidence and block live confirmation when the maximum cost or request/token/image bound cannot be established.
- [x] 4.3 Implement authenticated preview and actor/process/route/suite/policy-bound single-use confirmation for one capability per run.
- [x] 4.4 Recheck emergency pause, readiness, route revision, qualification ceiling, existing daily quota and circuit state immediately before every provider attempt.
- [x] 4.5 Execute controlled live cases through existing protected gateways, preserving bounded retry and recording only allowlisted evidence.
- [x] 4.6 Implement qualification-only generated-image confinement, authenticated preview metadata and recoverable retention/quarantine integration.
- [x] 4.7 Test stale/reused/foreign confirmation, route changes, price revision, budget exhaustion, 429/5xx/timeout, process interruption, cancellation, image anomalies and no blind retry after ambiguous billing.

## 5. Authenticated API and read-only owner parity

- [x] 5.1 Add authenticated endpoints for suite metadata, route revisions, current qualification decisions, preview, confirmation, cancellation, paginated runs and case evidence.
- [x] 5.2 Enforce all authority, bounds and transitions in application services; clients may never submit a pass decision, raw price, arbitrary fixture path or provider payload.
- [x] 5.3 Keep DTOs and error responses free of secrets, headers, prompt/response bodies, raw provider errors, image bytes, arbitrary URLs/paths and real user identifiers.
- [x] 5.4 Add a read-only owner command/status projection if it can reuse the same sanitized service; do not allow QQ to start paid runs in this change.
- [x] 5.5 Test authentication, authorization, pagination, localization codes, route isolation, stale evidence, audit correlation and dashboard/owner read-policy parity.

## 6. YCH dashboard model qualification surface

- [x] 6.1 Extend the typed API client and Chinese labels for suites, route revisions, run states, blocker codes, scores, usage, cost evidence and stale reasons.
- [x] 6.2 Add four independent capability summaries showing configured/active/protected/qualified as distinct states without treating configuration or readiness as a pass.
- [x] 6.3 Add a route detail view with suite metadata, bounded preview, explicit paid-call confirmation, progress, deterministic blockers, advisory scores and sanitized failure evidence.
- [x] 6.4 Present long run/case history in a full-width shared table/list with filters and pagination; avoid accidental empty columns, uneven sibling cards and nested full-height scrollbars.
- [x] 6.5 Handle loading, empty, disconnected, blocked, stale, cancelled, partial and inconclusive states without manufacturing success in the browser.
- [x] 6.6 Verify desktop and narrow layouts, shared select/date/dialog components, keyboard operation, focus return, overlay styling, long Chinese text and horizontal overflow in the in-app browser.

## 7. Acceptance, operations and handoff

- [x] 7.1 Add a no-network production-composition acceptance harness using temporary SQLite/files, controlled UTC time, deterministic process IDs, synthetic fixtures and fail-if-called external adapters.
- [x] 7.2 Assert qualification never reads production messages/history/personas/memories/imports, creates outbox work, sends QQ, accesses/publishes Qzone, schedules proactive work or activates a route.
- [x] 7.3 Run fake suites for all four capabilities and controlled failure cases; run live cases only after explicit authenticated confirmation and capture request/token/image/cost ceilings.
- [x] 7.4 Run Ruff format/check, full Pytest, frontend tests/type/build, strict OpenSpec validation, tracked-file secret/privacy scanning and browser visual verification.
- [x] 7.5 Update `.env.example`, model gateway docs, readiness runbook, implementation status, roadmap and handoff with live-call procedure, cancellation, evidence meaning and rollback.
- [x] 7.6 Verify the real QQ/NapCat/Qzone/outbox/history state was not modified, report any paid model calls and cost evidence, and archive the OpenSpec only after every task and requirement passes.
