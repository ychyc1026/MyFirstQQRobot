"""Revision-bound retention previews and recoverable managed-artifact quarantine."""

from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import os
import secrets
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from ych_bot.domain.managed_artifacts import (
    ArtifactOwnerScope,
    ArtifactReferenceState,
    ArtifactRetentionState,
    ArtifactVerificationState,
    ManagedArtifact,
    ManagedArtifactType,
    QuarantineBatch,
    QuarantineBatchState,
    QuarantineItem,
    QuarantineItemState,
    RetentionPreview,
    RetentionPreviewState,
    artifact_evidence_revision,
)

from .artifact_inventory import ArtifactInventoryService, ManagedRootRegistry, root_key_for
from .preflight import DatabasePreflightError, DatabasePreflightService


class RetentionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RetentionTypePolicy:
    artifact_type: ManagedArtifactType
    minimum_age_days: int
    keep_latest: int = 0

    def __post_init__(self) -> None:
        if self.minimum_age_days < 0 or self.keep_latest < 0:
            raise ValueError("retention policy values must be non-negative")


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    revision: int = 1
    rules: tuple[RetentionTypePolicy, ...] = (
        RetentionTypePolicy(
            ManagedArtifactType.MIGRATION_BACKUP,
            minimum_age_days=90,
            keep_latest=3,
        ),
    )

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("retention policy revision must be positive")
        types = [item.artifact_type for item in self.rules]
        if len(types) != len(set(types)):
            raise ValueError("retention policy artifact types must be unique")

    def rule_for(self, artifact_type: ManagedArtifactType) -> RetentionTypePolicy | None:
        return next((item for item in self.rules if item.artifact_type is artifact_type), None)


TARGET_TYPES: dict[str, frozenset[ManagedArtifactType]] = {
    "migration_backups": frozenset({ManagedArtifactType.MIGRATION_BACKUP}),
    "privacy_exports": frozenset({ManagedArtifactType.PRIVACY_EXPORT}),
    "privacy_deletion_backups": frozenset({ManagedArtifactType.PRIVACY_DELETION_BACKUP}),
    "imported_sources": frozenset({ManagedArtifactType.IMPORTED_SOURCE}),
}


