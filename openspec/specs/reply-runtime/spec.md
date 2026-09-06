# reply-runtime Specification

## Purpose
Define how YCH continuously executes the durable reply pipeline under staged, owner-controlled activation while remaining recoverable, observable, and incapable of accidental real network effects.

## Requirements

### Requirement: Supervised reply-worker lifecycle
The system SHALL run the reply worker only when its explicit worker gate is enabled, SHALL supervise it within the application lifecycle, and SHALL expose whether the loop is configured, active, paused, stopping, or failed.

#### Scenario: Application starts with the default configuration
- **WHEN** the reply-worker gate is disabled
- **THEN** no reply-worker loop starts and readiness reports the worker as not enabled

#### Scenario: Enabled worker exits unexpectedly
- **WHEN** the supervised reply loop raises an unhandled error
- **THEN** the runtime records a sanitized failure, reports the loop as failed, and does not claim further runs until explicitly restarted or the process restarts

### Requirement: Staged reply activation
The system SHALL support `observe_only`, `shadow`, `owner_approved`, `limited_auto`, and `auto` as explicit requested modes and SHALL derive an effective mode that can only be equally or more restrictive than the requested mode.

#### Scenario: Observe-only mode receives a message
- **WHEN** an eligible inbound message creates a durable reply run while the effective mode is `observe_only`
- **THEN** the run remains observable without invoking a model or creating reply outbox items

#### Scenario: Shadow mode processes a message
- **WHEN** the effective mode is `shadow` and protected model gates permit inference
- **THEN** the system may assemble context, call the chat model, and store a non-deliverable candidate or plan, terminalizes the run as `shadow_completed`, and SHALL NOT create reply outbox items

#### Scenario: Owner-approved mode has no approval
- **WHEN** a reply plan is ready in `owner_approved` mode without a valid approval bound to that run and plan revision
- **THEN** the system holds the run in `awaiting_approval` before outbox handoff and exposes an approval-required state

#### Scenario: Reply approval expires
- **WHEN** the bounded approval window expires before the owner confirms the exact run and plan revision
- **THEN** the run becomes suppressed as approval-expired and is never delivered from that approval

#### Scenario: Limited-auto conversation is not eligible
- **WHEN** a conversation is absent from the active limited-auto eligibility set
- **THEN** it is processed no further than shadow behavior and no reply outbox item is created

#### Scenario: Auto mode is requested with blockers
- **WHEN** `auto` is requested but any mandatory readiness gate is false
- **THEN** the effective mode remains non-delivering and the blocker is observable

### Requirement: Revalidated effect boundaries
The runtime SHALL re-evaluate activation mode, conversation eligibility, approval evidence, model-network permission, and outbound permission immediately before each model invocation and immediately before each outbox handoff.

#### Scenario: Operator pauses during context assembly
- **WHEN** emergency pause becomes active after a run is claimed but before model invocation
- **THEN** the runtime does not call the model and preserves a recoverable, auditable run state

#### Scenario: Outbound gate closes after planning
- **WHEN** the outbound gate becomes false before outbox handoff
- **THEN** no reply outbox item is created even if the plan was produced under a less restrictive earlier state

### Requirement: Conversation eligibility is explicit and scoped
The system SHALL bind limited-auto eligibility to the bot account and exact private user or group conversation, SHALL default to ineligible, and SHALL NOT infer eligibility from friendship, message frequency, imported history, persona, or memory.

#### Scenario: Same user exists under two bot accounts
- **WHEN** only one bot-account conversation is allowlisted
- **THEN** the other bot account remains ineligible

#### Scenario: Private user speaks in a group
- **WHEN** the user's private conversation is allowlisted but the group conversation is not
- **THEN** the group run is not eligible for automatic delivery

### Requirement: No-real-network execution profile
The runtime SHALL provide a deterministic verification profile in which model and transport calls use injected fakes and all real NapCat, QQ, model-provider, Qzone, history, and web-search network paths remain disabled.

#### Scenario: Full runtime acceptance test
- **WHEN** synthetic OneBot fixtures are processed in the no-real-network profile
- **THEN** lifecycle, activation, context, planning, approval, outbox, pause, and recovery behavior can be verified without external calls

### Requirement: Windows restart and sleep recovery
The runtime SHALL use persisted UTC timestamps and leases rather than process uptime, SHALL reclaim only stages declared safe to repeat, and SHALL quarantine ambiguous delivery instead of resending it after restart or sleep.

#### Scenario: Computer sleeps past a context lease
- **WHEN** the process resumes after the context-assembly lease expires
- **THEN** a worker can reclaim that run with a new fencing token and the old token cannot advance it

