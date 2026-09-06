# Owner Automation And Qzone

## Purpose

Define owner-directed control, proactive scheduling, reporting, Qzone profile access, and Qzone publication safeguards.

## Requirements

### Requirement: Wall-clock scheduling
The system SHALL persist absolute scheduled times, timezone, missed-task policy, grace period, and execution state independently of process uptime.

#### Scenario: Computer restarts after a task was due
- **WHEN** a task is discovered after its scheduled time
- **THEN** the configured missed-task policy determines skip, delay, or reapproval rather than process uptime

### Requirement: Owner-governed proactive effects
The system SHALL allow the owner to pause, approve, reject, and inspect proactive messages, reports, and Qzone publication tasks.

#### Scenario: A Qzone post requires approval
- **WHEN** a draft reaches an approval-gated state
- **THEN** it cannot publish before valid owner approval

### Requirement: Qzone observation is attributable
The system SHALL store Qzone-derived user information with source, collection mode, purpose, observation time, and access policy.

#### Scenario: Qzone content suggests a personal fact
- **WHEN** profile extraction observes a post
- **THEN** it is stored as sourced evidence rather than an unquestioned permanent fact

### Requirement: Owner reporting is independently gated
The system SHALL queue operational reports to owner QQ `2000000001` only when reporting and outbound delivery gates allow it.

#### Scenario: Reporting is enabled but outbound is paused
- **WHEN** a report becomes due while real delivery is paused
- **THEN** it remains observable and is not falsely marked delivered
