## MODIFIED Requirements

### Requirement: Auto model calls are limited to named conversations
When the effective reply mode is `auto`, the system SHALL invoke the chat model only for `{bot_qq}:private:{owner_qq}`, `{bot_qq}:private:2000000003`, and `{bot_qq}:group:2000000004` when a trigger mentions the exact bot, and SHALL NOT invoke the chat model for any other private user or group conversation.

#### Scenario: Named test private message in auto mode
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound private message from `2000000003` to bot `2000000002` is processed
- **THEN** the chat model may be called and a sendable outbox item may be created without waiting for per-message approval

#### Scenario: Owner private message in auto mode
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound private message from the owner to the configured bot is processed
- **THEN** the chat model may be called and a sendable outbox item may be created without waiting for per-message approval

#### Scenario: Named group mention in auto mode
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound group message in `2000000004` contains an `at` segment for bot `2000000002`
- **THEN** the chat model may be called and a sendable outbox item may be created for that group without waiting for per-message approval

#### Scenario: Named group mention with only an emoji
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound group message in `2000000004` mentions the bot and the only non-mention content is a `face` or `mface` segment
- **THEN** the chat model may be called with a sanitized emoji placeholder and a sendable outbox item may be created

#### Scenario: Named group message without mention in auto mode
- **GIVEN** effective mode is `auto`
- **WHEN** an inbound group message in `2000000004` does not mention the bot
- **THEN** the chat model is not called and no sendable outbox item is created

#### Scenario: Unnamed private message in auto mode
- **GIVEN** effective mode is `auto`
- **WHEN** an inbound private message from a different user is processed
- **THEN** the chat model is not called and no sendable outbox item is created

#### Scenario: Other group mention in auto mode
- **GIVEN** effective mode is `auto`
- **WHEN** an inbound group message in a different group mentions the bot
- **THEN** the chat model is not called and no sendable outbox item is created
