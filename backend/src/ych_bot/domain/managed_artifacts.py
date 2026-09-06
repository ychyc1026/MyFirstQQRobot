"""Typed contracts for managed artifacts and recoverable retention."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .readiness import require_aware_utc


class ManagedArtifactType(StrEnum):
    MIGRATION_BACKUP = "migration_backup"
    PRIVACY_EXPORT = "privacy_export"
    PRIVACY_DELETION_BACKUP = "privacy_deletion_backup"
    IMPORTED_SOURCE = "imported_source"
    QUARANTINE_EVIDENCE = "quarantine_evidence"


class ArtifactOwnerScope(StrEnum):
    SYSTEM = "system"
    USER = "user"


class ArtifactVerificationState(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    INVALID = "invalid"
    MISSING = "missing"
    ANOMALOUS = "anomalous"


class ArtifactReferenceState(StrEnum):
    UNKNOWN = "unknown"
    UNREFERENCED = "unreferenced"
    PROTECTED = "protected"


class ArtifactRetentionState(StrEnum):
    RETAIN = "retain"
    CANDIDATE = "candidate"
    QUARANTINED = "quarantined"
    BLOCKED = "blocked"


class RetentionPreviewState(StrEnum):
    READY = "ready"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REJECTED = "rejected"


class QuarantineBatchState(StrEnum):
    PREPARED = "prepared"
    MOVING = "moving"
    QUARANTINED = "quarantined"
    ROLLBACK_REQUIRED = "rollback_required"
    ROLLED_BACK = "rolled_back"
    BLOCKED = "blocked"


class QuarantineItemState(StrEnum):
    PENDING = "pending"
    MOVED = "moved"
    RESTORED = "restored"
    BLOCKED = "blocked"


QUARANTINE_TRANSITIONS: dict[QuarantineBatchState, frozenset[QuarantineBatchState]] = {
    QuarantineBatchState.PREPARED: frozenset(
        {QuarantineBatchState.MOVING, QuarantineBatchState.BLOCKED}
    ),
    QuarantineBatchState.MOVING: frozenset(
        {
            QuarantineBatchState.QUARANTINED,
            QuarantineBatchState.ROLLBACK_REQUIRED,
            QuarantineBatchState.BLOCKED,
        }
    ),
    QuarantineBatchState.ROLLBACK_REQUIRED: frozenset(
        {QuarantineBatchState.ROLLED_BACK, QuarantineBatchState.BLOCKED}
    ),
    QuarantineBatchState.QUARANTINED: frozenset(),
    QuarantineBatchState.ROLLED_BACK: frozenset(),
    QuarantineBatchState.BLOCKED: frozenset(),
}


def can_transition_quarantine_batch(
    current: QuarantineBatchState, target: QuarantineBatchState
) -> bool:
    return target in QUARANTINE_TRANSITIONS[current]


@dataclass(frozen=True, slots=True)
class ManagedArtifact:
    artifact_id: str
    artifact_type: ManagedArtifactType
    owner_scope: ArtifactOwnerScope
    owner_qq: str | None
    relative_path: str
    bundle_key: str
    size_bytes: int
    digest_sha256: str
    manifest_type: str
    created_at: datetime
    verification_state: ArtifactVerificationState = ArtifactVerificationState.PENDING
    verification_revision: int = 1
    reference_state: ArtifactReferenceState = ArtifactReferenceState.UNKNOWN
    reference_revision: int = 1
    retention_state: ArtifactRetentionState = ArtifactRetentionState.RETAIN
    revision: int = 1

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.relative_path or not self.bundle_key:
            raise ValueError("artifact id, relative path and bundle key are required")
        normalized_path = self.relative_path.replace("\\", "/")
        if (
            normalized_path.startswith("/")
            or ":" in normalized_path.split("/", 1)[0]
            or ".." in normalized_path.split("/")
        ):
            raise ValueError("relative_path must stay inside a managed root")
        if self.owner_scope is ArtifactOwnerScope.USER:
            if self.owner_qq is None or not self.owner_qq.isdigit():
                raise ValueError("user-owned artifacts require a numeric owner QQ")
        elif self.owner_qq is not None:
            raise ValueError("system-owned artifacts cannot have an owner QQ")
        if (
            self.size_bytes < 0
            or min(self.verification_revision, self.reference_revision, self.revision) < 1
        ):
            raise ValueError("sizes must be non-negative and revisions positive")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at, "created_at"))


def artifact_evidence_revision(artifact: ManagedArtifact) -> str:
    """Bind a retention decision to current bytes, references and catalog revision."""
    return ":".join(
        (
            artifact.digest_sha256,
            str(artifact.verification_revision),
            str(artifact.reference_revision),
            artifact.verification_state.value,
            artifact.reference_state.value,
            artifact.retention_state.value,
            str(artifact.revision),
        )
    )


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    reference_id: str
    artifact_id: str
    reference_type: str
    reference_key: str
    state: ArtifactReferenceState
    revision: int
    observed_at: datetime

    def __post_init__(self) -> None:
        if not all((self.reference_id, self.artifact_id, self.reference_type, self.reference_key)):
            raise ValueError("reference identity fields are required")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        object.__setattr__(self, "observed_at", require_aware_utc(self.observed_at, "observed_at"))


@dataclass(frozen=True, slots=True)
class RetentionPreview:
    preview_id: str
    token_hash: str
    actor_id: str
    actor_source: str
    process_instance_id: str
    policy_revision: int
    candidate_ids: tuple[str, ...]
    evidence_revisions: tuple[tuple[str, str], ...]
    total_bytes: int
    target_batch_type: str
    expires_at: datetime
    state: RetentionPreviewState = RetentionPreviewState.READY
    revision: int = 1
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if not all(
            (
                self.preview_id,
                self.token_hash,
                self.actor_id,
                self.actor_source,
                self.process_instance_id,
                self.target_batch_type,
            )
        ):
            raise ValueError("preview identity and binding fields are required")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("candidate IDs must be unique and ordered")
        if self.policy_revision < 1 or self.revision < 1 or self.total_bytes < 0:
            raise ValueError("preview revisions must be positive and bytes non-negative")
        object.__setattr__(self, "expires_at", require_aware_utc(self.expires_at, "expires_at"))


@dataclass(frozen=True, slots=True)
class QuarantineBatch:
    batch_id: str
    preview_id: str
    batch_type: str
    owner_scope: ArtifactOwnerScope
    owner_qq: str | None
    actor_id: str
    process_instance_id: str
    state: QuarantineBatchState
    revision: int
    created_at: datetime
    updated_at: datetime
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if not all(
            (
                self.batch_id,
                self.preview_id,
                self.batch_type,
                self.actor_id,
                self.process_instance_id,
            )
        ):
            raise ValueError("batch identity and binding fields are required")
        if self.owner_scope is ArtifactOwnerScope.USER:
            if self.owner_qq is None or not self.owner_qq.isdigit():
                raise ValueError("user-owned batches require a numeric owner QQ")
        elif self.owner_qq is not None:
            raise ValueError("system-owned batches cannot have an owner QQ")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        created = require_aware_utc(self.created_at, "created_at")
        updated = require_aware_utc(self.updated_at, "updated_at")
        if updated < created:
            raise ValueError("updated_at must not precede created_at")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)


@dataclass(frozen=True, slots=True)
class QuarantineItem:
    artifact_id: str
    sequence: int
    source_relative_path: str
    quarantine_relative_path: str
    expected_digest_sha256: str
    state: QuarantineItemState = QuarantineItemState.PENDING
    error_code: str = ""

    def __post_init__(self) -> None:
        if (
            not self.artifact_id
            or not self.source_relative_path
            or not self.quarantine_relative_path
        ):
            raise ValueError("quarantine item identity and relative paths are required")
        for path in (self.source_relative_path, self.quarantine_relative_path):
            normalized_path = path.replace("\\", "/")
            if (
                normalized_path.startswith("/")
                or ":" in normalized_path.split("/", 1)[0]
                or ".." in normalized_path.split("/")
            ):
                raise ValueError("quarantine item paths must stay inside managed roots")
        if self.sequence < 1:
            raise ValueError("sequence must be positive")
