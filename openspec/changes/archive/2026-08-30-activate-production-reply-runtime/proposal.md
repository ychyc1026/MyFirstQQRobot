## Why

The durable reply pipeline can already assemble isolated context, call an injected model, plan bubbles, and hand them to an outbox, but no reply worker is wired into the application lifecycle. This change turns that verified pipeline into an operable runtime without allowing startup, a dashboard click, or partial configuration to silently enable real QQ replies.

## What Changes

- Add a supervised reply runtime that can continuously claim durable reply runs, resume safe work after restart or Windows sleep, and shut down without abandoning ownership silently.
- Add an explicit activation state machine: `observe_only`, `shadow`, `owner_approved`, `limited_auto`, and `auto`; persisted emergency pause overrides every mode.
- Keep reply execution gated independently from OneBot ingestion, model networking, outbox delivery, and NapCat connectivity.
- Add allowlist and approval enforcement so early real-message trials can be limited to explicitly selected conversations.
- Route dashboard actions and authenticated current-instance owner QQ commands through the same authorization, transition, and audit service.
- Extend readiness and runtime observability with configured mode, effective mode, loop health, last progress, pause source, blockers, and restart recovery evidence.
- Add a deterministic no-real-network runtime mode using fake model and fake transport adapters for end-to-end verification.
- Preserve `delivery_unknown` quarantine and forbid automatic resend during runtime recovery.
- Update operational documentation with staged activation, rollback, restart, and emergency-stop procedures.

Implementing this change introduces the ability to run the reply pipeline continuously, but it does **not** enable real QQ, model, or network behavior by default. Real effects remain impossible until their independent configuration gates are explicitly enabled and the selected activation mode permits the conversation. It introduces no destructive behavior or data migration that removes existing records.

## Capabilities

### New Capabilities

- `reply-runtime`: Supervised reply-worker lifecycle, staged activation modes, eligibility enforcement, emergency pause, restart recovery, and no-network execution.

### Modified Capabilities

- `reply-orchestration`: Add runtime-safe claiming behavior for mode changes, shutdown, and recovered runs.
- `safety-controls`: Require backend-enforced activation transitions, instance-owner authorization, independent gates, and persistent emergency stop.
- `dashboard-operations`: Expose truthful runtime health and provide audited controls without treating UI state as authorization.

## Impact

- Backend configuration gains reply-runtime mode, worker, polling, and limited-auto eligibility settings, all defaulting to inactive values.
- Application startup and shutdown supervise a reply worker alongside existing durable workers.
- Reply orchestration gains an application service that evaluates effective mode before model invocation and before outbox handoff.
- SQLite stores the requested/effective activation state, persistent pause, allowlist or approval evidence, and runtime audit events; migration follows the existing verified-backup startup policy.
- Owner QQ commands and authenticated dashboard endpoints call the same control service.
- The `/pipeline` dashboard adds lifecycle health, activation controls, and blocker explanations while retaining sanitized evidence boundaries.
- Automated tests use temporary databases, fake time, fake model gateways, and fake transports; they do not connect to NapCat, QQ, model providers, Qzone, or history APIs.
