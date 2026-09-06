## ADDED Requirements

### Requirement: Reverse WebSocket requires a configured token
The system SHALL reject the OneBot reverse WebSocket when the configured access token is empty or the presented bearer/query token does not match, and SHALL accept the connection only after that comparison succeeds.

#### Scenario: Token is not configured
- **WHEN** a client connects to the OneBot WebSocket path while `YCH_ONEBOT_ACCESS_TOKEN` is empty
- **THEN** the connection is closed without ingesting events

#### Scenario: Token mismatches
- **WHEN** a client presents a different access token
- **THEN** the connection is closed without ingesting events

### Requirement: Lifecycle frames identify the connected bot
The system SHALL use OneBot `meta_event` frames to record the claimed `self_id` as the authenticated bot QQ and SHALL NOT count heartbeat or lifecycle frames as ignored conversational events.

#### Scenario: Heartbeat arrives from the configured bot
- **WHEN** a `meta_event` heartbeat carries `self_id` equal to the configured bot QQ
- **THEN** connection status reports connected with a matching authenticated bot QQ and ignored conversational count is unchanged

### Requirement: Observe ingest has no delivery or model side effects
The system SHALL persist accepted exact-bot inbound messages without constructing a NapCat HTTP client, without invoking chat, vision, image, or stats gateways, and without creating a deliverable outbox item while outbound and the reply worker remain disabled.

#### Scenario: Private message is stored in observe mode
- **WHEN** an authenticated reverse WebSocket delivers a private message for bot `2000000002` while outbound and the reply worker are disabled
- **THEN** the message is stored, no NapCat HTTP adapter is constructed, and outbox remains empty of sendable reply items
