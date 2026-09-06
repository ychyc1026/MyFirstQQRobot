## ADDED Requirements

### Requirement: Observe inbound is independent of outbound
The system SHALL treat reverse-WebSocket ingest as an independent real effect from QQ delivery and SHALL NOT enable outbound, the reply worker, model-network production replies, Qzone, history collection, or owner-report delivery when observe ingest is connected.

#### Scenario: Observe connection is live
- **WHEN** NapCat is connected and inbound events are being stored
- **THEN** outbound, reply-worker, Qzone, history, and owner-report gates remain in their prior disabled state

### Requirement: Observe start script does not enable delivery
The operator start script SHALL start the YCH listener before launching sibling NapCat, SHALL NOT change outbound, reply-worker, Qzone, history, or owner-report flags, and SHALL NOT use owner QQ `2000000001` as a NapCat login target.

#### Scenario: Operator runs observe start while delivery stays off
- **GIVEN** outbound and the reply worker are disabled
- **WHEN** the operator runs `scripts/start-observe.bat`
- **THEN** YCH is listening before NapCat is launched and delivery flags remain unchanged

#### Scenario: Operator start refuses NapCat when outbound is already on
- **GIVEN** outbound or the reply worker is enabled
- **WHEN** the operator runs the observe start script
- **THEN** the script does not launch NapCat and does not set those flags to enabled
