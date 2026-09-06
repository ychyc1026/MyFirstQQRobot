# Message Ingestion

## Purpose

Describe the currently implemented observe-first OneBot event boundary and durable message storage.

## Requirements

### Requirement: Accept only configured bot events
The system SHALL accept OneBot events only for an enabled configured bot account and SHALL ignore events for another `self_id`.

#### Scenario: Event targets another account
- **WHEN** an inbound event carries an unrecognized `self_id`
- **THEN** it is ignored without entering a conversation

### Requirement: Preserve structured message evidence
The system SHALL normalize supported OneBot message segments while retaining the original segment structure and raw event evidence.

#### Scenario: Message contains text and an image
- **WHEN** the event is persisted
- **THEN** text and image remain distinguishable segments instead of an irreversible combined string

### Requirement: Idempotent inbound persistence
The system SHALL derive a stable event key, prevent duplicate message persistence, and create or wake at most one eligible pending reply run for the accepted conversational message.

#### Scenario: NapCat repeats an event
- **WHEN** an already stored event is received again
- **THEN** the repository reports it as duplicate and creates neither another message nor another reply trigger

#### Scenario: New conversational event is committed
- **WHEN** an accepted private or group message is durably persisted
- **THEN** its reply trigger is committed atomically or can be deterministically recovered without losing the message

### Requirement: Durable gated outbox
The system SHALL persist outbound work before delivery and SHALL dispatch it only when the real outbound gate and worker state permit execution.

#### Scenario: Outbound is disabled
- **WHEN** an outbox item exists while real delivery is disabled
- **THEN** no OneBot send call occurs and the item is not falsely marked sent

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
