## ADDED Requirements

### Requirement: Backend-enforced reply activation transitions
The system SHALL validate reply-mode transitions in the backend against authenticated authority, mandatory readiness gates, allowed transition rules, and current emergency-pause state.

#### Scenario: Dashboard hides a restriction incorrectly
- **WHEN** a client directly requests a more permissive mode without satisfying backend checks
- **THEN** the request is rejected regardless of the client UI

#### Scenario: Non-owner QQ requests activation
- **WHEN** a QQ command sender is not the authenticated current-instance owner
- **THEN** no reply-runtime state changes

### Requirement: Independent real-effect gates
The system SHALL require reply-worker enablement, a delivering activation mode, protected chat-model enablement, model-network enablement, outbound enablement, active outbox delivery, valid OneBot authentication, and matching bot connectivity as independent conditions for automatic real replies.

#### Scenario: All but one gate are enabled
- **WHEN** any required real-effect gate is false
- **THEN** the system performs no automatic real reply and reports the exact blocker

### Requirement: Persistent emergency stop
The system SHALL provide a persistent emergency stop that immediately prevents new model calls and outbox handoffs, survives restart, has precedence over every requested mode, and requires an authenticated explicit resume action.

#### Scenario: Process restarts after emergency stop
- **WHEN** the owner previously activated emergency stop
- **THEN** the reply runtime remains effectively paused after restart even if configuration requests `auto`

#### Scenario: Emergency stop is activated with queued reply bubbles
- **WHEN** unsent reply bubbles already exist in outbox
- **THEN** the reply dispatcher does not claim them while paused and does not discard their audit evidence

### Requirement: Audited reply-runtime control
The system SHALL audit requested mode changes, rejected transitions, allowlist mutations, approval decisions, emergency stop/resume, worker failures, and recovery takeovers with actor, source, timestamp, prior state, resulting state, and sanitized reason.

#### Scenario: Owner enables a limited-auto conversation
- **WHEN** the authenticated owner adds an eligible conversation
- **THEN** the audit trail records the exact bot and conversation scope without storing message content or secrets