#### Scenario: Process restarts during send
- **WHEN** an outbox item was sending and no confirmed provider outcome exists
- **THEN** the run becomes `delivery_unknown` and is not automatically sent again

### Requirement: Graceful shutdown stops new claims
The runtime SHALL stop claiming new reply runs after shutdown begins, request the active worker to stop, and bound shutdown waiting without converting uncertain external effects into retries.

#### Scenario: Shutdown begins while worker is idle
- **WHEN** the application receives its shutdown signal
- **THEN** the worker exits without claiming another run

#### Scenario: Shutdown times out around delivery
- **WHEN** shutdown cannot confirm whether a send completed
- **THEN** recovery follows ambiguous-delivery quarantine rather than automatic resend

### Requirement: Shadow model calls are owner-private only
When the effective reply mode is `shadow`, the system SHALL invoke the chat model only for the exact conversation `{bot_qq}:private:{owner_qq}` and SHALL NOT invoke the chat model for any other private user or group conversation.

#### Scenario: Owner private message in shadow mode
- **GIVEN** effective mode is `shadow` and outbound is disabled
- **WHEN** an inbound private message from the owner to the configured bot is processed
- **THEN** the chat model may be called and the run terminalizes as `shadow_completed` without a sendable outbox item

#### Scenario: Non-owner private message in shadow mode
- **GIVEN** effective mode is `shadow`
- **WHEN** an inbound private message from a different user is processed
- **THEN** the chat model is not called, no sendable outbox item is created, and the run does not complete as a delivered reply

### Requirement: Owner-approved model calls are owner-private only
When the effective reply mode is `owner_approved`, the system SHALL invoke the chat model only for the exact conversation `{bot_qq}:private:{owner_qq}` and SHALL NOT invoke the chat model for any other private user or group conversation.

#### Scenario: Owner private message in owner-approved mode
- **GIVEN** effective mode is `owner_approved` and outbound is enabled
- **WHEN** an inbound private message from the owner to the configured bot is processed
- **THEN** the chat model may be called and the run waits in `awaiting_approval` without a sendable outbox item until the owner approves

#### Scenario: Non-owner private message in owner-approved mode
- **GIVEN** effective mode is `owner_approved`
- **WHEN** an inbound private message from a different user is processed
- **THEN** the chat model is not called, no sendable outbox item is created, and the run does not wait for delivery approval

### Requirement: Limited-auto model calls are owner-private only
When the effective reply mode is `limited_auto`, the system SHALL invoke the chat model only for the exact conversation `{bot_qq}:private:{owner_qq}` and SHALL NOT invoke the chat model for any other private user or group conversation.

#### Scenario: Owner private message is allowlisted in limited-auto mode
- **GIVEN** effective mode is `limited_auto`, outbound is enabled, and `{bot_qq}:private:{owner_qq}` is eligible
- **WHEN** an inbound private message from the owner to the configured bot is processed
- **THEN** the chat model may be called and a sendable outbox item may be created without waiting for per-message approval

#### Scenario: Non-owner private message in limited-auto mode
- **GIVEN** effective mode is `limited_auto`
- **WHEN** an inbound private message from a different user is processed
- **THEN** the chat model is not called and no sendable outbox item is created even if that conversation is later present in the eligibility set

### Requirement: Auto model calls cover this bot's conversations
When the effective reply mode is `auto`, the system SHALL invoke the chat or vision model for every private conversation of the exact configured bot and for every group conversation of that bot when a trigger mentions the exact bot, and SHALL NOT invoke the model for a conversation key that belongs to a different bot account.

#### Scenario: Any private message in auto mode
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound private message from any user to the configured bot is processed
- **THEN** the chat or vision model may be called and a sendable outbox item may be created without waiting for per-message approval

#### Scenario: Owner private message in auto mode
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound private message from the owner to the configured bot is processed
- **THEN** the chat or vision model may be called and a sendable outbox item may be created without waiting for per-message approval

#### Scenario: Group mention in auto mode
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound group message in any group contains an `at` segment for the configured bot
- **THEN** the chat or vision model may be called and a sendable outbox item may be created for that group without waiting for per-message approval

#### Scenario: Group mention with only an emoji
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound group message mentions the bot and the only non-mention content is a `face` or `mface` segment
- **THEN** the chat or vision model may be called with a sanitized emoji placeholder and a sendable outbox item may be created

#### Scenario: Group message without mention in auto mode
- **GIVEN** effective mode is `auto`
- **WHEN** an inbound group message does not mention the bot
- **THEN** the chat or vision model is not called and no sendable outbox item is created

#### Scenario: Different bot conversation in auto mode
- **GIVEN** effective mode is `auto`
- **WHEN** a reply run conversation key belongs to a different bot account
- **THEN** the chat or vision model is not called and no sendable outbox item is created
