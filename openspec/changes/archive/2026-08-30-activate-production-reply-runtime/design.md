## Context

See `proposal.md` for motivation. The archived reply-pipeline change provides durable runs, trigger evidence, context manifests, protected chat gateways, deterministic bubble planning, transactional outbox handoff, delivery evidence, lease fencing, and a read-only operations page. The current composition root deliberately reports `reply_worker_wired=false`: ingestion can persist messages, but no lifecycle-managed worker advances reply runs.

This application runs on a personal Windows computer where process termination, sleep, wall-clock jumps, and network loss are normal. NapCat, model providers, and the dashboard are separate trust boundaries. SQLite is the authoritative state store; `.env` is only a startup ceiling and never proof that a high-risk transition was authorized.

## Goals / Non-Goals

**Goals:**

- Wire one supervised reply runtime into startup and graceful shutdown without changing default external behavior.
- Make activation an explicit, persisted, backend-enforced state machine with a durable emergency stop.
- Complete shadow runs, hold approval runs, and deliver eligible runs without blocking later conversation work indefinitely.
- Use the same authorization and audit policy for current-instance owner QQ commands and dashboard controls.
- Recheck mutable safety conditions at both irreversible boundaries: model network call and outbox creation.
- Preserve idempotency, fencing, user isolation, and ambiguous-delivery quarantine across restart and sleep.

**Non-Goals:**

- Selecting or configuring the user's real chat or image provider credentials.
- Enabling NapCat reverse WebSocket, real model networking, real outbox delivery, Qzone, history access, proactive messages, or owner-report delivery.
- Changing immutable YCH/维护者 identity or the account-partition policy.
- Automatically classifying old friends, granting historical access, or deriving delivery eligibility from user data.
- Automatically reconciling or resending `delivery_unknown` items.

## Decisions

### 1. Persist runtime policy per bot account

Add `reply_runtime_state` keyed by `bot_qq` with requested mode, emergency-pause flag, monotonically increasing revision, updated actor/source, and timestamps. Add `reply_runtime_eligibility` keyed by `bot_qq + conversation_key`, bind approval evidence to `run_id + reply_plan_revision/hash`, and add durable deferred-trigger evidence for messages arriving after an active run leaves settling.

The effective mode is derived on every status read and effect-boundary check from persisted state plus immutable configuration ceilings and live readiness. It is not persisted as independent truth because that could become stale when connectivity, a circuit breaker, or an environment gate changes.

Alternative considered: keep mode only in `.env`. Rejected because dashboard/QQ controls, restart persistence, actor attribution, confirmation revision, and emergency stop require durable mutable state.

### 2. Treat configuration as a ceiling, not an activation command

New settings include `YCH_REPLY_WORKER_ENABLED=false`, `YCH_REPLY_RUNTIME_MAX_MODE=observe_only`, and a bounded poll interval. Startup configuration can prevent a mode but cannot promote persisted state. A real effect requires the intersection of requested mode, configured maximum, durable pause, conversation eligibility or approval, model protections, outbound protections, OneBot authentication, and matching connection state.

Mode behavior is:

| Mode | Context/model | Outbox handoff |
|---|---|---|
| `observe_only` | No | No |
| `shadow` | Allowed only through protected chat gates | Never |
| `owner_approved` | Allowed | Only with current run/plan approval |
| `limited_auto` | Allowed | Only for exact active conversation eligibility |
| `auto` | Allowed | For policy-eligible conversations after all global gates pass |

Any missing prerequisite demotes the effective behavior to `shadow` or `observe_only`; it never upgrades behavior.

Alternative considered: a single `REPLIES_ENABLED` boolean. Rejected because it cannot represent shadow evaluation, approval trials, per-conversation rollout, or truthful downgrade reasons.

### 3. Add explicit runtime completion and waiting stages

Extend the reply-run state machine with `shadow_completed` as a terminal result and `awaiting_approval` as a non-terminal state. A shadow run must become terminal so it releases the per-conversation active-run constraint. An owner-approved run stores a stable plan revision and waits without creating outbox records. Approval resumes only that exact revision; a changed plan invalidates the approval. The approval window is bounded to 30 minutes by default; expiry suppresses the run instead of sending a stale reply.

Runs stopped before model invocation because of a transient pause remain safely reclaimable. Runs denied by stable policy become suppressed with a machine-readable reason. Mode changes never rewrite historical terminal outcomes. Messages arriving after execution begins are written as ordered deferred triggers; terminalization atomically promotes them into one successor settling run so they are neither lost nor appended to an already approved plan.

Alternative considered: leave shadow runs at `planning_reply`. Rejected because one permanent active run would block later messages in that conversation and blur “work waiting” with “shadow evaluation complete.”

### 4. Compose a dedicated runtime service around existing stages

Introduce a lifecycle worker that calls an application-level runtime service. One `run_once` iteration:

1. reads the current effective policy and returns without claiming in `observe_only` or emergency pause;
2. claims a ready run with a fresh fencing token;
3. assembles isolated context;
4. rechecks the model boundary and invokes the protected chat gateway;
5. validates and persists a deterministic plan;
6. terminalizes as shadow, waits for revision-bound approval, or rechecks delivery eligibility;
7. transactionally hands the plan to outbox only when the second boundary passes.

