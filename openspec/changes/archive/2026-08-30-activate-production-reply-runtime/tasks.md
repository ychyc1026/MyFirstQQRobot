## 1. Runtime contracts and persistence

- [x] 1.1 Define typed activation modes, effective-mode decisions, runtime lifecycle states, `shadow_completed` and `awaiting_approval` stages, approval bindings, eligibility scopes, and sanitized runtime failure codes.
- [x] 1.2 Add an additive SQLite migration for per-bot runtime state, conversation eligibility, revision-bound reply approvals, deferred triggers, and runtime events, seeded as `observe_only` plus emergency-paused with an empty eligibility set.
- [x] 1.3 Implement compare-and-set repository operations for runtime revisions, emergency pause, eligibility, approval expiry, worker health, and audit evidence.
- [x] 1.4 Implement ordered deferred-trigger persistence and atomic successor-run promotion when the active conversation run becomes terminal.
- [x] 1.5 Test constraints, default seeds, concurrent mutations, stale revisions, deferred-message ordering, migration backup behavior, and rollback safety without external network access.

## 2. Activation policy and shared control

- [x] 2.1 Implement effective-mode evaluation as the intersection of requested mode, configuration ceiling, persistent pause, live readiness, model protections, outbound protections, OneBot authentication, and matching bot connectivity.
- [x] 2.2 Implement exact bot-and-conversation limited-auto eligibility that defaults to denied and is independent of friendship, history, persona, memory, or imported documents.
- [x] 2.3 Implement 30-minute revision-bound owner approval, expiry suppression, stale-plan rejection, and explicit cancellation for `awaiting_approval` runs.
- [x] 2.4 Implement one `ReplyRuntimeControlService` for status, transition preview/confirmation, eligibility changes, approval decisions, emergency stop, and resume.
- [x] 2.5 Add authenticated current-instance owner QQ commands that call the shared control service and reject group commands, non-owner senders, and textual identity claims.
- [x] 2.6 Record mode changes, rejected transitions, eligibility mutations, approvals, stop/resume, worker failures, and lease takeovers with sanitized actor/source and before/after evidence.

## 3. Production runtime and lifecycle

- [x] 3.1 Compose a production reply-runtime service from existing orchestration, isolated context assembly, protected chat gateway, deterministic planner, and transactional outbox handoff.
- [x] 3.2 Revalidate mode, pause, runtime revision, eligibility or approval, model gate, and outbound gate immediately before model invocation and immediately before outbox handoff.
- [x] 3.3 Terminalize shadow runs as `shadow_completed`, hold owner-approved plans without outbox creation, and fail closed with machine-readable evidence when a run loses eligibility.
- [x] 3.4 Implement a durable reply worker with bounded polling, stop signaling, loop health, last progress, sanitized failure state, and no endless retry after an unhandled loop failure.
- [x] 3.5 Wire the worker into FastAPI startup and graceful shutdown behind `YCH_REPLY_WORKER_ENABLED=false`, `YCH_REPLY_RUNTIME_MAX_MODE=observe_only`, and validated polling/approval settings.
- [x] 3.6 Coordinate reply emergency stop with reply-linked outbox claims so queued bubbles remain auditable but cannot be claimed until an explicit safe resume.

## 4. Operations API and readiness

- [x] 4.1 Extend reply readiness with requested/effective mode, configuration ceiling, worker lifecycle, persistent pause, live OneBot/model/outbox blockers, last progress, approval backlog, and recovery counts.
- [x] 4.2 Add administrator-session APIs for runtime status, high-risk transition preview/confirmation, eligibility management, approval/cancel, emergency stop, and resume.
- [x] 4.3 Sanitize runtime DTOs so APIs never expose API keys, prompt/context bodies, reply text, raw OneBot payloads, lease tokens, or reusable confirmation secrets.
- [x] 4.4 Add API and control parity tests proving dashboard and owner QQ operations reach the same policy decisions and audit schema.

## 5. Dashboard control surface

- [x] 5.1 Extend `/pipeline` with requested/effective mode, truthful downgrade blockers, worker health, pause source, last progress, approval backlog, and limited-auto scope.
- [x] 5.2 Add explicit preview-and-confirm flows for delivering modes, eligibility changes, approval, emergency stop, and resume; stale previews must be rejected and refreshed.
- [x] 5.3 Add approval expiry, successor-message, recovered-lease, worker-failed, and `delivery_unknown` states without offering blind retry controls.
- [x] 5.4 Verify desktop and narrow-screen layouts, loading/empty/error states, keyboard interaction, and theme consistency in the in-app browser against a synthetic temporary database.

## 6. No-network acceptance and operational handoff

- [x] 6.1 Build a composition-root test harness using temporary SQLite, fake clock, synthetic OneBot fixtures, fake protected chat gateway, and fake transport; assert that real NapCat, QQ, model, Qzone, history, and search clients are never called.
- [x] 6.2 Exercise `observe_only`, `shadow`, `owner_approved`, `limited_auto`, and `auto` in the fake harness, including default deny, exact allowlist scope, approval expiry, mode downgrade, and outbox idempotency.
- [x] 6.3 Verify concurrent conversations, messages arriving during execution, restart, Windows sleep-like lease expiry, stale worker fencing, graceful shutdown, loop crash, emergency-stop races, and ambiguous delivery quarantine.
- [x] 6.4 Run Ruff format/check, the full backend test suite, frontend type/build checks, strict OpenSpec validation, sensitive-data scanning, and browser visual verification with every real network gate off.
- [x] 6.5 Update `.env.example`, readiness/runbook documentation, owner command help, dashboard guidance, rollback procedure, and staged rollout instructions from shadow to owner-approved to a narrow limited-auto allowlist.
- [x] 6.6 Archive `activate-production-reply-runtime` only after implementation matches every accepted requirement and the no-real-network acceptance suite passes.
