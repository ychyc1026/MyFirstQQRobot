## ADDED Requirements

### Requirement: Auto model calls are limited to named private conversations
When the effective reply mode is `auto`, the system SHALL invoke the chat model only for `{bot_qq}:private:{owner_qq}` and `{bot_qq}:private:2000000003`, and SHALL NOT invoke the chat model for any other private user or group conversation.

#### Scenario: Named test private message in auto mode
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound private message from `2000000003` to bot `2000000002` is processed
- **THEN** the chat model may be called and a sendable outbox item may be created without waiting for per-message approval

#### Scenario: Owner private message in auto mode
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound private message from the owner to the configured bot is processed
- **THEN** the chat model may be called and a sendable outbox item may be created without waiting for per-message approval

#### Scenario: Unnamed private message in auto mode
- **GIVEN** effective mode is `auto`
- **WHEN** an inbound private message from a different user is processed
- **THEN** the chat model is not called and no sendable outbox item is created
