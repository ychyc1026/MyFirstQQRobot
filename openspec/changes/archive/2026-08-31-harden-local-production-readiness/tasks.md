## 1. Domain contracts and additive persistence

- [x] 1.1 Define typed readiness profiles, probe statuses, decision/blocker DTOs, evidence freshness rules, capability scopes and sanitized reason codes.
- [x] 1.2 Define managed-artifact types, owner scopes, verification/reference/retention states, preview evidence and quarantine-batch state machine.
- [x] 1.3 Add the next additive SQLite migration for readiness decisions/probes, managed artifacts, reference revisions, retention previews, quarantine batches/items and audit correlation without changing existing effect gates.
- [x] 1.4 Implement repository create/read/list operations for immutable readiness decisions and probe evidence, scoped by bot and process instance.
- [x] 1.5 Implement repository upsert and compare-and-set operations for artifacts, references, single-use previews and quarantine batch transitions.
- [x] 1.6 Test migration backup enforcement, default-denied seeds, foreign keys, uniqueness, user isolation, stale revisions and rollback compatibility with a temporary database.

## 2. Launcher and readiness evaluation

- [x] 2.1 Implement a no-write launcher preflight result for configuration, database inspection, managed roots and administrator-port availability before `uvicorn.run`.
- [x] 2.2 Generate a process-instance ID at composition time and reject absent, stale or mismatched launcher evidence instead of fabricating a pass.
- [x] 2.3 Implement independently testable probes for database/backup, path confinement, administrator auth, ports, configured worker ceilings and durable pauses.
- [x] 2.4 Implement independently testable probes for exact OneBot bot identity/connection, model capability gates, outbox/outbound state, Qzone/history gates and reply activation scope.
- [x] 2.5 Implement profile policies for `local_start`, `offline_shadow` and `controlled_real_effect`, including structured blockers/warnings and required `unknown`/`stale` failure behavior.
- [x] 2.6 Implement bounded concurrent probe refresh, per-probe timeout, absolute observed/expires times and sanitized snapshot persistence.
- [x] 2.7 Test port conflict, current-process ownership, invalid admin auth, database failure, exact-bot mismatch, independent capability blockers and configured-versus-active reporting.
- [x] 2.8 Test Windows sleep-like wall-clock jumps, process restart, evidence TTL expiry and the rule that a prior process's pass never authorizes the new process.

## 3. Managed artifact inventory and verification

- [x] 3.1 Implement centralized managed-root validation that rejects traversal and Windows symlink/junction/reparse-point escape and accepts artifact IDs rather than client paths.
- [x] 3.2 Implement a migration-backup adapter that preserves the accepted newest-three/90-day policy and current SQLite/manifest revalidation.
- [x] 3.3 Implement privacy export and privacy deletion-backup adapters, including ZIP CRC, manifest, original-file bundle and explicit user ownership verification.
- [x] 3.4 Implement an imported-source adapter that only inventories database-referenced files under `storage/imports` and never recursively adopts unrelated files.
- [x] 3.5 Implement quarantine-batch discovery and reconciliation under project-managed `storage/trash` roots.
- [x] 3.6 Implement idempotent read-only reconciliation that registers legacy supported artifacts without moving, renaming, deleting or reading private contents into DTOs.
- [x] 3.7 Implement workflow reference providers for active privacy jobs, approvals, migration rollback windows, knowledge/import tasks and unresolved quarantine investigations.
- [x] 3.8 Test hash/size mismatch, invalid manifest, corrupt ZIP, missing sidecar, cross-user enumeration, path escape, unrelated-file exclusion and repeated-scan idempotency.

## 4. Retention preview and recoverable quarantine

- [x] 4.1 Implement retention classification that always applies integrity and reference protection before type-specific age/count policy and defaults non-migration privacy policies to no candidates unless configured.
- [x] 4.2 Implement short-lived preview creation bound to actor/session or owner, process instance, policy revision, ordered candidate IDs, digest/reference revisions, bytes and target batch type.
- [x] 4.3 Implement single-use preview confirmation with compare-and-set so concurrent or stale confirmations cannot move files.
- [x] 4.4 Implement bundle-aware same-volume quarantine moves with explicit `prepared`, `moving`, `quarantined`, `rollback_required`, `rolled_back` and `blocked` transitions.
- [x] 4.5 Implement reverse-order rollback for partial moves and restart reconciliation that preserves both copies when source/destination state is ambiguous.
- [x] 4.6 Adapt the existing migration-backup cleanup endpoint through the generalized retention service without weakening its integrity or recoverability behavior.
- [x] 4.7 Record sanitized preview, rejection, move, rollback, anomaly and reconciliation audits plus dashboard-only owner-report correlation.
- [x] 4.8 Test candidate mutation, policy/reference revision change, preview expiry/reuse, unauthorized confirmation, Windows file lock, sidecar failure, interrupted move and no-permanent-delete behavior.

