# reply-delivery Specification

## Purpose
Define deterministic reply planning, transactional outbox handoff, ordered delivery evidence, and fail-closed handling of ambiguous outcomes.

## Requirements

### Requirement: Stable reply plan
The system SHALL validate model output into an ordered reply plan before any outbound item is created.

#### Scenario: Model output violates the reply contract
- **WHEN** the result cannot be safely normalized into supported bubbles
- **THEN** the run fails or degrades according to policy without sending partial unvalidated content

### Requirement: Idempotent outbox handoff
The system SHALL assign every planned bubble a stable idempotency key and SHALL reuse the same outbox record across retries.

#### Scenario: Worker retries after creating the outbox
- **WHEN** the reply worker resumes the same run
- **THEN** it finds the existing bubble outbox records instead of inserting duplicates

### Requirement: Ambiguous delivery quarantine
The system SHALL distinguish confirmed rejection from an ambiguous transport outcome and SHALL NOT automatically resend an ambiguous item.

#### Scenario: Connection drops after send request
- **WHEN** the system cannot determine whether QQ accepted the message
- **THEN** the delivery becomes `delivery_unknown` and requires reconciliation or operator action

### Requirement: Natural multi-bubble delivery
The system SHALL support an ordered, bounded number of reply bubbles while preserving semantic completeness and configured per-user style limits.

#### Scenario: Reply plan contains multiple bubbles
- **WHEN** delivery is permitted
- **THEN** bubbles are enqueued and sent in order without interleaving another run for the same conversation
