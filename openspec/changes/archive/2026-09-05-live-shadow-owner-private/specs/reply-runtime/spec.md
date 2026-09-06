## ADDED Requirements

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