class RetentionService:
    def __init__(
        self,
        repository: Any,
        inventory: ArtifactInventoryService,
        roots: ManagedRootRegistry,
        *,
        process_instance_id: str,
        policy: RetentionPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        preview_ttl: timedelta = timedelta(minutes=5),
        move_file: Callable[[Path, Path], None] | None = None,
        database_preflight: DatabasePreflightService | None = None,
    ) -> None:
        if not process_instance_id:
            raise ValueError("retention requires a process instance ID")
        if not timedelta(seconds=10) <= preview_ttl <= timedelta(minutes=15):
            raise ValueError("retention preview TTL must be between 10 seconds and 15 minutes")
        self._repository = repository
        self._inventory = inventory
        self._roots = roots
        self._process_instance_id = process_instance_id
        self._policy = policy or RetentionPolicy()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._preview_ttl = preview_ttl
        self._move_file = move_file or _atomic_replace
        self._database_preflight = database_preflight

    @property
    def policy(self) -> RetentionPolicy:
        return self._policy

    async def refresh_classification(self) -> list[ManagedArtifact]:
        await self._inventory.reconcile()
        artifacts = await self._repository.all_managed_artifacts()
        targets = self._classify(artifacts)
        for artifact in artifacts:
            target = targets[artifact.artifact_id]
            if target is artifact.retention_state:
                continue
            updated = await self._repository.set_artifact_retention_state(
                artifact_id=artifact.artifact_id,
                expected_revision=artifact.revision,
                target=target,
                now=self._clock(),
            )
            if updated is None:
                raise RetentionError(
                    "classification_race", "artifact classification changed concurrently"
                )
        current = await self._repository.all_managed_artifacts()
        anomalies = [
            item
            for item in current
            if item.verification_state
            in {
                ArtifactVerificationState.INVALID,
                ArtifactVerificationState.MISSING,
                ArtifactVerificationState.ANOMALOUS,
            }
        ]
        if anomalies:
            anomaly_fingerprint = hashlib.sha256(
                json.dumps(
                    [
                        (item.artifact_id, artifact_evidence_revision(item))
                        for item in sorted(anomalies, key=lambda value: value.artifact_id)
                    ],
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:24]
            await self._audit(
                "retention.inventory_anomaly",
                f"inventory-{anomaly_fingerprint}",
                artifacts=anomalies,
                result="blocked",
                reason="verification_failed",
                correlation_id=f"inventory-{anomaly_fingerprint}",
            )
        return current

    def _classify(self, artifacts: Sequence[ManagedArtifact]) -> dict[str, ArtifactRetentionState]:
        now = self._clock().astimezone(UTC)
        targets: dict[str, ArtifactRetentionState] = {}
        groups: dict[
            tuple[ManagedArtifactType, ArtifactOwnerScope, str | None],
            list[ManagedArtifact],
        ] = {}
        for artifact in artifacts:
            if artifact.retention_state is ArtifactRetentionState.QUARANTINED:
                targets[artifact.artifact_id] = ArtifactRetentionState.QUARANTINED
                continue
            if artifact.verification_state is not ArtifactVerificationState.VERIFIED:
                targets[artifact.artifact_id] = ArtifactRetentionState.BLOCKED
                continue
            groups.setdefault(
                (artifact.artifact_type, artifact.owner_scope, artifact.owner_qq), []
            ).append(artifact)
        for (artifact_type, _scope, _owner), group in groups.items():
            rule = self._policy.rule_for(artifact_type)
            ordered = sorted(
                group,
                key=lambda item: (item.created_at, item.artifact_id),
                reverse=True,
            )
            for index, artifact in enumerate(ordered):
                candidate = (
                    artifact.reference_state is ArtifactReferenceState.UNREFERENCED
                    and rule is not None
                    and index >= rule.keep_latest
                    and artifact.created_at <= now - timedelta(days=rule.minimum_age_days)
                )
                targets[artifact.artifact_id] = (
                    ArtifactRetentionState.CANDIDATE if candidate else ArtifactRetentionState.RETAIN
                )
        return targets

    async def create_preview(
        self,
        *,
        actor_id: str,
        actor_source: str,
        target_batch_type: str,
        owner_scope: ArtifactOwnerScope | None = None,
        owner_qq: str | None = None,
        confirmation_token: str | None = None,
    ) -> dict[str, Any]:
        self._validate_actor(actor_id, actor_source)
        candidates = self._select_candidates(
            await self.refresh_classification(),
            target_batch_type=target_batch_type,
            owner_scope=owner_scope,
            owner_qq=owner_qq,
        )
        if not candidates:
            raise RetentionError("no_candidates", "no verified retention candidates")
        scopes = {(item.owner_scope, item.owner_qq) for item in candidates}
        if len(scopes) != 1:
            raise RetentionError(
                "owner_scope_required", "preview must target exactly one artifact owner scope"
            )
        raw_token = confirmation_token or secrets.token_urlsafe(32)
        if len(raw_token) < 32:
            raise RetentionError("invalid_token", "confirmation token is too short")
        now = self._clock()
        preview = RetentionPreview(
            preview_id=str(uuid4()),
            token_hash=_token_hash(raw_token),
            actor_id=actor_id,
            actor_source=actor_source,
            process_instance_id=self._process_instance_id,
            policy_revision=self._policy.revision,
            candidate_ids=tuple(item.artifact_id for item in candidates),
            evidence_revisions=tuple(
                (item.artifact_id, artifact_evidence_revision(item)) for item in candidates
            ),
            total_bytes=sum(item.size_bytes for item in candidates),
            target_batch_type=target_batch_type,
            expires_at=now + self._preview_ttl,
            correlation_id=str(uuid4()),
        )
        await self._repository.create_retention_preview(preview, now=now)
        await self._audit(
            "retention.preview_created",
            preview.preview_id,
            artifacts=candidates,
            result="ready",
            correlation_id=preview.correlation_id,
            extra={"actor_id": actor_id, "actor_source": actor_source},
        )
        return {
            "preview_id": preview.preview_id,
            "confirmation_token": raw_token,
            "correlation_id": preview.correlation_id,
            "revision": preview.revision,
            "policy_revision": preview.policy_revision,
            "expires_at": preview.expires_at.isoformat(),
            "target_batch_type": target_batch_type,
            "candidate_ids": list(preview.candidate_ids),
            "candidate_count": len(candidates),
            "candidate_bytes": preview.total_bytes,
            "recoverable_quarantine": True,
            "permanent_deletion": False,
        }

    async def confirm(
        self,
        *,
        confirmation_token: str,
        actor_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        token_hash = _token_hash(confirmation_token)
        preview = await self._repository.retention_preview_by_token_hash(token_hash)
        if preview is None:
            await self._audit_rejection("unknown_preview", actor_id)
            raise RetentionError("unknown_preview", "retention preview was not found")
        try:
            self._validate_preview_actor(preview, actor_id, expected_revision)
            if preview.expires_at < self._clock():
                await self._repository.consume_retention_preview(
                    token_hash=token_hash,
                    actor_id=actor_id,
                    process_instance_id=self._process_instance_id,
                    expected_revision=expected_revision,
                    policy_revision=self._policy.revision,
                    now=self._clock(),
                )
                raise RetentionError("preview_expired", "retention preview expired")
            current = await self.refresh_classification()
            by_id = {item.artifact_id: item for item in current}
            preview_artifacts = [by_id[item] for item in preview.candidate_ids if item in by_id]
            if len(preview_artifacts) != len(preview.candidate_ids):
                raise RetentionError("candidate_changed", "retention candidate set changed")
            scope, owner = (
                preview_artifacts[0].owner_scope,
                preview_artifacts[0].owner_qq,
            )
            selected = self._select_candidates(
                current,
                target_batch_type=preview.target_batch_type,
                owner_scope=scope,
                owner_qq=owner,
            )
            expected_evidence = dict(preview.evidence_revisions)
            if tuple(item.artifact_id for item in selected) != preview.candidate_ids or any(
                artifact_evidence_revision(item) != expected_evidence.get(item.artifact_id)
                for item in selected
            ):
                raise RetentionError("candidate_changed", "retention preview is stale")
            consumed = await self._repository.consume_retention_preview(
                token_hash=token_hash,
                actor_id=actor_id,
                process_instance_id=self._process_instance_id,
                expected_revision=expected_revision,
                policy_revision=self._policy.revision,
                now=self._clock(),
            )
            if consumed is None:
                raise RetentionError(
                    "preview_conflict", "retention preview expired, changed or was already used"
                )
            return await self._quarantine(consumed, selected)
        except RetentionError as exc:
            await self._audit_rejection(exc.code, actor_id, preview=preview)
            raise

    async def quarantine_legacy_migration_preview(
        self,
        *,
        cleanup_token: str,
        actor_id: str,
        actor_source: str,
    ) -> dict[str, Any]:
        if self._database_preflight is None:
            raise RetentionError("legacy_unavailable", "migration preview adapter is unavailable")
        try:
            legacy = await self._database_preflight.retention_candidates_for_token(cleanup_token)
        except DatabasePreflightError as exc:
            raise RetentionError("legacy_preview_changed", str(exc)) from exc
        preview = await self.create_preview(
            actor_id=actor_id,
            actor_source=actor_source,
            target_batch_type="migration_backups",
            owner_scope=ArtifactOwnerScope.SYSTEM,
            confirmation_token=cleanup_token,
        )
        expected_names = {str(item["backup_file"]) for item in legacy}
        current = {
            item.artifact_id: item for item in await self._repository.all_managed_artifacts()
        }
        selected_names = {
            Path(current[item].relative_path).name for item in preview["candidate_ids"]
        }
        if selected_names != expected_names:
            raise RetentionError(
                "legacy_preview_changed", "legacy and managed retention previews differ"
            )
        result = await self.confirm(
            confirmation_token=cleanup_token,
            actor_id=actor_id,
            expected_revision=int(preview["revision"]),
        )
        return {
            "status": result["status"],
            "candidate_count": result["candidate_count"],
            "candidate_bytes": result["candidate_bytes"],
            "files": sorted(expected_names),
            "quarantine_id": result["batch_id"],
            "recoverable": True,
        }

    async def _quarantine(
        self, preview: RetentionPreview, artifacts: Sequence[ManagedArtifact]
    ) -> dict[str, Any]:
        batch_id = str(uuid4())
        now = self._clock()
        owner_scope = artifacts[0].owner_scope
        owner_qq = artifacts[0].owner_qq
        items = tuple(
            QuarantineItem(
                artifact_id=artifact.artifact_id,
                sequence=index,
                source_relative_path=artifact.relative_path,
                quarantine_relative_path=self._destination_relative(
                    batch_id, index, Path(artifact.relative_path).name
                ),
                expected_digest_sha256=artifact.digest_sha256,
            )
            for index, artifact in enumerate(artifacts, start=1)
        )
        batch = QuarantineBatch(
            batch_id=batch_id,
            preview_id=preview.preview_id,
            batch_type=preview.target_batch_type,
            owner_scope=owner_scope,
            owner_qq=owner_qq,
            actor_id=preview.actor_id,
            process_instance_id=self._process_instance_id,
            state=QuarantineBatchState.PREPARED,
            revision=1,
            created_at=now,
            updated_at=now,
            correlation_id=preview.correlation_id,
        )
        await self._repository.create_quarantine_batch(batch, items)
        moving = await self._repository.transition_quarantine_batch(
            batch_id=batch_id,
            expected_revision=1,
            target=QuarantineBatchState.MOVING,
            now=self._clock(),
        )
        if moving is None:
            raise RetentionError("batch_conflict", "quarantine batch could not start")
        gc.collect()
        moved: list[tuple[Path, Path]] = []
        try:
            for artifact, item in zip(artifacts, items, strict=True):
                members = await self._bundle_members(artifact, item)
                for source, destination, expected_digest in members:
                    self._assert_same_volume(source, destination)
                    if destination.exists():
                        raise FileExistsError("quarantine destination already exists")
                    if expected_digest and _hash_file(source) != expected_digest:
                        raise RetentionError(
                            "candidate_changed", "artifact changed immediately before move"
                        )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(self._move_file, source, destination)
                    moved.append((source, destination))
                    if source.exists() or not destination.is_file():
                        raise OSError("atomic quarantine move could not be verified")
                    if expected_digest and _hash_file(destination) != expected_digest:
                        raise OSError("quarantine destination integrity mismatch")
                changed = await self._repository.set_quarantine_item_state(
                    batch_id=batch_id,
                    artifact_id=item.artifact_id,
                    expected=QuarantineItemState.PENDING,
                    target=QuarantineItemState.MOVED,
                    now=self._clock(),
                )
                if not changed:
                    raise RetentionError("item_conflict", "quarantine item changed concurrently")
        except Exception as exc:
            return await self._rollback_failed_move(
                batch=moving,
                items=items,
                moved=moved,
                cause=exc,
            )
        completed = await self._repository.finalize_quarantine_batch(
            batch_id=batch_id,
            expected_revision=moving.revision,
            now=self._clock(),
        )
        if completed is None:
            return await self._rollback_failed_move(
                batch=moving,
                items=items,
                moved=moved,
                cause=RetentionError(
                    "finalize_conflict", "quarantine database finalization failed"
                ),
            )
        await self._audit(
            "retention.quarantined",
            batch_id,
            artifacts=artifacts,
            result="quarantined",
            correlation_id=preview.correlation_id,
            extra={"actor_id": preview.actor_id, "actor_source": preview.actor_source},
        )
        return {
            "status": "quarantined",
            "batch_id": batch_id,
            "correlation_id": preview.correlation_id,
            "candidate_count": len(artifacts),
            "candidate_bytes": sum(item.size_bytes for item in artifacts),
            "recoverable": True,
            "permanent_deletion": False,
        }

    async def _rollback_failed_move(
        self,
        *,
        batch: QuarantineBatch,
        items: Sequence[QuarantineItem],
        moved: list[tuple[Path, Path]],
        cause: Exception,
    ) -> dict[str, Any]:
        code = _failure_code(cause)
        rollback_required = await self._repository.transition_quarantine_batch(
            batch_id=batch.batch_id,
            expected_revision=batch.revision,
            target=QuarantineBatchState.ROLLBACK_REQUIRED,
            failure_code=code,
            now=self._clock(),
        )
        if rollback_required is None:
            raise RetentionError("rollback_state_conflict", "rollback could not be started")
        rollback_ok = True
        for source, destination in reversed(moved):
            try:
                if source.exists() or not destination.is_file():
                    rollback_ok = False
                    continue
                await asyncio.to_thread(self._move_file, destination, source)
                rollback_ok = rollback_ok and source.is_file() and not destination.exists()
            except Exception:
                rollback_ok = False
        artifact_rows = {
            item.artifact_id: item
            for item in await self._repository.all_managed_artifacts()
            if item.artifact_id in {value.artifact_id for value in items}
        }
        if rollback_ok:
            try:
                for item in items:
                    artifact = artifact_rows[item.artifact_id]
                    source = self._roots.validate(
                        root_key_for(artifact.artifact_type, artifact.relative_path),
                        artifact.relative_path,
                    )
                    if _hash_file(source) != item.expected_digest_sha256:
                        raise ValueError("restored artifact integrity mismatch")
                    if artifact.artifact_type is ManagedArtifactType.MIGRATION_BACKUP:
                        sidecar = self._roots.validate(
                            "migration_backups",
                            f"storage/backups/migrations/{artifact.bundle_key}.manifest.json",
                        )
                        _verify_migration_pair(source, sidecar, artifact)
            except (KeyError, OSError, ValueError, json.JSONDecodeError):
                rollback_ok = False
        stored_items = {
            item.artifact_id: item
            for item in await self._repository.quarantine_batch_items(batch.batch_id)
        }
        for item in items:
            current = stored_items[item.artifact_id]
            if current.state in {QuarantineItemState.PENDING, QuarantineItemState.MOVED}:
                changed = await self._repository.set_quarantine_item_state(
                    batch_id=batch.batch_id,
                    artifact_id=item.artifact_id,
                    expected=current.state,
                    target=(
                        QuarantineItemState.RESTORED if rollback_ok else QuarantineItemState.BLOCKED
                    ),
                    error_code="" if rollback_ok else code,
                    now=self._clock(),
                )
                rollback_ok = rollback_ok and changed
        target = QuarantineBatchState.ROLLED_BACK if rollback_ok else QuarantineBatchState.BLOCKED
        ended = await self._repository.transition_quarantine_batch(
            batch_id=batch.batch_id,
            expected_revision=rollback_required.revision,
            target=target,
            failure_code=code,
            now=self._clock(),
        )
        await self._audit(
            "retention.rollback",
            batch.batch_id,
            artifacts=list(artifact_rows.values()),
            result=target.value,
            reason=code,
            correlation_id=batch.correlation_id,
        )
        if ended is None or not rollback_ok:
            raise RetentionError(
                "rollback_blocked", "quarantine failed and automatic rollback is incomplete"
            ) from cause
        raise RetentionError(
            "move_rolled_back", "quarantine move failed and was rolled back safely"
        ) from cause

    async def reconcile_interrupted_batches(self) -> dict[str, int]:
        states = (
            QuarantineBatchState.PREPARED,
            QuarantineBatchState.MOVING,
            QuarantineBatchState.ROLLBACK_REQUIRED,
        )
        batches = await self._repository.list_quarantine_batches(states=states)
        result = {"quarantined": 0, "rolled_back": 0, "blocked": 0}
        for batch in batches:
            outcome = await self._reconcile_batch(batch)
            result[outcome] += 1
        return result

    async def _reconcile_batch(self, batch: QuarantineBatch) -> str:
        items = await self._repository.quarantine_batch_items(batch.batch_id)
        artifacts = {
            item.artifact_id: item
            for item in await self._repository.all_managed_artifacts()
            if item.artifact_id in {value.artifact_id for value in items}
        }
        if len(artifacts) != len(items):
            await self._block_batch(batch, "missing_catalog_evidence")
            return "blocked"
        item_locations: list[str] = []
        moved_members: list[tuple[Path, Path]] = []
        for item in items:
            members = await self._bundle_members(
                artifacts[item.artifact_id], item, require_source=False
            )
            states: list[str] = []
            for source, destination, expected_digest in members:
                source_exists = source.is_file()
                destination_exists = destination.is_file()
                if source_exists and destination_exists:
                    states.append("ambiguous")
                elif not source_exists and not destination_exists:
                    states.append("missing")
                elif source_exists:
                    states.append(
                        "source"
                        if not expected_digest or _hash_file(source) == expected_digest
                        else "invalid"
                    )
                else:
                    states.append(
                        "destination"
                        if not expected_digest or _hash_file(destination) == expected_digest
                        else "invalid"
                    )
                    moved_members.append((source, destination))
            if any(value in {"ambiguous", "missing", "invalid"} for value in states):
                await self._block_batch(batch, "ambiguous_filesystem_evidence")
                return "blocked"
            item_locations.append(
                "destination"
                if all(value == "destination" for value in states)
                else "source"
                if all(value == "source" for value in states)
                else "mixed"
            )
        active = batch
        if active.state is QuarantineBatchState.PREPARED:
            started = await self._repository.transition_quarantine_batch(
                batch_id=active.batch_id,
                expected_revision=active.revision,
                target=QuarantineBatchState.MOVING,
                now=self._clock(),
            )
            if started is None:
                await self._block_batch(active, "reconciliation_race")
                return "blocked"
            active = started
        if (
            all(value == "destination" for value in item_locations)
            and active.state is QuarantineBatchState.MOVING
        ):
            try:
                for item in items:
                    artifact = artifacts[item.artifact_id]
                    if artifact.artifact_type is ManagedArtifactType.MIGRATION_BACKUP:
                        members = await self._bundle_members(artifact, item, require_source=False)
                        _verify_migration_pair(members[0][1], members[1][1], artifact)
            except (OSError, ValueError, json.JSONDecodeError):
                await self._block_batch(active, "destination_bundle_invalid")
                return "blocked"
            for item in items:
                if item.state is QuarantineItemState.PENDING:
                    await self._repository.set_quarantine_item_state(
                        batch_id=active.batch_id,
                        artifact_id=item.artifact_id,
                        expected=QuarantineItemState.PENDING,
                        target=QuarantineItemState.MOVED,
                        now=self._clock(),
                    )
            completed = await self._repository.finalize_quarantine_batch(
                batch_id=active.batch_id,
                expected_revision=active.revision,
                now=self._clock(),
            )
            if completed is not None:
                await self._audit(
                    "retention.restart_reconciled",
                    active.batch_id,
                    artifacts=list(artifacts.values()),
                    result="quarantined",
                    correlation_id=active.correlation_id,
                )
                return "quarantined"
        rollback_required = active
        if active.state is QuarantineBatchState.MOVING:
            transitioned = await self._repository.transition_quarantine_batch(
                batch_id=active.batch_id,
                expected_revision=active.revision,
                target=QuarantineBatchState.ROLLBACK_REQUIRED,
                failure_code="restart_reconciliation",
                now=self._clock(),
            )
            if transitioned is None:
                await self._block_batch(active, "reconciliation_race")
                return "blocked"
            rollback_required = transitioned
        rollback_ok = True
        for source, destination in reversed(moved_members):
            try:
                if source.exists() or not destination.is_file():
                    rollback_ok = False
                    continue
                await asyncio.to_thread(self._move_file, destination, source)
                rollback_ok = rollback_ok and source.is_file() and not destination.exists()
            except Exception:
                rollback_ok = False
        if rollback_ok:
            try:
                for item in items:
                    artifact = artifacts[item.artifact_id]
                    if artifact.artifact_type is ManagedArtifactType.MIGRATION_BACKUP:
                        members = await self._bundle_members(artifact, item, require_source=False)
                        _verify_migration_pair(members[0][0], members[1][0], artifact)
            except (OSError, ValueError, json.JSONDecodeError):
                rollback_ok = False
        for item in await self._repository.quarantine_batch_items(active.batch_id):
            if item.state in {QuarantineItemState.PENDING, QuarantineItemState.MOVED}:
                changed = await self._repository.set_quarantine_item_state(
                    batch_id=active.batch_id,
                    artifact_id=item.artifact_id,
                    expected=item.state,
                    target=(
                        QuarantineItemState.RESTORED if rollback_ok else QuarantineItemState.BLOCKED
                    ),
                    error_code="" if rollback_ok else "restart_rollback_failed",
                    now=self._clock(),
                )
                rollback_ok = rollback_ok and changed
        ended = await self._repository.transition_quarantine_batch(
            batch_id=active.batch_id,
            expected_revision=rollback_required.revision,
            target=(
                QuarantineBatchState.ROLLED_BACK if rollback_ok else QuarantineBatchState.BLOCKED
            ),
            failure_code="restart_reconciliation",
            now=self._clock(),
        )
        outcome = "rolled_back" if rollback_ok and ended is not None else "blocked"
        await self._audit(
            "retention.restart_reconciled",
            active.batch_id,
            artifacts=list(artifacts.values()),
            result=outcome,
            correlation_id=active.correlation_id,
        )
        return outcome

    async def _block_batch(self, batch: QuarantineBatch, reason: str) -> None:
        if batch.state in {
            QuarantineBatchState.PREPARED,
            QuarantineBatchState.MOVING,
            QuarantineBatchState.ROLLBACK_REQUIRED,
        }:
            await self._repository.transition_quarantine_batch(
                batch_id=batch.batch_id,
                expected_revision=batch.revision,
                target=QuarantineBatchState.BLOCKED,
                failure_code=reason,
                now=self._clock(),
            )
        await self._audit(
            "retention.restart_reconciled",
            batch.batch_id,
            artifacts=[],
            result="blocked",
            reason=reason,
            correlation_id=batch.correlation_id,
        )

    def _select_candidates(
        self,
        artifacts: Sequence[ManagedArtifact],
        *,
        target_batch_type: str,
        owner_scope: ArtifactOwnerScope | None,
        owner_qq: str | None,
    ) -> list[ManagedArtifact]:
        types = TARGET_TYPES.get(target_batch_type)
        if types is None:
            raise RetentionError("unknown_batch_type", "unknown retention batch type")
        selected = [
            item
            for item in artifacts
            if item.artifact_type in types
            and item.retention_state is ArtifactRetentionState.CANDIDATE
            and (owner_scope is None or item.owner_scope is owner_scope)
            and (owner_qq is None or item.owner_qq == owner_qq)
        ]
        return sorted(selected, key=lambda item: (item.created_at, item.artifact_id))

    async def _bundle_members(
        self,
        artifact: ManagedArtifact,
        item: QuarantineItem,
        *,
        require_source: bool = True,
    ) -> list[tuple[Path, Path, str]]:
        if require_source:
            source = await self._roots.resolve_artifact(self._repository, artifact.artifact_id)
        else:
            source = self._roots.validate(
                root_key_for(artifact.artifact_type, artifact.relative_path),
                artifact.relative_path,
                require_exists=False,
            )
        destination = self._roots.validate(
            "trash", item.quarantine_relative_path, require_exists=False
        )
        members = [(source, destination, artifact.digest_sha256)]
        if artifact.artifact_type is ManagedArtifactType.MIGRATION_BACKUP:
            sidecar_relative = f"storage/backups/migrations/{artifact.bundle_key}.manifest.json"
            sidecar = self._roots.validate(
                "migration_backups",
                sidecar_relative,
                require_exists=require_source,
            )
            sidecar_destination = destination.parent / sidecar.name
            self._roots.validate(
                "trash",
                sidecar_destination.relative_to(self._roots.project_root).as_posix(),
                require_exists=False,
            )
            sidecar_digest = ""
            if require_source:
                _verify_migration_pair(source, sidecar, artifact)
                sidecar_digest = _hash_file(sidecar)
            members.append((sidecar, sidecar_destination, sidecar_digest))
        return members

    @staticmethod
    def _destination_relative(batch_id: str, sequence: int, filename: str) -> str:
        return f"storage/trash/managed-artifacts/{batch_id}/{sequence:04d}/{filename}"

    def _assert_same_volume(self, source: Path, destination: Path) -> None:
        if os.path.normcase(source.drive) != os.path.normcase(destination.drive):
            raise RetentionError("cross_volume_move", "quarantine destination is on another volume")
        if source.stat().st_dev != self._roots.project_root.stat().st_dev:
            raise RetentionError(
                "cross_volume_move", "artifact is not on the managed project volume"
            )

    @staticmethod
    def _validate_actor(actor_id: str, actor_source: str) -> None:
        if not actor_id or actor_source not in {"dashboard", "owner_qq"}:
            raise RetentionError("unauthorized_actor", "retention actor is not authorized")

    def _validate_preview_actor(
        self, preview: RetentionPreview, actor_id: str, expected_revision: int
    ) -> None:
        if preview.state is not RetentionPreviewState.READY:
            raise RetentionError("preview_used", "retention preview is no longer usable")
        if preview.actor_id != actor_id:
            raise RetentionError("unauthorized_actor", "retention actor does not match preview")
        if preview.process_instance_id != self._process_instance_id:
            raise RetentionError("process_changed", "retention preview belongs to another process")
        if preview.policy_revision != self._policy.revision:
            raise RetentionError("policy_changed", "retention policy changed")
        if preview.revision != expected_revision:
            raise RetentionError("preview_conflict", "retention preview revision changed")

    async def _audit_rejection(
        self,
        reason: str,
        actor_id: str,
        *,
        preview: RetentionPreview | None = None,
    ) -> None:
        await self._audit(
            "retention.confirm_rejected",
            preview.preview_id if preview else str(uuid4()),
            artifacts=[],
            result="rejected",
            reason=reason,
            correlation_id=preview.correlation_id if preview else "",
            extra={"actor_id": actor_id},
        )

    async def _audit(
        self,
        action: str,
        subject_id: str,
        *,
        artifacts: Sequence[ManagedArtifact],
        result: str,
        reason: str = "",
        correlation_id: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        scopes = sorted(
            {
                "system"
                if item.owner_scope is ArtifactOwnerScope.SYSTEM
                else f"user:{item.owner_qq}"
                for item in artifacts
            }
        )
        details: dict[str, Any] = {
            "artifact_ids": [item.artifact_id for item in artifacts],
            "artifact_types": sorted({item.artifact_type.value for item in artifacts}),
            "owner_scopes": scopes,
            "count": len(artifacts),
            "bytes": sum(item.size_bytes for item in artifacts),
            "policy_revision": self._policy.revision,
            "process_instance_id": self._process_instance_id,
            "result": result,
            "reason": reason,
            "correlation_id": correlation_id,
            "dashboard_only_report": True,
            "permanent_deletion": False,
        }
        details.update(extra or {})
        await self._repository.record_system_audit(action, subject_id, details)
        if action in {
            "retention.inventory_anomaly",
            "retention.quarantined",
            "retention.rollback",
            "retention.restart_reconciled",
        }:
            # The durable audit remains authoritative; a dashboard projection failure
            # must not turn a completed filesystem rollback into a reported move failure.
            with suppress(Exception):
                await self._repository.record_dashboard_only_owner_report(
                    severity=("warning" if result in {"blocked", "rolled_back"} else "info"),
                    category="managed_artifact_retention",
                    title="Managed artifact retention update",
                    body=(
                        f"result={result}; count={len(artifacts)}; "
                        f"bytes={sum(item.size_bytes for item in artifacts)}; "
                        f"reason={reason or 'none'}"
                    ),
                    related_type="managed_artifact_retention",
                    related_id=subject_id,
                    dedupe_key=(f"retention:{action}:{correlation_id or subject_id}"),
                    now=self._clock(),
                )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_replace(source: Path, destination: Path) -> None:
    for attempt in range(10):
        try:
            source.rename(destination)
            return
        except PermissionError:
            if attempt == 9:
                raise
            gc.collect()
            time.sleep(0.05 * (attempt + 1))


def _verify_migration_pair(backup: Path, manifest: Path, artifact: ManagedArtifact) -> None:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        payload.get("format") != "ych-migration-backup-v1"
        or payload.get("backup_file") != backup.name
        or int(payload.get("byte_count", -1)) != backup.stat().st_size
        or payload.get("sha256") != artifact.digest_sha256
        or _hash_file(backup) != artifact.digest_sha256
    ):
        raise ValueError("migration bundle integrity mismatch")


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "file_locked"
    if isinstance(exc, FileExistsError):
        return "destination_exists"
    if isinstance(exc, RetentionError):
        return exc.code
    return "move_failed"