The worker owns polling, stop signaling, loop health, and sanitized exception accounting. Domain/application services own decisions and transitions. The worker never calls NapCat directly; delivery remains the outbox dispatcher's responsibility.

Alternative considered: extend `FakeFirstReplyWorker` with many flags. Rejected because test-only progression and production lifecycle policy would become coupled and make unsafe combinations difficult to reason about.

### 5. Use two independent pauses with one emergency action

Reply-runtime pause prevents claims, model calls, and handoffs. Outbox pause prevents already-created items from being claimed. The emergency-stop application operation persists both states transactionally where possible and reports partial failure as a blocked runtime rather than pretending success. Resume is explicit and does not restore a more permissive effective mode until readiness is recalculated.

This separation preserves the outbox as a shared subsystem for non-reply operations while the emergency reply action still fences queued reply bubbles through reply-linked outbox evidence.

Alternative considered: cancel all queued reply outbox rows. Rejected because cancellation is destructive to pending intent, complicates rollback, and is not necessary to stop effects.

### 6. Use revision-bound control previews

Transitions into `owner_approved`, `limited_auto`, or `auto`, eligibility mutations, approval decisions, emergency stop, and resume go through one `ReplyRuntimeControlService`. High-risk dashboard operations first create a short-lived preview containing the current runtime revision, readiness hash, affected bot/conversation scope, and requested result. Confirmation succeeds only if that evidence still matches.

Owner QQ commands authenticate from the OneBot sender identity and current instance ownership; message text never authenticates authority. Dashboard requests require an administrator session. Both surfaces invoke the same service and produce the same audit schema.

Alternative considered: let each surface validate independently. Rejected because policy drift between dashboard and QQ control would make authorization and audit behavior inconsistent.

### 7. Keep runtime health distinct from business outcomes

The worker snapshot contains configured gate, requested/effective mode, loop state, pause source, last iteration/claim/progress timestamps, recovered count, and last sanitized loop failure. Reply-run failures remain on reply runs; a model rejection is not reported as a dead worker. Readiness includes static configuration, live OneBot connection, model circuit state, outbox state, and persistent policy blockers.

The `/pipeline` page consumes sanitized DTOs only. It does not receive API keys, prompt/context bodies, reply text, raw OneBot payloads, lease tokens, or control-preview secrets beyond the one-time confirmation handle required for the authenticated action.

### 8. Make the no-network profile a composition-root choice

Tests construct the production runtime service with deterministic fake chat and transport adapters, a temporary SQLite database, synthetic OneBot events, and a controllable clock. Production settings cannot select the fake profile accidentally. Acceptance tests assert that all external clients remain unused unless a test explicitly injects the fake implementation.

Alternative considered: a runtime `dry_run` branch inside real adapters. Rejected because a misplaced condition could still construct or call real clients and because dependency injection gives stronger evidence that no real network path exists.

## Risks / Trade-offs

- **[Risk] Persisted requested `auto` survives restart while external conditions change** → Effective mode is recomputed and emergency pause/configuration ceilings override it; no startup promotion occurs.
- **[Risk] Mode or eligibility changes between planning and handoff** → Recheck revision, gates, eligibility, and approval inside the handoff boundary; fail closed on mismatch.
- **[Risk] Shadow/approval stages block a conversation forever** → Shadow becomes terminal; awaiting approval has explicit expiry/cancel behavior and new messages form a successor according to a documented policy rather than joining an executing plan.
- **[Risk] Emergency stop races with outbox claim** → Persist runtime pause and outbox pause, fence reply-linked claims, and quarantine any outcome that became ambiguous during the race.
- **[Risk] Worker loop hides repeated programming errors** → Record loop failure and stop supervision after an unhandled error instead of endlessly retrying unknown code defects.
- **[Risk] `auto` is too broad for first real trial** → Configuration maximum defaults to `observe_only`; rollout documentation requires shadow, then owner-approved, then a narrow limited-auto allowlist before auto can be considered.
- **[Trade-off] More stages and policy records increase operational complexity** → They replace implicit flags with auditable evidence and allow safe recovery, which is necessary for a local machine runtime.

## Migration Plan

1. Add typed mode/stage contracts and an additive SQLite migration for runtime state, eligibility, approval binding, deferred triggers, and runtime events. Existing databases use the verified pre-migration backup path.
2. Seed each enabled bot with requested mode `observe_only`, emergency pause enabled, revision `1`, and an empty eligibility set. Do not create outbox rows during migration.
3. Add repository compare-and-set operations and tests before wiring any worker.
4. Build the runtime service and worker against fake gateways; keep configuration maximum and worker gate off.
5. Add shared owner/dashboard control operations, readiness, and UI.
6. Wire lifecycle supervision with all default gates off and complete no-network restart/sleep/shutdown acceptance tests.
7. Document staged rollout. Real provider and NapCat configuration remains a later operator action after this change is implemented and reviewed.

Rollback is code-first: stop the reply worker, persist emergency pause, pause outbox, restore the previous application version, and retain the additive tables and new run-stage evidence. If schema rollback is ever required, restore the verified pre-migration SQLite backup while all workers are stopped; do not down-migrate a database after real reply evidence has been written.
