## REMOVED Requirements

### Requirement: Auto model calls are limited to named conversations

**Reason**: The owner authorized production use for real users, so `auto` is no longer limited to the three named probe conversations, and the scenarios that suppressed unnamed private chats and other groups no longer describe the system.

**Migration**: Replaced by "Auto model calls cover this bot's conversations" in this change. Exact-bot matching and the `@`-gate for groups are preserved; only the three-key allowlist is dropped.

## ADDED Requirements

### Requirement: Auto model calls cover this bot's conversations
When the effective reply mode is `auto`, the system SHALL invoke the chat or vision model for every private conversation of the exact configured bot and for every group conversation of that bot when a trigger mentions the exact bot, and SHALL NOT invoke the model for a conversation key that belongs to a different bot account.

#### Scenario: Any private message in auto mode
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound private message from any user to the configured bot is processed
- **THEN** the chat or vision model may be called and a sendable outbox item may be created without waiting for per-message approval

#### Scenario: Owner private message in auto mode
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound private message from the owner to the configured bot is processed
- **THEN** the chat or vision model may be called and a sendable outbox item may be created without waiting for per-message approval

#### Scenario: Group mention in auto mode
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound group message in any group contains an `at` segment for the configured bot
- **THEN** the chat or vision model may be called and a sendable outbox item may be created for that group without waiting for per-message approval

#### Scenario: Group mention with only an emoji
- **GIVEN** effective mode is `auto` and outbound is enabled
- **WHEN** an inbound group message mentions the bot and the only non-mention content is a `face` or `mface` segment
- **THEN** the chat or vision model may be called with a sanitized emoji placeholder and a sendable outbox item may be created

#### Scenario: Group message without mention in auto mode
- **GIVEN** effective mode is `auto`
- **WHEN** an inbound group message does not mention the bot
- **THEN** the chat or vision model is not called and no sendable outbox item is created

#### Scenario: Different bot conversation in auto mode
- **GIVEN** effective mode is `auto`
- **WHEN** a reply run conversation key belongs to a different bot account
- **THEN** the chat or vision model is not called and no sendable outbox item is created
