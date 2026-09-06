## MODIFIED Requirements

### Requirement: Observe start script does not enable delivery
The operator start script SHALL start the YCH listener before launching sibling NapCat, SHALL NOT change outbound, reply-worker, Qzone, history, or owner-report flags, and SHALL NOT use owner QQ `2000000001` as a NapCat login target.

#### Scenario: Operator runs observe start while delivery stays off
- **GIVEN** outbound is disabled
- **WHEN** the operator runs `scripts/start-observe.bat` with max mode `observe_only` or `shadow`
- **THEN** YCH is listening before NapCat is launched and delivery flags remain unchanged

#### Scenario: Operator start refuses NapCat when outbound is already on
- **GIVEN** outbound is enabled
- **WHEN** the operator runs the observe start script
- **THEN** the script does not launch NapCat and does not set those flags to enabled

#### Scenario: Operator start refuses NapCat when limited_auto or auto is configured
- **GIVEN** reply runtime max mode is `limited_auto` or `auto`
- **WHEN** the operator runs the observe start script
- **THEN** the script does not launch NapCat and does not set those flags to enabled

## ADDED Requirements

### Requirement: Live shadow does not enable QQ delivery
Raising the reply runtime to `shadow` for owner-private live inference SHALL NOT enable outbound, Qzone publish, Qzone profile collection, history collection, or owner-report delivery.

#### Scenario: Shadow worker runs while outbound stays off
- **WHEN** the reply worker processes an owner-private shadow run
- **THEN** outbound remains disabled and no NapCat send client is required
