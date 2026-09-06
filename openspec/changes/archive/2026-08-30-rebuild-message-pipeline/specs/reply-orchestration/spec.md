## ADDED Requirements

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
