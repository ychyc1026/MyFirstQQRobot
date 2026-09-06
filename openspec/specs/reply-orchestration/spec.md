# reply-orchestration Specification

## Purpose
Define the durable per-conversation reply state machine, trigger coalescing, lease fencing, restart recovery, and terminal successor promotion.

## Requirements

### Requirement: Durable reply-run state machine
The system SHALL represent each reply attempt as a durable run with explicit stages, trigger messages, policy snapshot, attempts, leases, evidence references, and terminal outcome.

#### Scenario: Process stops during context assembly
- **WHEN** a worker lease expires after an unclean shutdown
- **THEN** a later worker can safely reclaim the non-terminal run without creating a duplicate terminal run

### Requirement: Per-conversation serialization
The system SHALL execute at most one context/model planning run per conversation while permitting different conversations to progress independently.

#### Scenario: Two users message simultaneously
- **WHEN** separate private conversation keys receive messages
- **THEN** neither user's run blocks or consumes the other user's messages or context

### Requirement: Burst settling
The system SHALL collect nearby inbound messages into one pending run only before model execution begins and SHALL preserve their original order.

#### Scenario: User sends three short messages quickly
- **WHEN** all three arrive inside the configured settling window
- **THEN** one run processes the ordered three-message trigger set

### Requirement: Explicit suppression and failure
The system SHALL record a machine-readable reason when a message is intentionally not answered or a reply run fails.

#### Scenario: Message type is unsupported
- **WHEN** no safe interpretation is available
- **THEN** the run becomes suppressed without calling a model or creating an outbox item

### Requirement: Activation-aware run progression
The system SHALL persist enough activation evidence on each reply run to explain why it stopped, waited, ran in shadow, required approval, or became eligible for delivery.

#### Scenario: Effective mode becomes more restrictive
- **WHEN** a run can no longer advance under the current effective mode
- **THEN** it remains in or enters an explicit recoverable waiting or suppressed state with a machine-readable reason

### Requirement: Fenced runtime recovery
The system SHALL require the current unexpired lease token for every runtime transition after a claim and SHALL reject progress from an earlier worker after lease takeover.

#### Scenario: Old worker resumes after Windows sleep
- **WHEN** another worker has reclaimed the expired run
- **THEN** the old worker cannot store context, invoke progression, approve handoff, or alter the terminal result

### Requirement: Mode transition does not rewrite past outcomes
Changing the requested activation mode SHALL affect only future progression and SHALL NOT rewrite completed, failed, suppressed, rejected, or delivery-unknown evidence.

#### Scenario: Operator changes from shadow to limited auto
- **WHEN** earlier runs contain shadow candidates
- **THEN** those runs are not retroactively delivered unless a separately authorized, revision-bound action explicitly resumes an eligible non-terminal run

### Requirement: Arrivals during execution are durable successors
The system SHALL preserve inbound messages that arrive after a run leaves settling as ordered deferred triggers and SHALL create a successor run after the active run becomes terminal, without appending them to an already assembled or approved plan.

#### Scenario: User sends another message while approval is pending
- **WHEN** the conversation has a run in `awaiting_approval`
- **THEN** the new message is stored as a deferred trigger for a successor and cannot alter the plan awaiting approval

#### Scenario: Active run becomes terminal
- **WHEN** one or more deferred triggers exist for that conversation
- **THEN** exactly one successor run is created with those triggers in original order and enters the settling policy
