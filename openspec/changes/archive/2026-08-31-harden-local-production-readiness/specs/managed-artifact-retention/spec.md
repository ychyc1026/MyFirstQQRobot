## Purpose

定义迁移备份、隐私归档、导入原文件和隔离批次的统一受管清单、完整性验证、引用保护与可恢复清理流程。

## ADDED Requirements

### Requirement: Typed managed artifact inventory
The system SHALL inventory managed migration backups, privacy export and deletion archives, imported-source archives, manifests, and recoverable quarantine batches with a stable artifact ID, type, owning user or system scope, relative managed path, byte size, digest, creation time, verification state, reference state, and retention classification.

#### Scenario: Existing files are first discovered
- **WHEN** the inventory scanner finds a supported artifact created before this capability existed
- **THEN** it records metadata by read-only inspection without moving, renaming, deleting, or opening unrelated files

#### Scenario: Private archive is listed
- **WHEN** a user-bound archive is returned to an operator surface
- **THEN** the inventory exposes its owner QQ and safe metadata but not archived message or document contents

### Requirement: Managed-root confinement
The system SHALL resolve every candidate and destination against an explicit project-managed root, reject traversal, symlinks, junctions or reparse points that escape that root, and SHALL NOT accept client-supplied filesystem paths as cleanup authority.

#### Scenario: Candidate resolves outside storage
- **WHEN** a recorded path or Windows reparse point resolves outside the permitted managed root
- **THEN** the artifact is marked anomalous and excluded from every move operation

### Requirement: Current integrity verification
An artifact SHALL be eligible for retention action only after current size, digest, manifest consistency, archive CRC where applicable, and type-specific integrity checks pass; an old verification result alone SHALL NOT be sufficient.

#### Scenario: File changes after inventory
- **WHEN** current bytes no longer match the stored digest or manifest
- **THEN** the artifact is blocked, retained in place, and reported for investigation

### Requirement: Reference-protected retention candidates
Retention classification SHALL preserve artifacts required by an active privacy job, pending approval, unresolved migration, current rollback window, active import task, or existing quarantine investigation, and SHALL evaluate configurable age/count rules only after reference protection.

#### Scenario: Old archive is still referenced
- **WHEN** an artifact is older than its retention threshold but belongs to an active or unresolved workflow
- **THEN** it is not a cleanup candidate

### Requirement: Revision-bound cleanup preview
The system SHALL generate a short-lived cleanup preview bound to the authenticated actor, process instance, policy revision, ordered candidate IDs, current digests, references, total bytes, and intended quarantine destination; previews SHALL be single-use.

#### Scenario: Candidate set changes after preview
- **WHEN** a candidate is added, removed, modified, re-referenced, or reclassified before confirmation
- **THEN** confirmation fails without moving any artifact

#### Scenario: Unauthorized client submits confirmation
- **WHEN** the request lacks a valid administrator session or current-instance owner authorization required by policy
- **THEN** no filesystem or inventory state changes

### Requirement: Atomic recoverable quarantine
Confirmed cleanup SHALL move each artifact together with its manifest and required sidecars into a new project-managed quarantine batch, update inventory only after the move succeeds, and roll back the batch when a partial move fails.

#### Scenario: Sidecar move fails
- **WHEN** one file in a candidate bundle cannot be moved
- **THEN** already moved members are returned to their original managed paths where safe, the batch is marked failed, and no successful cleanup result is reported

#### Scenario: Application restarts during quarantine
- **WHEN** startup finds an interrupted quarantine batch
- **THEN** it reconciles source, destination, and inventory evidence into a recoverable or blocked state without deleting either copy

### Requirement: No automatic permanent destruction
This capability SHALL NOT automatically permanently delete managed artifacts or quarantine batches and SHALL NOT restore a database over the running database through the dashboard.

#### Scenario: Quarantine ages beyond its policy
- **WHEN** a quarantine batch becomes old
- **THEN** it remains visible for a later explicit permanent-destruction policy rather than being silently erased

### Requirement: Audited retention operations
Inventory anomalies, verification failures, preview creation, rejected confirmations, quarantine moves, rollback attempts, and restart reconciliation SHALL produce sanitized audit evidence with actor, source, artifact types, counts, bytes, revision, result, and timestamp.

#### Scenario: Privacy archives are quarantined
- **WHEN** an authenticated operator confirms a valid batch
- **THEN** the audit identifies affected user scopes and artifact IDs without storing archived private content or credentials

