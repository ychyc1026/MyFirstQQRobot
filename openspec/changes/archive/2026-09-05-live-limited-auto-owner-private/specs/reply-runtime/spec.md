## ADDED Requirements

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
