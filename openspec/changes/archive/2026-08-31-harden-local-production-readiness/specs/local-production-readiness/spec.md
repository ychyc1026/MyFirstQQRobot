## Purpose

定义 Windows 本机运行环境进入普通启动、无网络影子和受控真实效果前必须满足的统一证据、失败关闭与重启恢复行为。

## ADDED Requirements

### Requirement: Profile-specific readiness
The system SHALL evaluate separate `local_start`, `offline_shadow`, and `controlled_real_effect` readiness profiles and SHALL return a structured result containing status, blockers, warnings, evidence timestamps, evidence revisions, and the evaluated bot and process instance.

#### Scenario: Local service is safe but real effects are blocked
- **WHEN** database and local ports permit startup but OneBot or outbound delivery is unavailable
- **THEN** `local_start` may pass while `controlled_real_effect` is blocked with the exact independent reasons

#### Scenario: Unknown evidence is evaluated
- **WHEN** a required probe cannot determine its state
- **THEN** the applicable profile reports `unknown` as a blocker rather than treating it as healthy

### Requirement: Local startup preflight
The `local_start` profile SHALL verify database integrity and migration-backup readiness, required local path confinement, configured listen-address validity, port availability or current-process ownership, administrator-auth configuration, and safe worker startup ceilings before effect-capable workers start.

#### Scenario: Required port belongs to another process
- **WHEN** the configured local API or OneBot-facing port is already owned by an unrelated process
- **THEN** startup is blocked before workers start and the report identifies the port without exposing credentials or unrelated process command lines

#### Scenario: Existing database needs an unverifiable migration backup
- **WHEN** a schema migration is required and the preflight backup cannot be created and reverified
- **THEN** `local_start` fails before repository migration

### Requirement: Offline shadow profile
The `offline_shadow` profile SHALL require an isolated temporary or production database in a non-delivering runtime mode, fake or network-disabled adapters, a non-delivering outbox, and valid context-isolation protections, and SHALL NOT require a live QQ or model-provider network connection.

#### Scenario: Offline shadow is requested
- **WHEN** all offline-shadow gates pass while OneBot and model networking remain disabled
- **THEN** the profile passes without constructing or calling a real external client

### Requirement: Controlled real-effect profile
The `controlled_real_effect` profile SHALL require fresh matching evidence for database preflight, exact bot identity, OneBot authentication and connection, the applicable model or transport gate, persistent pause state, worker lifecycle, outbound or publication gate, activation scope, and current owner authorization.

#### Scenario: Connection belongs to a different bot
- **WHEN** OneBot is connected but its authenticated self QQ does not equal the evaluated bot QQ
- **THEN** the profile fails and no real effect is attempted

#### Scenario: Configuration exists but runtime gate is closed
- **WHEN** a provider credential is configured while its network or effect gate is disabled
- **THEN** the profile reports the configuration and gate as separate states and remains blocked

### Requirement: Freshness and boundary revalidation
Readiness evidence SHALL have an explicit absolute timestamp, source, revision or identity, and profile-specific maximum age; startup snapshots and dashboard reads SHALL NOT authorize later real effects, and critical evidence SHALL be revalidated immediately before each external-effect boundary.

#### Scenario: Computer resumes after sleep
- **WHEN** wall-clock time advances beyond an evidence TTL while the process was suspended
- **THEN** stale connection, port, worker, and provider evidence is rejected until refreshed

#### Scenario: Runtime state changes after dashboard preview
- **WHEN** readiness revision or blocker evidence changes before a protected confirmation is executed
- **THEN** confirmation is rejected and a fresh preview is required

### Requirement: Restart-safe readiness history
The system SHALL distinguish the current process-instance snapshot from historical readiness results, SHALL never reuse a prior instance's passing status as authority, and SHALL retain sanitized failure evidence for audit and diagnosis.

#### Scenario: Application restarts after a passing check
- **WHEN** a new process instance starts
- **THEN** all instance-bound probes return pending or are rerun before an effect-capable profile can pass

### Requirement: Readiness isolation and sanitization
Readiness results SHALL be scoped to an explicit bot and local process instance and SHALL exclude secrets, raw chat or Qzone payloads, private document bodies, reusable confirmation tokens, and arbitrary absolute filesystem paths.

#### Scenario: Operator reads readiness details
- **WHEN** an authenticated dashboard session requests a profile result
- **THEN** it receives stable probe codes and safe summaries without credentials or private content

