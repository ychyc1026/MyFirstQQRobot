# Identity And Privacy

## Purpose

Define user classification, access defaults, privacy jobs, and user-bound data isolation.

## Requirements

### Requirement: Evidence-based relationship classification
The system SHALL distinguish existing friends, new friends, and unresolved identities only from stored evidence, and SHALL NOT infer prior friendship from the first observed message alone.

#### Scenario: Unknown sender first appears
- **WHEN** no reliable friendship evidence exists for a sender
- **THEN** the sender remains unresolved rather than being labeled an old or new friend

### Requirement: History access defaults to deny
The system SHALL deny live historical chat access unless a specific policy and supported adapter explicitly authorize it.

#### Scenario: Existing friend starts a conversation
- **WHEN** an existing friend messages the bot without a history-read grant
- **THEN** prior QQ chat history is not automatically read

### Requirement: User-bound private data
The system SHALL associate private documents, profiles, memories, private conversation data, and per-user personas with an explicit user QQ and SHALL prevent cross-user and group-chat reuse.

#### Scenario: Group message builds context
- **WHEN** a group conversation requests context
- **THEN** private per-user persona and memory material is excluded

### Requirement: Verifiable privacy export and deletion
The system SHALL preview privacy jobs, export structured data, archive tracked original files, verify artifact hashes and sizes, and stop deletion before database mutation if required evidence cannot be secured.

#### Scenario: An imported source file was altered
- **WHEN** a delete job detects a source file whose bytes do not match its recorded hash
- **THEN** the delete job fails before deleting database records

### Requirement: Retention classification for privacy artifacts
Privacy exports, pre-deletion archives, imported original-file archives, and their manifests SHALL have explicit user ownership, artifact type, retention classification, verification state, workflow references, and quarantine state, and SHALL remain isolated from every other user.

#### Scenario: Different user's archive is queried
- **WHEN** a workflow or non-privileged user context attempts to enumerate or act on another user's artifact
- **THEN** access is denied and no metadata or content crosses the user boundary

### Requirement: Privacy artifact cleanup preserves deletion evidence
The system SHALL NOT classify a privacy artifact as a retention candidate while it is required to verify an active export, deletion, appeal, approval, or audit, and any later cleanup SHALL use current integrity verification and recoverable quarantine.

#### Scenario: Deletion backup is still under review
- **WHEN** the privacy job or related audit is unresolved
- **THEN** the archive and its original-file bundle remain protected regardless of age

#### Scenario: Eligible privacy archive is quarantined
- **WHEN** an authorized current preview is confirmed after all references are closed
- **THEN** the bundle moves as one recoverable unit and the user-bound audit evidence remains available without exposing archive contents

### Requirement: Operator labels stay off the model path
Operator labels SHALL NOT be copied into chat, vision, group, persona, memory, or knowledge context, and SHALL NOT be treated as imported profile data or user instructions.

#### Scenario: Labeled user receives an auto reply
- **GIVEN** user `2000000003` has operator label `小明`
- **WHEN** the reply runtime assembles context for that private chat
- **THEN** the assembled sources do not include the operator label

#### Scenario: Label text is not an instruction
- **WHEN** the owner saves a label that contains command-like text
- **THEN** the label is stored as display text only and does not change authorization or reply mode
