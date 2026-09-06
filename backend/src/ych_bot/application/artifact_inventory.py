"""Read-only discovery and verification of explicitly managed local artifacts."""

from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import os
import sqlite3
import stat
import zipfile
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from ych_bot.domain.managed_artifacts import (
    ArtifactOwnerScope,
    ArtifactReference,
    ArtifactReferenceState,
    ArtifactRetentionState,
    ArtifactVerificationState,
    ManagedArtifact,
    ManagedArtifactType,
)


class PathConfinementError(ValueError):
    """A managed artifact path is outside its server-owned root."""


@dataclass(frozen=True, slots=True)
class ManagedRoot:
    key: str
    relative_path: str


MANAGED_ROOTS = (
    ManagedRoot("migration_backups", "storage/backups/migrations"),
    ManagedRoot("privacy_exports", "storage/exports"),
    ManagedRoot("privacy_backups", "storage/privacy-backups"),
    ManagedRoot("imports", "storage/imports"),
    ManagedRoot("trash", "storage/trash"),
)


class ManagedRootRegistry:
    def __init__(
        self,
        project_root: Path,
        *,
        reparse_detector: Callable[[Path], bool] | None = None,
    ) -> None:
        self.project_root = project_root.resolve(strict=True)
        self._roots = {item.key: self.project_root / item.relative_path for item in MANAGED_ROOTS}
        self._reparse_detector = reparse_detector or _is_reparse_point

    def root(self, key: str) -> Path:
        try:
            return self._roots[key]
        except KeyError as exc:
            raise PathConfinementError("unknown managed root") from exc

    def validate(
        self,
        key: str,
        project_relative_path: str,
        *,
        require_exists: bool = True,
    ) -> Path:
        root = self.root(key)
        pure = PurePosixPath(project_relative_path.replace("\\", "/"))
        if pure.is_absolute() or not pure.parts or ".." in pure.parts or "." in pure.parts:
            raise PathConfinementError("managed path is not a safe project-relative path")
        if ":" in pure.parts[0]:
            raise PathConfinementError("managed path contains a drive prefix")
        candidate = self.project_root.joinpath(*pure.parts)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PathConfinementError("managed path does not belong to its declared root") from exc
        resolved_root = root.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        if not _is_within(self.project_root, resolved_root):
            raise PathConfinementError("managed root resolves outside the project")
        if not _is_within(resolved_root, resolved_candidate):
            raise PathConfinementError("managed path resolves outside its root")
        current = self.project_root
        for part in candidate.relative_to(self.project_root).parts:
            current = current / part
            if not current.exists() and not current.is_symlink():
                break
            try:
                is_reparse = self._reparse_detector(current)
            except OSError as exc:
                raise PathConfinementError("managed link metadata could not be verified") from exc
            if is_reparse and not _is_within(resolved_root, current.resolve(strict=True)):
                raise PathConfinementError("managed reparse point escapes its root")
        if require_exists and not candidate.is_file():
            raise FileNotFoundError(candidate.name)
        return candidate

    async def resolve_artifact(self, repository: Any, artifact_id: str) -> Path:
        artifact = await repository.managed_artifact(artifact_id)
        if artifact is None:
            raise PathConfinementError("managed artifact ID is unknown")
        return self.validate(
            root_key_for(artifact.artifact_type, artifact.relative_path),
            artifact.relative_path,
        )


@dataclass(frozen=True, slots=True)
class ReferenceFact:
    reference_type: str
    reference_key: str
    state: ArtifactReferenceState


@dataclass(frozen=True, slots=True)
class ArtifactObservation:
    artifact_type: ManagedArtifactType
    owner_scope: ArtifactOwnerScope
    owner_qq: str | None
    relative_path: str
    bundle_key: str
    size_bytes: int
    digest_sha256: str
    manifest_type: str
    created_at: datetime
    verification_state: ArtifactVerificationState
    retention_state: ArtifactRetentionState
    references: tuple[ReferenceFact, ...] = ()
    safe_reason: str = ""

    @property
    def artifact_id(self) -> str:
        identity = ":".join(
            (
                self.artifact_type.value,
                self.owner_scope.value,
                self.owner_qq or "system",
                self.relative_path,
            )
        )
        return str(uuid5(NAMESPACE_URL, f"ych-managed-artifact:{identity}"))


