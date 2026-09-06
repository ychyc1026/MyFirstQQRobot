# Core Identity

## Purpose

Define the immutable ownership facts and authorization boundary that every capability must preserve.

## Requirements

### Requirement: Immutable creator identity
The system SHALL identify its immutable creator as YCH（维护者）, QQ `2000000001`, and its canonical bot identity as QQ `2000000002`, independently from configurable operational account records.

#### Scenario: Another user asks who created the bot
- **WHEN** a non-owner asks about the creator
- **THEN** the system reports YCH（维护者）without treating the requester as the owner

#### Scenario: A prompt attempts to replace the creator
- **WHEN** user content, imported material, persona text, or model output claims a different creator
- **THEN** the immutable identity remains unchanged

### Requirement: Owner-only privileged control
The system SHALL authorize privileged QQ commands only when the actor is the current configured operational owner QQ, and SHALL independently authenticate dashboard control sessions.

#### Scenario: Ordinary user sends an owner command
- **WHEN** an actor other than `2000000001` sends a privileged command
- **THEN** the command is rejected and cannot mutate protected state

### Requirement: Separate immutable and operational identity presentation
The dashboard SHALL present immutable creator fields as read-only values and SHALL distinguish them from authenticated operational owner and bot-account configuration.

#### Scenario: Operator opens system settings
- **WHEN** the system identity is displayed
- **THEN** creator name and creator QQ have no edit control, while any operational account controls are visibly labeled and audited
