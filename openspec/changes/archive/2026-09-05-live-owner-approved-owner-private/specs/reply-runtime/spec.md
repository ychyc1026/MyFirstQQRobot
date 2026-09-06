## ADDED Requirements

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
