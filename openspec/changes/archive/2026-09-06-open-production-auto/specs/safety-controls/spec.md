## REMOVED Requirements

### Requirement: Live auto send is limited to named conversations

**Reason**: The owner authorized production use for real users, so sendability under `auto` is no longer limited to the owner chat, the named test private chat, and the named group.

**Migration**: Replaced by "Live auto send covers this bot's conversations" in this change. Exact-bot matching and the `@`-gate for groups are preserved; only the three-key allowlist is dropped.

## ADDED Requirements

### Requirement: Live auto send covers this bot's conversations
Raising the reply runtime to `auto` SHALL create a sendable outbox item for every private conversation of the exact configured bot and for every group conversation of that bot when a trigger mentions the exact bot.

#### Scenario: Any private user receives a sendable reply
- **WHEN** effective mode is `auto` and a private sender talks to the configured bot
- **THEN** a sendable private outbox item may be created without per-message approval

#### Scenario: Any group mention receives a sendable reply
- **WHEN** effective mode is `auto` and a message in any group mentions the configured bot
- **THEN** a sendable group outbox item may be created without per-message approval

#### Scenario: Group messages without mention do not send
- **WHEN** effective mode is `auto` and a group message does not mention the configured bot
- **THEN** no sendable outbox item is created

### Requirement: Production keeps search and image generation closed
Opening production `auto` for real users SHALL NOT enable web search, image generation, privacy-job execution, or document OCR.

#### Scenario: Search remains disabled
- **WHEN** production `auto` is active for real users
- **THEN** the web-search adapter is not constructed and search credentials are not required

#### Scenario: Image generation remains disabled
- **WHEN** production `auto` is active for real users
- **THEN** image-generation tasks are not executed