class ArtifactAdapter(Protocol):
    artifact_types: frozenset[ManagedArtifactType]

    async def discover(self) -> list[ArtifactObservation]: ...


class MigrationBackupAdapter:
    artifact_types = frozenset({ManagedArtifactType.MIGRATION_BACKUP})

    def __init__(
        self,
        roots: ManagedRootRegistry,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._roots = roots
        self._clock = clock or (lambda: datetime.now(UTC))

    async def discover(self) -> list[ArtifactObservation]:
        return await asyncio.to_thread(self._discover_sync)

    def _discover_sync(self) -> list[ArtifactObservation]:
        root = self._roots.root("migration_backups")
        if not root.is_dir():
            return []
        manifests = sorted(root.glob("*.manifest.json"), reverse=True)
        manifest_backups: set[str] = set()
        cutoff = self._clock().astimezone(UTC) - timedelta(days=90)
        observations: list[ArtifactObservation] = []
        for index, manifest_path in enumerate(manifests):
            relative_manifest = manifest_path.relative_to(self._roots.project_root).as_posix()
            relative_backup = relative_manifest
            created_at = datetime.fromtimestamp(manifest_path.stat().st_mtime, UTC)
            try:
                self._roots.validate("migration_backups", relative_manifest)
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                backup_name = str(payload["backup_file"])
                if Path(backup_name).name != backup_name:
                    raise ValueError("unsafe_backup_name")
                manifest_backups.add(backup_name)
                relative_backup = (
                    (manifest_path.parent / backup_name)
                    .relative_to(self._roots.project_root)
                    .as_posix()
                )
                backup_path = self._roots.validate("migration_backups", relative_backup)
                created_at = _aware_datetime(payload["created_at"])
                byte_count, digest = _hash_file(backup_path)
                if payload.get("format") != "ych-migration-backup-v1":
                    raise ValueError("unsupported_manifest")
                if byte_count != int(payload["byte_count"]) or digest != payload["sha256"]:
                    raise ValueError("hash_or_size_mismatch")
                with closing(
                    sqlite3.connect(backup_path.as_uri() + "?mode=ro", uri=True)
                ) as connection:
                    connection.execute("PRAGMA query_only = ON")
                    check = connection.execute("PRAGMA quick_check").fetchone()
                if check is None or check[0] != "ok":
                    raise ValueError("sqlite_integrity_failed")
                references = (
                    (
                        ReferenceFact(
                            reference_type="migration_rollback_window",
                            reference_key="newest_three",
                            state=ArtifactReferenceState.PROTECTED,
                        ),
                    )
                    if index < 3
                    else ()
                )
                retention = (
                    ArtifactRetentionState.CANDIDATE
                    if index >= 3 and created_at < cutoff
                    else ArtifactRetentionState.RETAIN
                )
                observations.append(
                    ArtifactObservation(
                        artifact_type=ManagedArtifactType.MIGRATION_BACKUP,
                        owner_scope=ArtifactOwnerScope.SYSTEM,
                        owner_qq=None,
                        relative_path=relative_backup,
                        bundle_key=manifest_path.name.removesuffix(".manifest.json"),
                        size_bytes=byte_count,
                        digest_sha256=digest,
                        manifest_type="ych-migration-backup-v1",
                        created_at=created_at,
                        verification_state=ArtifactVerificationState.VERIFIED,
                        retention_state=retention,
                        references=references,
                        safe_reason="verified",
                    )
                )
            except FileNotFoundError as exc:
                observations.append(
                    ArtifactObservation(
                        artifact_type=ManagedArtifactType.MIGRATION_BACKUP,
                        owner_scope=ArtifactOwnerScope.SYSTEM,
                        owner_qq=None,
                        relative_path=relative_backup,
                        bundle_key=manifest_path.name.removesuffix(".manifest.json"),
                        size_bytes=0,
                        digest_sha256="",
                        manifest_type="ych-migration-backup-v1",
                        created_at=created_at,
                        verification_state=ArtifactVerificationState.MISSING,
                        retention_state=ArtifactRetentionState.BLOCKED,
                        safe_reason=_safe_reason(exc),
                    )
                )
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                invalid_path = (
                    self._roots.project_root / Path(relative_backup)
                    if relative_backup != relative_manifest
                    else manifest_path
                )
                size, digest = _safe_hash(invalid_path)
                observations.append(
                    ArtifactObservation(
                        artifact_type=ManagedArtifactType.MIGRATION_BACKUP,
                        owner_scope=ArtifactOwnerScope.SYSTEM,
                        owner_qq=None,
                        relative_path=relative_backup,
                        bundle_key=manifest_path.name.removesuffix(".manifest.json"),
                        size_bytes=size,
                        digest_sha256=digest,
                        manifest_type="invalid-migration-manifest",
                        created_at=datetime.fromtimestamp(manifest_path.stat().st_mtime, UTC),
                        verification_state=ArtifactVerificationState.INVALID,
                        retention_state=ArtifactRetentionState.BLOCKED,
                        safe_reason=_safe_reason(exc),
                    )
                )
        for backup_path in sorted(root.glob("*.sqlite3"), reverse=True):
            if backup_path.name in manifest_backups:
                continue
            relative = backup_path.relative_to(self._roots.project_root).as_posix()
            size, digest = _safe_hash(backup_path)
            observations.append(
                ArtifactObservation(
                    artifact_type=ManagedArtifactType.MIGRATION_BACKUP,
                    owner_scope=ArtifactOwnerScope.SYSTEM,
                    owner_qq=None,
                    relative_path=relative,
                    bundle_key=backup_path.stem,
                    size_bytes=size,
                    digest_sha256=digest,
                    manifest_type="missing-migration-manifest",
                    created_at=datetime.fromtimestamp(backup_path.stat().st_mtime, UTC),
                    verification_state=ArtifactVerificationState.INVALID,
                    retention_state=ArtifactRetentionState.BLOCKED,
                    safe_reason="missing_manifest",
                )
            )
        gc.collect()
        return observations


class PrivacyArtifactAdapter:
    artifact_types = frozenset(
        {ManagedArtifactType.PRIVACY_EXPORT, ManagedArtifactType.PRIVACY_DELETION_BACKUP}
    )

    def __init__(self, roots: ManagedRootRegistry, repository: Any) -> None:
        self._roots = roots
        self._repository = repository

    async def discover(self) -> list[ArtifactObservation]:
        records = (await self._repository.managed_artifact_source_records())["privacy"]
        return await asyncio.to_thread(self._discover_sync, records)

    def _discover_sync(self, records: Sequence[dict[str, Any]]) -> list[ArtifactObservation]:
        observations: list[ArtifactObservation] = []
        for record in records:
            artifact_type_raw = str(record["artifact_type"])
            if artifact_type_raw in {"export", "export_file_archive"}:
                artifact_type = ManagedArtifactType.PRIVACY_EXPORT
                root_key = "privacy_exports"
            elif artifact_type_raw in {"pre_delete_backup", "pre_delete_file_archive"}:
                artifact_type = ManagedArtifactType.PRIVACY_DELETION_BACKUP
                root_key = "privacy_backups"
            else:
                continue
            owner_qq = str(record.get("owner_qq") or "")
            owner_valid = owner_qq.isdigit()
            scope = ArtifactOwnerScope.USER if owner_valid else ArtifactOwnerScope.SYSTEM
            relative = str(record["path"])
            references = (
                (
                    ReferenceFact(
                        reference_type="privacy_job",
                        reference_key=str(record["request_id"]),
                        state=ArtifactReferenceState.PROTECTED,
                    ),
                )
                if str(record.get("request_status")) in {"pending", "running"}
                else ()
            )
            try:
                candidate = self._roots.validate(root_key, relative)
                byte_count, digest = _hash_file(candidate)
                if not owner_valid:
                    raise ValueError("owner_unresolved")
                if byte_count != int(record["byte_count"]) or digest != record["sha256"]:
                    raise ValueError("hash_or_size_mismatch")
                manifest_type = self._verify_privacy_bundle(
                    candidate,
                    request_id=str(record["request_id"]),
                    is_archive=artifact_type_raw.endswith("file_archive"),
                )
                verification = ArtifactVerificationState.VERIFIED
                retention = ArtifactRetentionState.RETAIN
                reason = "verified"
            except PathConfinementError as exc:
                byte_count, digest = 0, ""
                manifest_type = "unsafe-privacy-artifact"
                verification = ArtifactVerificationState.ANOMALOUS
                retention = ArtifactRetentionState.BLOCKED
                reason = _safe_reason(exc)
                relative = _anomaly_relative(root_key, relative)
            except FileNotFoundError as exc:
                byte_count, digest = 0, ""
                manifest_type = "missing-privacy-artifact"
                verification = ArtifactVerificationState.MISSING
                retention = ArtifactRetentionState.BLOCKED
                reason = _safe_reason(exc)
            except (
                OSError,
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
                zipfile.BadZipFile,
            ) as exc:
                candidate = self._roots.validate(root_key, relative, require_exists=False)
                byte_count, digest = _safe_hash(candidate)
                manifest_type = "invalid-privacy-artifact"
                verification = ArtifactVerificationState.INVALID
                retention = ArtifactRetentionState.BLOCKED
                reason = _safe_reason(exc)
            observations.append(
                ArtifactObservation(
                    artifact_type=artifact_type,
                    owner_scope=scope,
                    owner_qq=owner_qq if owner_valid else None,
                    relative_path=relative,
                    bundle_key=str(record["request_id"]),
                    size_bytes=byte_count,
                    digest_sha256=digest,
                    manifest_type=manifest_type,
                    created_at=_aware_datetime(record["created_at"]),
                    verification_state=verification,
                    retention_state=retention,
                    references=references,
                    safe_reason=reason,
                )
            )
        return observations

    @staticmethod
    def _verify_privacy_bundle(candidate: Path, *, request_id: str, is_archive: bool) -> str:
        if not is_archive:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            if payload.get("format") != "ych-privacy-bundle-v1":
                raise ValueError("unsupported_privacy_bundle")
            if payload.get("request_id") != request_id or not isinstance(payload.get("data"), dict):
                raise ValueError("privacy_bundle_identity_mismatch")
            return "ych-privacy-bundle-v1"
        with zipfile.ZipFile(candidate, "r") as archive:
            if archive.testzip() is not None:
                raise ValueError("zip_crc_failed")
            archive_members = archive.namelist()
            for name in archive_members:
                member = PurePosixPath(name)
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError("unsafe_archive_member")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if manifest.get("format") != "ych-private-file-archive-v1":
                raise ValueError("unsupported_file_archive")
            if manifest.get("request_id") != request_id or not isinstance(
                manifest.get("files"), list
            ):
                raise ValueError("file_archive_identity_mismatch")
            expected_members = {"manifest.json"}
            for record in manifest["files"]:
                member = PurePosixPath(str(record.get("archive_path") or ""))
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError("unsafe_archive_member")
                expected_members.add(member.as_posix())
                digest = hashlib.sha256()
                byte_count = 0
                with archive.open(member.as_posix(), "r") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                        byte_count += len(chunk)
                if byte_count != int(record.get("byte_count", -1)):
                    raise ValueError("archive_member_size_mismatch")
                if digest.hexdigest() != record.get("sha256"):
                    raise ValueError("archive_member_hash_mismatch")
            if set(archive_members) != expected_members or len(archive_members) != len(
                expected_members
            ):
                raise ValueError("archive_member_manifest_mismatch")
        return "ych-private-file-archive-v1"


class ImportedSourceAdapter:
    artifact_types = frozenset({ManagedArtifactType.IMPORTED_SOURCE})
    _terminal_jobs = frozenset({"completed", "failed", "rejected", "cancelled"})

    def __init__(self, roots: ManagedRootRegistry, repository: Any) -> None:
        self._roots = roots
        self._repository = repository

    async def discover(self) -> list[ArtifactObservation]:
        records = (await self._repository.managed_artifact_source_records())["imports"]
        return await asyncio.to_thread(self._discover_sync, records)

    def _discover_sync(self, records: Sequence[dict[str, Any]]) -> list[ArtifactObservation]:
        observations: list[ArtifactObservation] = []
        for record in records:
            owner_qq = str(record.get("owner_qq") or "")
            owner_valid = owner_qq.isdigit()
            relative = str(record["storage_path"])
            references: list[ReferenceFact] = []
            job_id = str(record.get("job_id") or "")
            job_status = str(record.get("job_status") or "")
            if job_id and job_status not in self._terminal_jobs:
                references.append(
                    ReferenceFact(
                        reference_type="knowledge_job",
                        reference_key=job_id,
                        state=ArtifactReferenceState.PROTECTED,
                    )
                )
            if job_id and bool(record.get("pending_approval")):
                references.append(
                    ReferenceFact(
                        reference_type="pending_approval",
                        reference_key=job_id,
                        state=ArtifactReferenceState.PROTECTED,
                    )
                )
            try:
                candidate = self._roots.validate("imports", relative)
                byte_count, digest = _hash_file(candidate)
                if not owner_valid:
                    raise ValueError("owner_unresolved")
                if byte_count != int(record["byte_count"]) or digest != record["content_sha256"]:
                    raise ValueError("hash_or_size_mismatch")
                verification = ArtifactVerificationState.VERIFIED
                retention = ArtifactRetentionState.RETAIN
                reason = "verified"
            except PathConfinementError as exc:
                byte_count, digest = 0, ""
                verification = ArtifactVerificationState.ANOMALOUS
                retention = ArtifactRetentionState.BLOCKED
                reason = _safe_reason(exc)
                relative = _anomaly_relative("imports", relative)
            except FileNotFoundError as exc:
                byte_count, digest = 0, ""
                verification = ArtifactVerificationState.MISSING
                retention = ArtifactRetentionState.BLOCKED
                reason = _safe_reason(exc)
            except (OSError, KeyError, TypeError, ValueError) as exc:
                candidate = self._roots.validate("imports", relative, require_exists=False)
                byte_count, digest = _safe_hash(candidate)
                verification = ArtifactVerificationState.INVALID
                retention = ArtifactRetentionState.BLOCKED
                reason = _safe_reason(exc)
            observations.append(
                ArtifactObservation(
                    artifact_type=ManagedArtifactType.IMPORTED_SOURCE,
                    owner_scope=(
                        ArtifactOwnerScope.USER if owner_valid else ArtifactOwnerScope.SYSTEM
                    ),
                    owner_qq=owner_qq if owner_valid else None,
                    relative_path=relative,
                    bundle_key=str(record["document_id"]),
                    size_bytes=byte_count,
                    digest_sha256=digest,
                    manifest_type=f"imported-source:{record.get('detected_format', 'unknown')}",
                    created_at=_aware_datetime(record["created_at"]),
                    verification_state=verification,
                    retention_state=retention,
                    references=tuple(references),
                    safe_reason=reason,
                )
            )
        return observations


class QuarantineInventoryAdapter:
    artifact_types = frozenset({ManagedArtifactType.QUARANTINE_EVIDENCE})

    def __init__(self, roots: ManagedRootRegistry, repository: Any) -> None:
        self._roots = roots
        self._repository = repository

    async def discover(self) -> list[ArtifactObservation]:
        records = (await self._repository.managed_artifact_source_records())["quarantine"]
        return await asyncio.to_thread(self._discover_sync, records)

    def _discover_sync(self, records: Sequence[dict[str, Any]]) -> list[ArtifactObservation]:
        observations = [self._database_observation(record) for record in records]
        root = self._roots.root("trash") / "migration-backups"
        if not root.is_dir():
            return observations
        tracked = {item.relative_path for item in observations}
        for batch_dir in sorted(item for item in root.iterdir() if item.is_dir()):
            for manifest in sorted(batch_dir.glob("*.manifest.json")):
                try:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                    backup_name = str(payload["backup_file"])
                    if payload.get("format") != "ych-migration-backup-v1":
                        continue
                    if Path(backup_name).name != backup_name:
                        continue
                    backup = manifest.parent / backup_name
                    relative = backup.relative_to(self._roots.project_root).as_posix()
                    if relative in tracked:
                        continue
                    candidate = self._roots.validate("trash", relative)
                    size, digest = _hash_file(candidate)
                    verified = size == int(payload["byte_count"]) and digest == payload["sha256"]
                    observations.append(
                        ArtifactObservation(
                            artifact_type=ManagedArtifactType.QUARANTINE_EVIDENCE,
                            owner_scope=ArtifactOwnerScope.SYSTEM,
                            owner_qq=None,
                            relative_path=relative,
                            bundle_key=batch_dir.name,
                            size_bytes=size,
                            digest_sha256=digest,
                            manifest_type="legacy-migration-quarantine-v1",
                            created_at=_aware_datetime(payload["created_at"]),
                            verification_state=(
                                ArtifactVerificationState.VERIFIED
                                if verified
                                else ArtifactVerificationState.INVALID
                            ),
                            retention_state=ArtifactRetentionState.RETAIN,
                            references=(
                                ReferenceFact(
                                    reference_type="quarantine_investigation",
                                    reference_key=batch_dir.name,
                                    state=ArtifactReferenceState.PROTECTED,
                                ),
                            ),
                            safe_reason="verified" if verified else "hash_or_size_mismatch",
                        )
                    )
                except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
        return observations

    def _database_observation(self, record: dict[str, Any]) -> ArtifactObservation:
        relative = str(record["quarantine_relative_path"])
        owner_qq = str(record.get("owner_qq") or "")
        owner_scope = ArtifactOwnerScope(str(record["owner_scope"]))
        owner_valid = owner_scope is ArtifactOwnerScope.SYSTEM or owner_qq.isdigit()
        try:
            candidate = self._roots.validate("trash", relative)
            size, digest = _hash_file(candidate)
            verified = digest == str(record["expected_digest_sha256"])
            verification = (
                ArtifactVerificationState.VERIFIED
                if verified
                else ArtifactVerificationState.INVALID
            )
        except PathConfinementError:
            size, digest = 0, ""
            verification = ArtifactVerificationState.ANOMALOUS
            relative = _anomaly_relative("trash", relative)
        except OSError:
            size, digest = 0, ""
            verification = ArtifactVerificationState.MISSING
        if not owner_valid:
            verification = ArtifactVerificationState.ANOMALOUS
        batch_state = str(record.get("batch_state") or "")
        references = (
            (
                ReferenceFact(
                    reference_type="quarantine_investigation",
                    reference_key=str(record["batch_id"]),
                    state=ArtifactReferenceState.PROTECTED,
                ),
            )
            if batch_state != "rolled_back"
            else ()
        )
        return ArtifactObservation(
            artifact_type=ManagedArtifactType.QUARANTINE_EVIDENCE,
            owner_scope=owner_scope if owner_valid else ArtifactOwnerScope.SYSTEM,
            owner_qq=(owner_qq if owner_valid and owner_scope is ArtifactOwnerScope.USER else None),
            relative_path=relative,
            bundle_key=str(record["batch_id"]),
            size_bytes=size,
            digest_sha256=digest,
            manifest_type="managed-quarantine-v1",
            created_at=_aware_datetime(record["updated_at"]),
            verification_state=verification,
            retention_state=ArtifactRetentionState.RETAIN,
            references=references,
            safe_reason="verified"
            if verification is ArtifactVerificationState.VERIFIED
            else "anomaly",
        )


class ArtifactInventoryService:
    def __init__(
        self,
        repository: Any,
        *,
        adapters: Sequence[ArtifactAdapter],
        clock: Callable[[], datetime] | None = None,
        max_concurrency: int = 4,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("artifact inventory concurrency must be positive")
        self._repository = repository
        self._adapters = tuple(adapters)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_concurrency = max_concurrency

    async def reconcile(self) -> dict[str, Any]:
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def discover(adapter: ArtifactAdapter) -> list[ArtifactObservation]:
            async with semaphore:
                return await adapter.discover()

        discovered = [
            observation
            for batch in await asyncio.gather(*(discover(adapter) for adapter in self._adapters))
            for observation in batch
        ]
        observations = {item.artifact_id: item for item in discovered}
        existing = {
            item.artifact_id: item for item in await self._repository.all_managed_artifacts()
        }
        counts = {"created": 0, "updated": 0, "unchanged": 0, "missing": 0}
        results: list[dict[str, str]] = []
        for artifact_id, observation in observations.items():
            current = existing.get(artifact_id)
            reference_state = (
                ArtifactReferenceState.PROTECTED
                if any(
                    item.state is ArtifactReferenceState.PROTECTED
                    for item in observation.references
                )
                else ArtifactReferenceState.UNREFERENCED
            )
            retention_state = observation.retention_state
            if (
                current is not None
                and current.retention_state is ArtifactRetentionState.QUARANTINED
            ):
                retention_state = ArtifactRetentionState.QUARANTINED
            elif observation.verification_state is not ArtifactVerificationState.VERIFIED:
                retention_state = ArtifactRetentionState.BLOCKED
            elif reference_state is ArtifactReferenceState.PROTECTED:
                retention_state = ArtifactRetentionState.RETAIN
            elif (
                current is not None and current.retention_state is ArtifactRetentionState.CANDIDATE
            ):
                retention_state = ArtifactRetentionState.CANDIDATE
            verification_revision = (
                1
                if current is None
                else current.verification_revision
                + int(
                    (
                        current.size_bytes,
                        current.digest_sha256,
                        current.verification_state,
                    )
                    != (
                        observation.size_bytes,
                        observation.digest_sha256,
                        observation.verification_state,
                    )
                )
            )
            reference_revision = (
                1
                if current is None
                else current.reference_revision
                + int(current.reference_state is not reference_state)
            )
            candidate = ManagedArtifact(
                artifact_id=artifact_id,
                artifact_type=observation.artifact_type,
                owner_scope=observation.owner_scope,
                owner_qq=observation.owner_qq,
                relative_path=observation.relative_path,
                bundle_key=observation.bundle_key,
                size_bytes=observation.size_bytes,
                digest_sha256=observation.digest_sha256,
                manifest_type=observation.manifest_type,
                created_at=observation.created_at,
                verification_state=observation.verification_state,
                verification_revision=verification_revision,
                reference_state=reference_state,
                reference_revision=reference_revision,
                retention_state=retention_state,
                revision=1 if current is None else current.revision,
            )
            if current is None:
                stored = await self._repository.upsert_managed_artifact(
                    candidate, now=self._clock()
                )
                if stored is None:
                    raise RuntimeError("managed artifact insert lost its revision race")
                counts["created"] += 1
            elif _artifact_state(current) != _artifact_state(candidate):
                candidate = replace(candidate, revision=current.revision + 1)
                stored = await self._repository.upsert_managed_artifact(
                    candidate,
                    expected_revision=current.revision,
                    now=self._clock(),
                )
                if stored is None:
                    raise RuntimeError("managed artifact update lost its revision race")
                counts["updated"] += 1
            else:
                stored = current
                counts["unchanged"] += 1
            await self._reconcile_references(stored, observation.references)
            results.append(
                {
                    "artifact_id": artifact_id,
                    "artifact_type": observation.artifact_type.value,
                    "verification_state": observation.verification_state.value,
                    "reference_state": reference_state.value,
                    "retention_state": retention_state.value,
                    "safe_reason": observation.safe_reason,
                }
            )
        supported_types = frozenset(
            item for adapter in self._adapters for item in adapter.artifact_types
        )
        for artifact_id, current in existing.items():
            if artifact_id in observations or current.artifact_type not in supported_types:
                continue
            if current.retention_state is ArtifactRetentionState.QUARANTINED:
                counts["unchanged"] += 1
                continue
            if (
                current.verification_state is ArtifactVerificationState.MISSING
                and current.retention_state is ArtifactRetentionState.BLOCKED
            ):
                counts["unchanged"] += 1
                continue
            missing = replace(
                current,
                verification_state=ArtifactVerificationState.MISSING,
                verification_revision=current.verification_revision + 1,
                retention_state=ArtifactRetentionState.BLOCKED,
                revision=current.revision + 1,
            )
            if (
                await self._repository.upsert_managed_artifact(
                    missing,
                    expected_revision=current.revision,
                    now=self._clock(),
                )
                is not None
            ):
                counts["missing"] += 1
        return {**counts, "total": len(results), "items": results, "filesystem_mutated": False}

    async def _reconcile_references(
        self, artifact: ManagedArtifact, facts: Sequence[ReferenceFact]
    ) -> None:
        existing = {
            (item.reference_type, item.reference_key): item
            for item in await self._repository.artifact_references(artifact.artifact_id)
        }
        observed_keys: set[tuple[str, str]] = set()
        for fact in facts:
            key = (fact.reference_type, fact.reference_key)
            observed_keys.add(key)
            current = existing.get(key)
            reference = ArtifactReference(
                reference_id=(
                    current.reference_id
                    if current
                    else str(
                        uuid5(
                            NAMESPACE_URL,
                            f"ych-artifact-reference:{artifact.artifact_id}:{key[0]}:{key[1]}",
                        )
                    )
                ),
                artifact_id=artifact.artifact_id,
                reference_type=fact.reference_type,
                reference_key=fact.reference_key,
                state=fact.state,
                revision=1 if current is None else current.revision,
                observed_at=self._clock(),
            )
            if current is None:
                await self._repository.upsert_artifact_reference(reference)
            elif current.state is not fact.state:
                await self._repository.upsert_artifact_reference(
                    replace(reference, revision=current.revision + 1),
                    expected_revision=current.revision,
                )
        for key, current in existing.items():
            if key in observed_keys or current.state is ArtifactReferenceState.UNREFERENCED:
                continue
            await self._repository.upsert_artifact_reference(
                replace(
                    current,
                    state=ArtifactReferenceState.UNREFERENCED,
                    revision=current.revision + 1,
                    observed_at=self._clock(),
                ),
                expected_revision=current.revision,
            )


def root_key_for(artifact_type: ManagedArtifactType, relative_path: str) -> str:
    if artifact_type is ManagedArtifactType.MIGRATION_BACKUP:
        return "migration_backups"
    if artifact_type is ManagedArtifactType.PRIVACY_EXPORT:
        return "privacy_exports"
    if artifact_type is ManagedArtifactType.PRIVACY_DELETION_BACKUP:
        return "privacy_backups"
    if artifact_type is ManagedArtifactType.IMPORTED_SOURCE:
        return "imports"
    if artifact_type is ManagedArtifactType.QUARANTINE_EVIDENCE:
        return "trash"
    raise PathConfinementError(f"unsupported artifact type for {relative_path}")


def _artifact_state(value: ManagedArtifact) -> tuple[object, ...]:
    return (
        value.bundle_key,
        value.size_bytes,
        value.digest_sha256,
        value.manifest_type,
        value.verification_state,
        value.verification_revision,
        value.reference_state,
        value.reference_revision,
        value.retention_state,
    )


def _aware_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _hash_file(path: Path) -> tuple[int, str]:
    if not path.is_file():
        raise FileNotFoundError(path.name)
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            byte_count += len(chunk)
    return byte_count, digest.hexdigest()


def _safe_hash(path: Path) -> tuple[int, str]:
    try:
        return _hash_file(path)
    except OSError:
        return 0, ""


def _safe_reason(exc: BaseException) -> str:
    if isinstance(exc, FileNotFoundError):
        return "missing_file"
    if isinstance(exc, PathConfinementError):
        return "path_confinement_failed"
    if isinstance(exc, zipfile.BadZipFile):
        return "invalid_zip"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json"
    if isinstance(exc, ValueError):
        text = str(exc).strip().lower().replace(" ", "_")
        if text and text.replace("_", "").isalnum():
            return text[:80]
        return "invalid_metadata"
    return "io_error" if isinstance(exc, OSError) else "verification_failed"


def _anomaly_relative(root_key: str, untrusted_path: str) -> str:
    root = next(item for item in MANAGED_ROOTS if item.key == root_key)
    identity = hashlib.sha256(untrusted_path.encode("utf-8", errors="replace")).hexdigest()
    return f"{root.relative_path}/.inventory-anomalies/{identity}"


def _is_within(root: Path, candidate: Path) -> bool:
    root_value = os.path.normcase(str(root.resolve(strict=False)))
    candidate_value = os.path.normcase(str(candidate.resolve(strict=False)))
    try:
        return os.path.commonpath((root_value, candidate_value)) == root_value
    except ValueError:
        return False


def _is_reparse_point(path: Path) -> bool:
    details = path.lstat()
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)
