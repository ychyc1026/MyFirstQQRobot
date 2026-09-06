## MODIFIED Requirements

### Requirement: Idempotent inbound persistence
The system SHALL derive a stable event key, prevent duplicate message persistence, and create or wake at most one eligible pending reply run for the accepted conversational message.

#### Scenario: NapCat repeats an event
- **WHEN** an already stored event is received again
- **THEN** the repository reports it as duplicate and creates neither another message nor another reply trigger

#### Scenario: New conversational event is committed
- **WHEN** an accepted private or group message is durably persisted
- **THEN** its reply trigger is committed atomically or can be deterministically recovered without losing the message
