## MODIFIED Requirements

### Requirement: Observe start script does not enable delivery
The operator start script SHALL start the YCH listener before launching sibling NapCat, SHALL NOT change outbound, reply-worker, Qzone, history, or owner-report flags, and SHALL NOT use owner QQ `2000000001` as a NapCat login target.

#### Scenario: Operator runs observe start while delivery stays off
- **GIVEN** outbound is disabled
- **WHEN** the operator runs `scripts/start-observe.bat` with max mode `observe_only` or `shadow`
- **THEN** YCH is listening before NapCat is launched and delivery flags remain unchanged

#### Scenario: Operator start refuses NapCat when outbound is already on
- **GIVEN** outbound is enabled and reply runtime max mode is neither `owner_approved` nor `limited_auto`
- **WHEN** the operator runs the observe start script
- **THEN** the script does not launch NapCat and does not set those flags to enabled

#### Scenario: Operator start refuses NapCat when limited_auto or auto is configured
- **GIVEN** reply runtime max mode is `auto`
- **WHEN** the operator runs the observe start script
- **THEN** the script does not launch NapCat and does not set those flags to enabled

#### Scenario: Operator runs start while owner_approved outbound is already on
- **GIVEN** reply runtime max mode is `owner_approved` and outbound is enabled
- **WHEN** the operator runs `scripts/start-observe.bat`
- **THEN** YCH is listening before NapCat is launched and those flags remain unchanged

#### Scenario: Operator runs start while limited_auto outbound is already on
- **GIVEN** reply runtime max mode is `limited_auto` and outbound is enabled
- **WHEN** the operator runs `scripts/start-observe.bat`
- **THEN** YCH is listening before NapCat is launched and those flags remain unchanged

## ADDED Requirements

### Requirement: Live limited-auto send requires explicit eligibility
Raising the reply runtime to `limited_auto` for owner-private live send SHALL create a sendable outbox item only for the exact allowlisted owner private conversation, and SHALL NOT enable Qzone publish, Qzone profile collection, history collection, owner-report delivery, or `auto`.

#### Scenario: Allowlisted owner private message may send
- **WHEN** the owner private conversation is eligible and effective mode is `limited_auto`
- **THEN** exactly that generated plan may be handed to the reply outbox without a new per-message approval

#### Scenario: Missing eligibility means no QQ send
- **WHEN** effective mode is `limited_auto` and the owner private conversation is not eligible
- **THEN** no sendable outbox item is created for that run
