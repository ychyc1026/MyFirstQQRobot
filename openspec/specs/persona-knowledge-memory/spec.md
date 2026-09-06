# Persona, Knowledge, And Memory

## Purpose

Define the separation between immutable identity, manual persona definitions, user understanding materials, user-facing personas, and memory records.

## Requirements

### Requirement: Layered persona precedence
The system SHALL keep immutable identity, global persona, developer definition, per-user persona, and transient session context as distinct layers with explicit precedence.

#### Scenario: Imported persona contradicts creator identity
- **WHEN** a per-user persona source contradicts immutable identity
- **THEN** immutable identity wins and the contradiction is not promoted

### Requirement: Separate document purposes
The system SHALL distinguish documents used to understand a user from documents used to define how the bot behaves toward that user.

#### Scenario: User diary is imported for understanding
- **WHEN** a diary is tagged as user-understanding material
- **THEN** its content may inform a user profile but does not directly become the bot's persona

### Requirement: Approval before activation
The system SHALL keep generated knowledge and persona outputs inactive until the configured review and approval state permits use.

#### Scenario: Long-document processing completes
- **WHEN** all chunks are processed but owner approval is absent
- **THEN** the derived profile or persona is not injected into live replies

### Requirement: Typed and attributable memory
The system SHALL store memory with an owner user, kind, structured value, provenance, confidence or review state where applicable, and lifecycle metadata.

#### Scenario: Conflicting facts are detected
- **WHEN** new evidence conflicts with an active memory
- **THEN** the system records a conflict for explicit resolution instead of silently overwriting the fact
