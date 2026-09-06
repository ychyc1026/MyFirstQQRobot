## MODIFIED Requirements

### Requirement: Observe start script does not enable delivery
The operator start script SHALL start the YCH listener before launching sibling NapCat, SHALL NOT change outbound, reply-worker, Qzone, history, or owner-report flags, and SHALL NOT use owner QQ `2000000001` as a NapCat login target.

#### Scenario: Operator runs observe start while delivery stays off
- **GIVEN** outbound is disabled
- **WHEN** the operator runs `scripts/start-observe.bat` with max mode `observe_only` or `shadow`
- **THEN** YCH is listening before NapCat is launched and delivery flags remain unchanged

#### Scenario: Operator start refuses NapCat when outbound is already on
- **GIVEN** outbound is enabled and reply runtime max mode is not `owner_approved`
- **WHEN** the operator runs the observe start script
- **THEN** the script does not launch NapCat and does not set those flags to enabled

#### Scenario: Operator start refuses NapCat when limited_auto or auto is configured
- **GIVEN** reply runtime max mode is `limited_auto` or `auto`
- **WHEN** the operator runs the observe start script
- **THEN** the script does not launch NapCat and does not set those flags to enabled

#### Scenario: Operator runs start while owner_approved outbound is already on
- **GIVEN** reply runtime max mode is `owner_approved` and outbound is enabled
- **WHEN** the operator runs `scripts/start-observe.bat`
- **THEN** YCH is listening before NapCat is launched and those flags remain unchanged

## ADDED Requirements

### Requirement: Live owner-approved send requires explicit owner approval
Raising the reply runtime to `owner_approved` for owner-private live send SHALL create a sendable outbox item only after the authenticated owner approves the exact pending plan, and SHALL NOT enable Qzone publish, Qzone profile collection, history collection, owner-report delivery, `limited_auto`, or `auto`.

#### Scenario: Approval creates one outbox item
- **WHEN** the owner approves a pending owner-private plan while outbound remains enabled
- **THEN** exactly that plan may be handed to the reply outbox and later dispatched

#### Scenario: No approval means no QQ send
- **WHEN** a generated owner-private plan is waiting for approval
- **THEN** no NapCat send is attempted for that run
