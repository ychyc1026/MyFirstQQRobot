## ADDED Requirements

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