## 5. Readiness guard at external-effect boundaries

- [x] 5.1 Implement an in-process `ReadinessGuard` that evaluates the exact bot, capability and scope without calling the application's own HTTP API.
- [x] 5.2 Integrate the guard immediately before protected chat/vision/image/stats model network calls while preserving each gateway's existing limits, circuit and enablement checks.
- [x] 5.3 Integrate the guard immediately before NapCat reply/proactive/owner-report delivery and keep outbox idempotency and `delivery_unknown` quarantine unchanged.
- [x] 5.4 Integrate the guard immediately before Qzone publication/profile access and live history access while preserving per-user authorization and freeze checks.
- [x] 5.5 Emit stable blocked evidence and dashboard-only reports without automatic retry or enabling the owner-report delivery route.
- [x] 5.6 Prove with boundary tests that readiness is necessary but never sufficient, capability failures remain independent and state/TTL changes after preview fail closed.

## 6. Authenticated APIs and control parity

- [x] 6.1 Add authenticated APIs for current profile summary, detailed evaluation, explicit refresh and sanitized historical decisions.
- [x] 6.2 Add authenticated APIs for artifact inventory/filtering, verification refresh, retention preview, confirmation and quarantine history using IDs only.
- [x] 6.3 Enforce administrator session and current-instance owner policy through shared application services rather than route- or UI-specific checks.
- [x] 6.4 Keep API DTOs free of credentials, private bodies, raw manifests, arbitrary absolute paths, confirmation hashes and reusable tokens.
- [x] 6.5 Add API tests for authentication, bot/user isolation, stale evidence, actor binding, audit correlation, dashboard/owner policy parity and error localization codes.

## 7. Dashboard readiness and retention surfaces

- [x] 7.1 Extend the API client and labels for readiness profiles, probe states, remediation codes, artifact states and quarantine outcomes.
- [x] 7.2 Replace the shell's coarse connectivity indicator with a concise truthful current-profile summary that keeps configured, active, paused, stale and blocked states distinct.
- [x] 7.3 Extend system settings with profile cards, grouped blockers/warnings, evidence source/revision/freshness, manual refresh and no secret/path disclosure.
- [x] 7.4 Extend privacy audit with managed artifact totals, user/system scope filters, verification/reference state, retention candidates and quarantine history.
- [x] 7.5 Add an explicit preview-and-confirm surface stating target types/scopes/count/bytes, recoverable quarantine semantics and the absence of permanent deletion or live database restore.
- [x] 7.6 Handle loading, empty, long-list, partial-probe failure, stale preview, file anomaly and backend-disconnected states without manufacturing success locally.
- [x] 7.7 Verify all affected desktop and narrow-screen routes in the in-app browser for shared page-layout rules, keyboard operation, focus return, overlays, overflow and localized copy.

## 8. No-network acceptance, operations and handoff

- [x] 8.1 Build a production-composition acceptance harness with temporary SQLite/files, controlled UTC clock, deterministic process IDs, synthetic launcher evidence and fake probe/transport adapters.
- [x] 8.2 Assert readiness evaluation, artifact scan, preview, quarantine, rollback, reconciliation and dashboard-only reporting never construct or call real NapCat, QQ, model, Qzone, history, search or Internet clients.
- [x] 8.3 Exercise clean startup, migration-required startup, restart, Windows sleep, port conflict, worker interruption, OneBot bot mismatch, evidence expiry and emergency-pause precedence.
- [x] 8.4 Exercise legacy artifact adoption, large-file refresh, cross-user isolation, corrupt artifacts, path escape, concurrent confirmation, partial move and interrupted-batch recovery.
- [x] 8.5 Run Ruff format/check, full Pytest, frontend tests/type/build, strict OpenSpec validation, sensitive-data scanning and browser visual verification with every real network gate off.
- [x] 8.6 Update `.env.example`, startup/readiness runbook, database and privacy retention docs, implementation status, roadmap, handoff and owner command/help text, correcting stale statements about already implemented migration-backup retention.
- [x] 8.7 Verify the real database and NapCat state were not modified during implementation, record rollback evidence, and archive the OpenSpec only after every task and accepted requirement passes.
