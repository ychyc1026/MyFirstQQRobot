## ADDED Requirements

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

