"""Persistence for readiness evidence and recoverable managed-artifact retention."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ych_bot.domain.managed_artifacts import (
    ArtifactOwnerScope,
    ArtifactReference,
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
    can_transition_quarantine_batch,
)
from ych_bot.domain.readiness import (
    CapabilityScope,
    EvidenceFreshness,
    ProbeStatus,
    ReadinessBlocker,
    ReadinessDecision,
    ReadinessDecisionStatus,
    ReadinessProbeResult,
    ReadinessProfile,
    ReadinessReasonCode,
    require_aware_utc,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _iso(value: datetime, field_name: str) -> str:
    return require_aware_utc(value, field_name).isoformat()


def _utc_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    return require_aware_utc(current, "now")


def _blocker_payload(items: tuple[ReadinessBlocker, ...]) -> list[dict[str, str]]:
    return [
        {
            "code": item.code.value,
            "probe_code": item.probe_code,
            "capability_scope": item.capability_scope.value,
            "safe_detail": item.safe_detail,
        }
        for item in items
    ]


def _blockers(raw: str) -> tuple[ReadinessBlocker, ...]:
    return tuple(
        ReadinessBlocker(
            code=ReadinessReasonCode(item["code"]),
            probe_code=str(item["probe_code"]),
            capability_scope=CapabilityScope(item["capability_scope"]),
            safe_detail=str(item.get("safe_detail", "")),
        )
        for item in json.loads(raw)
    )


def _probe(row: sqlite3.Row) -> ReadinessProbeResult:
    evidence = json.loads(row["evidence_json"])
    return ReadinessProbeResult(
        probe_code=row["probe_code"],
        status=ProbeStatus(row["status"]),
        capability_scope=CapabilityScope(row["capability_scope"]),
        freshness=EvidenceFreshness(
            observed_at=datetime.fromisoformat(row["observed_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
        ),
        source=row["source"],
        source_revision=row["source_revision"],
        safe_detail=row["safe_detail"],
        remediation_code=row["remediation_code"],
        evidence=tuple(sorted((str(key), str(value)) for key, value in evidence.items())),
    )


def _decision(row: sqlite3.Row, probes: tuple[ReadinessProbeResult, ...]) -> ReadinessDecision:
    return ReadinessDecision(
        decision_id=row["id"],
        bot_qq=row["bot_qq"],
        process_instance_id=row["process_instance_id"],
        profile=ReadinessProfile(row["profile"]),
        capability_scope=CapabilityScope(row["capability_scope"]),
        scope_hash=row["scope_hash"],
        revision=int(row["revision"]),
        status=ReadinessDecisionStatus(row["status"]),
        evaluated_at=datetime.fromisoformat(row["evaluated_at"]),
        blockers=_blockers(row["blockers_json"]),
        warnings=_blockers(row["warnings_json"]),
        probes=probes,
        correlation_id=row["correlation_id"],
    )


def _artifact(row: sqlite3.Row) -> ManagedArtifact:
    return ManagedArtifact(
        artifact_id=row["id"],
        artifact_type=ManagedArtifactType(row["artifact_type"]),
        owner_scope=ArtifactOwnerScope(row["owner_scope"]),
        owner_qq=row["owner_qq"],
        relative_path=row["relative_path"],
        bundle_key=row["bundle_key"],
        size_bytes=int(row["size_bytes"]),
        digest_sha256=row["digest_sha256"],
        manifest_type=row["manifest_type"],
        created_at=datetime.fromisoformat(row["created_at"]),
        verification_state=ArtifactVerificationState(row["verification_state"]),
        verification_revision=int(row["verification_revision"]),
        reference_state=ArtifactReferenceState(row["reference_state"]),
        reference_revision=int(row["reference_revision"]),
        retention_state=ArtifactRetentionState(row["retention_state"]),
        revision=int(row["revision"]),
    )


def _reference(row: sqlite3.Row) -> ArtifactReference:
    return ArtifactReference(
        reference_id=row["id"],
        artifact_id=row["artifact_id"],
        reference_type=row["reference_type"],
        reference_key=row["reference_key"],
        state=ArtifactReferenceState(row["state"]),
        revision=int(row["revision"]),
        observed_at=datetime.fromisoformat(row["observed_at"]),
    )


def _preview(row: sqlite3.Row) -> RetentionPreview:
    revisions = json.loads(row["evidence_revisions_json"])
    return RetentionPreview(
        preview_id=row["id"],
        token_hash=row["token_hash"],
        actor_id=row["actor_id"],
        actor_source=row["actor_source"],
        process_instance_id=row["process_instance_id"],
        policy_revision=int(row["policy_revision"]),
        candidate_ids=tuple(json.loads(row["candidate_ids_json"])),
        evidence_revisions=tuple((str(item[0]), str(item[1])) for item in revisions),
        total_bytes=int(row["total_bytes"]),
        target_batch_type=row["target_batch_type"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
        state=RetentionPreviewState(row["state"]),
        revision=int(row["revision"]),
        correlation_id=row["correlation_id"],
    )


def _batch(row: sqlite3.Row) -> QuarantineBatch:
    return QuarantineBatch(
        batch_id=row["id"],
        preview_id=row["preview_id"],
        batch_type=row["batch_type"],
        owner_scope=ArtifactOwnerScope(row["owner_scope"]),
        owner_qq=row["owner_qq"],
        actor_id=row["actor_id"],
        process_instance_id=row["process_instance_id"],
        state=QuarantineBatchState(row["state"]),
        revision=int(row["revision"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        correlation_id=row["correlation_id"],
    )


class OperationalReadinessRepositoryMixin:
    def _connect(self) -> sqlite3.Connection:  # pragma: no cover
        raise NotImplementedError

    async def create_readiness_decision(self, decision: ReadinessDecision) -> None:
        await asyncio.to_thread(self._create_readiness_decision_sync, decision)

    def _create_readiness_decision_sync(self, decision: ReadinessDecision) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO readiness_decisions(
                    id, bot_qq, process_instance_id, profile, capability_scope, scope_hash,
                    revision, status, blockers_json, warnings_json, correlation_id, evaluated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.bot_qq,
                    decision.process_instance_id,
                    decision.profile.value,
                    decision.capability_scope.value,
                    decision.scope_hash,
                    decision.revision,
                    decision.status.value,
                    _json(_blocker_payload(decision.blockers)),
                    _json(_blocker_payload(decision.warnings)),
                    decision.correlation_id,
                    _iso(decision.evaluated_at, "evaluated_at"),
                ),
            )
            for result in decision.probes:
                connection.execute(
                    """
                    INSERT INTO readiness_probe_evidence(
                        id, decision_id, probe_code, status, capability_scope,
                        observed_at, expires_at, source, source_revision,
                        safe_detail, remediation_code, evidence_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        decision.decision_id,
                        result.probe_code,
                        result.status.value,
                        result.capability_scope.value,
                        _iso(result.freshness.observed_at, "observed_at"),
                        _iso(result.freshness.expires_at, "expires_at"),
                        result.source,
                        result.source_revision,
                        result.safe_detail,
                        result.remediation_code,
                        _json(dict(result.evidence)),
                    ),
                )

    async def readiness_decision(self, decision_id: str) -> ReadinessDecision | None:
        return await asyncio.to_thread(self._readiness_decision_sync, decision_id)

    def _readiness_decision_sync(self, decision_id: str) -> ReadinessDecision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM readiness_decisions WHERE id = ?", (decision_id,)
            ).fetchone()
            if row is None:
                return None
            probes = tuple(
                _probe(item)
                for item in connection.execute(
                    """
                    SELECT * FROM readiness_probe_evidence
                    WHERE decision_id = ? ORDER BY probe_code, capability_scope
                    """,
                    (decision_id,),
                ).fetchall()
            )
        return _decision(row, probes)

    async def list_readiness_decisions(
        self,
        *,
        bot_qq: str,
        process_instance_id: str | None = None,
        profile: ReadinessProfile | None = None,
        capability_scope: CapabilityScope | None = None,
        limit: int = 50,
    ) -> list[ReadinessDecision]:
        return await asyncio.to_thread(
            self._list_readiness_decisions_sync,
            bot_qq,
            process_instance_id,
            profile,
            capability_scope,
            limit,
        )

    def _list_readiness_decisions_sync(
        self,
        bot_qq: str,
        process_instance_id: str | None,
        profile: ReadinessProfile | None,
        capability_scope: CapabilityScope | None,
        limit: int,
    ) -> list[ReadinessDecision]:
        if not bot_qq.isdigit():
            raise ValueError("bot_qq must contain digits only")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        clauses = ["bot_qq = ?"]
        params: list[Any] = [bot_qq]
        if process_instance_id is not None:
            clauses.append("process_instance_id = ?")
            params.append(process_instance_id)
        if profile is not None:
            clauses.append("profile = ?")
            params.append(profile.value)
        if capability_scope is not None:
            clauses.append("capability_scope = ?")
            params.append(capability_scope.value)
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id FROM readiness_decisions WHERE {" AND ".join(clauses)}
                ORDER BY evaluated_at DESC, revision DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._readiness_decision_sync(str(row["id"]))) is not None
        ]

    async def upsert_managed_artifact(
        self,
        artifact: ManagedArtifact,
        *,
        expected_revision: int | None = None,
        now: datetime | None = None,
    ) -> ManagedArtifact | None:
        return await asyncio.to_thread(
            self._upsert_managed_artifact_sync,
            artifact,
            expected_revision,
            _utc_now(now),
        )

    def _upsert_managed_artifact_sync(
        self,
        artifact: ManagedArtifact,
        expected_revision: int | None,
        now: datetime,
    ) -> ManagedArtifact | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM managed_artifacts
                WHERE artifact_type = ? AND owner_scope = ? AND owner_qq IS ?
                  AND relative_path = ?
                """,
                (
                    artifact.artifact_type.value,
                    artifact.owner_scope.value,
                    artifact.owner_qq,
                    artifact.relative_path,
                ),
            ).fetchone()
            values = (
                artifact.bundle_key,
                artifact.size_bytes,
                artifact.digest_sha256,
                artifact.manifest_type,
                artifact.verification_state.value,
                artifact.verification_revision,
                artifact.reference_state.value,
                artifact.reference_revision,
                artifact.retention_state.value,
            )
            if existing is None:
                if expected_revision is not None or artifact.revision != 1:
                    connection.rollback()
                    return None
                connection.execute(
                    """
                    INSERT INTO managed_artifacts(
                        id, artifact_type, owner_scope, owner_qq, relative_path, bundle_key,
                        size_bytes, digest_sha256, manifest_type, created_at,
                        verification_state, verification_revision, reference_state,
                        reference_revision, retention_state, revision, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        artifact.artifact_id,
                        artifact.artifact_type.value,
                        artifact.owner_scope.value,
                        artifact.owner_qq,
                        artifact.relative_path,
                        *values[:4],
                        _iso(artifact.created_at, "created_at"),
                        *values[4:],
                        _iso(now, "now"),
                    ),
                )
            else:
                if (
                    expected_revision is None
                    or int(existing["revision"]) != expected_revision
                    or artifact.revision != expected_revision + 1
                    or artifact.artifact_id != existing["id"]
                ):
                    connection.rollback()
                    return None
                cursor = connection.execute(
                    """
                    UPDATE managed_artifacts SET
                        bundle_key = ?, size_bytes = ?, digest_sha256 = ?, manifest_type = ?,
                        verification_state = ?, verification_revision = ?, reference_state = ?,
                        reference_revision = ?, retention_state = ?, revision = ?, updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (
                        *values,
                        artifact.revision,
                        _iso(now, "now"),
                        artifact.artifact_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return None
            row = connection.execute(
                "SELECT * FROM managed_artifacts WHERE id = ?", (artifact.artifact_id,)
            ).fetchone()
        return _artifact(row)

    async def list_managed_artifacts(
        self,
        *,
        owner_scope: ArtifactOwnerScope,
        owner_qq: str | None,
        artifact_type: ManagedArtifactType | None = None,
        limit: int = 500,
    ) -> list[ManagedArtifact]:
        return await asyncio.to_thread(
            self._list_managed_artifacts_sync, owner_scope, owner_qq, artifact_type, limit
        )

    def _list_managed_artifacts_sync(
        self,
        owner_scope: ArtifactOwnerScope,
        owner_qq: str | None,
        artifact_type: ManagedArtifactType | None,
        limit: int,
    ) -> list[ManagedArtifact]:
        if owner_scope is ArtifactOwnerScope.USER and (owner_qq is None or not owner_qq.isdigit()):
            raise ValueError("user scope requires a numeric owner QQ")
        if owner_scope is ArtifactOwnerScope.SYSTEM and owner_qq is not None:
            raise ValueError("system scope does not accept owner QQ")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        clauses = ["owner_scope = ?", "owner_qq IS ?"]
        params: list[Any] = [owner_scope.value, owner_qq]
        if artifact_type is not None:
            clauses.append("artifact_type = ?")
            params.append(artifact_type.value)
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM managed_artifacts WHERE {" AND ".join(clauses)}
                ORDER BY created_at DESC, id LIMIT ?
                """,
                params,
            ).fetchall()
        return [_artifact(row) for row in rows]

    async def managed_artifact(self, artifact_id: str) -> ManagedArtifact | None:
        return await asyncio.to_thread(self._managed_artifact_sync, artifact_id)

    def _managed_artifact_sync(self, artifact_id: str) -> ManagedArtifact | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM managed_artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        return _artifact(row) if row is not None else None

    async def all_managed_artifacts(self, *, limit: int = 10_000) -> list[ManagedArtifact]:
        return await asyncio.to_thread(self._all_managed_artifacts_sync, limit)

    def _all_managed_artifacts_sync(self, limit: int) -> list[ManagedArtifact]:
        if not 1 <= limit <= 50_000:
            raise ValueError("limit must be between 1 and 50000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM managed_artifacts ORDER BY created_at, id LIMIT ?", (limit,)
            ).fetchall()
        return [_artifact(row) for row in rows]

    async def artifact_references(self, artifact_id: str) -> list[ArtifactReference]:
        return await asyncio.to_thread(self._artifact_references_sync, artifact_id)

    def _artifact_references_sync(self, artifact_id: str) -> list[ArtifactReference]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM managed_artifact_references
                WHERE artifact_id = ? ORDER BY reference_type, reference_key
                """,
                (artifact_id,),
            ).fetchall()
        return [_reference(row) for row in rows]

    async def managed_artifact_source_records(self) -> dict[str, list[dict[str, Any]]]:
        return await asyncio.to_thread(self._managed_artifact_source_records_sync)

    def _managed_artifact_source_records_sync(self) -> dict[str, list[dict[str, Any]]]:
        with self._connect() as connection:
            privacy = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT artifact.*, request.request_kind,
                           request.status AS request_status
                    FROM privacy_job_artifacts AS artifact
                    JOIN privacy_requests AS request ON request.id = artifact.request_id
                    ORDER BY artifact.created_at, artifact.request_id, artifact.artifact_type
                    """
                ).fetchall()
            ]
            imports = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT blob.document_id, blob.storage_path, blob.media_type,
                           blob.detected_format, blob.byte_count, blob.created_at,
                           document.subject_user_qq AS owner_qq,
                           document.content_sha256, document.status AS document_status,
                           document.original_filename,
                           job.id AS job_id, job.status AS job_status,
                           EXISTS(
                               SELECT 1 FROM approval_requests AS approval
                               WHERE approval.subject_id = job.id
                                 AND approval.status = 'pending'
                           ) AS pending_approval
                    FROM document_blobs AS blob
                    JOIN knowledge_documents AS document
                      ON document.id = blob.document_id
                    LEFT JOIN knowledge_processing_jobs AS job
                      ON job.document_id = document.id
                    ORDER BY blob.created_at, blob.document_id
                    """
                ).fetchall()
            ]
            quarantine = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT batch.id AS batch_id, batch.state AS batch_state,
                           batch.owner_scope, batch.owner_qq, batch.updated_at,
                           item.artifact_id, item.sequence, item.source_relative_path,
                           item.quarantine_relative_path, item.expected_digest_sha256,
                           item.state AS item_state
                    FROM quarantine_batches AS batch
                    JOIN quarantine_batch_items AS item ON item.batch_id = batch.id
                    ORDER BY batch.created_at, item.sequence
                    """
                ).fetchall()
            ]
        return {"privacy": privacy, "imports": imports, "quarantine": quarantine}

    async def upsert_artifact_reference(
        self,
        reference: ArtifactReference,
        *,
        expected_revision: int | None = None,
    ) -> ArtifactReference | None:
        return await asyncio.to_thread(
            self._upsert_artifact_reference_sync, reference, expected_revision
        )

    def _upsert_artifact_reference_sync(
        self, reference: ArtifactReference, expected_revision: int | None
    ) -> ArtifactReference | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM managed_artifact_references
                WHERE artifact_id = ? AND reference_type = ? AND reference_key = ?
                """,
                (reference.artifact_id, reference.reference_type, reference.reference_key),
            ).fetchone()
            if row is None:
                if expected_revision is not None or reference.revision != 1:
                    connection.rollback()
                    return None
                connection.execute(
                    """
                    INSERT INTO managed_artifact_references(
                        id, artifact_id, reference_type, reference_key,
                        state, revision, observed_at
                    ) VALUES(?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        reference.reference_id,
                        reference.artifact_id,
                        reference.reference_type,
                        reference.reference_key,
                        reference.state.value,
                        _iso(reference.observed_at, "observed_at"),
                    ),
                )
            else:
                if (
                    expected_revision is None
                    or int(row["revision"]) != expected_revision
                    or reference.revision != expected_revision + 1
                    or reference.reference_id != row["id"]
                ):
                    connection.rollback()
                    return None
                cursor = connection.execute(
                    """
                    UPDATE managed_artifact_references
                    SET state = ?, revision = ?, observed_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (
                        reference.state.value,
                        reference.revision,
                        _iso(reference.observed_at, "observed_at"),
                        reference.reference_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return None
            updated = connection.execute(
                "SELECT * FROM managed_artifact_references WHERE id = ?",
                (reference.reference_id,),
            ).fetchone()
        return _reference(updated)

    async def create_retention_preview(
        self, preview: RetentionPreview, *, now: datetime | None = None
    ) -> None:
        await asyncio.to_thread(self._create_retention_preview_sync, preview, _utc_now(now))

    def _create_retention_preview_sync(self, preview: RetentionPreview, now: datetime) -> None:
        if preview.state is not RetentionPreviewState.READY or preview.revision != 1:
            raise ValueError("new retention previews must start ready at revision 1")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if preview.candidate_ids:
                placeholders = ",".join("?" for _item in preview.candidate_ids)
                rows = connection.execute(
                    f"SELECT * FROM managed_artifacts WHERE id IN ({placeholders})",
                    preview.candidate_ids,
                ).fetchall()
                artifacts = {str(row["id"]): _artifact(row) for row in rows}
                expected_evidence = dict(preview.evidence_revisions)
                if (
                    len(rows) != len(preview.candidate_ids)
                    or tuple(expected_evidence) != preview.candidate_ids
                    or any(
                        artifacts[artifact_id].retention_state
                        is not ArtifactRetentionState.CANDIDATE
                        or artifact_evidence_revision(artifacts[artifact_id])
                        != expected_evidence[artifact_id]
                        for artifact_id in preview.candidate_ids
                    )
                    or sum(artifacts[item].size_bytes for item in preview.candidate_ids)
                    != preview.total_bytes
                ):
                    raise ValueError(
                        "preview candidates or evidence do not match current catalog state"
                    )
            connection.execute(
                """
                INSERT INTO retention_previews(
                    id, token_hash, actor_id, actor_source, process_instance_id,
                    policy_revision, candidate_ids_json, evidence_revisions_json,
                    total_bytes, target_batch_type, state, revision, correlation_id,
                    created_at, expires_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', 1, ?, ?, ?)
                """,
                (
                    preview.preview_id,
                    preview.token_hash,
                    preview.actor_id,
                    preview.actor_source,
                    preview.process_instance_id,
                    preview.policy_revision,
                    _json(preview.candidate_ids),
                    _json(preview.evidence_revisions),
                    preview.total_bytes,
                    preview.target_batch_type,
                    preview.correlation_id,
                    _iso(now, "now"),
                    _iso(preview.expires_at, "expires_at"),
                ),
            )

    async def retention_preview_by_token_hash(self, token_hash: str) -> RetentionPreview | None:
        return await asyncio.to_thread(self._retention_preview_by_token_hash_sync, token_hash)

    def _retention_preview_by_token_hash_sync(self, token_hash: str) -> RetentionPreview | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM retention_previews WHERE token_hash = ?", (token_hash,)
            ).fetchone()
        return _preview(row) if row is not None else None

    async def consume_retention_preview(
        self,
        *,
        token_hash: str,
        actor_id: str,
        process_instance_id: str,
        expected_revision: int,
        policy_revision: int,
        now: datetime | None = None,
    ) -> RetentionPreview | None:
        return await asyncio.to_thread(
            self._consume_retention_preview_sync,
            token_hash,
            actor_id,
            process_instance_id,
            expected_revision,
            policy_revision,
            _utc_now(now),
        )

    def _consume_retention_preview_sync(
        self,
        token_hash: str,
        actor_id: str,
        process_instance_id: str,
        expected_revision: int,
        policy_revision: int,
        now: datetime,
    ) -> RetentionPreview | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM retention_previews WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if row is None or row["state"] != RetentionPreviewState.READY.value:
                connection.rollback()
                return None
            if datetime.fromisoformat(row["expires_at"]) < now:
                connection.execute(
                    """
                    UPDATE retention_previews SET state = 'expired', revision = revision + 1
                    WHERE id = ? AND state = 'ready'
                    """,
                    (row["id"],),
                )
                return None
            if (
                row["actor_id"] != actor_id
                or row["process_instance_id"] != process_instance_id
                or int(row["revision"]) != expected_revision
                or int(row["policy_revision"]) != policy_revision
            ):
                connection.rollback()
                return None
            candidate_ids = tuple(json.loads(row["candidate_ids_json"]))
            expected_evidence = dict(json.loads(row["evidence_revisions_json"]))
            placeholders = ",".join("?" for _item in candidate_ids)
            candidates = (
                connection.execute(
                    f"SELECT * FROM managed_artifacts WHERE id IN ({placeholders})",
                    candidate_ids,
                ).fetchall()
                if candidate_ids
                else []
            )
            artifacts = {str(item["id"]): _artifact(item) for item in candidates}
            if (
                len(artifacts) != len(candidate_ids)
                or tuple(expected_evidence) != candidate_ids
                or any(
                    artifacts[artifact_id].retention_state is not ArtifactRetentionState.CANDIDATE
                    or artifact_evidence_revision(artifacts[artifact_id])
                    != expected_evidence[artifact_id]
                    for artifact_id in candidate_ids
                )
                or sum(artifacts[item].size_bytes for item in candidate_ids)
                != int(row["total_bytes"])
            ):
                connection.rollback()
                return None
            cursor = connection.execute(
                """
                UPDATE retention_previews
                SET state = 'consumed', revision = revision + 1, consumed_at = ?
                WHERE id = ? AND state = 'ready' AND revision = ?
                """,
                (_iso(now, "now"), row["id"], expected_revision),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            updated = connection.execute(
                "SELECT * FROM retention_previews WHERE id = ?", (row["id"],)
            ).fetchone()
        return _preview(updated)

    async def create_quarantine_batch(
        self, batch: QuarantineBatch, items: tuple[QuarantineItem, ...]
    ) -> None:
        await asyncio.to_thread(self._create_quarantine_batch_sync, batch, items)

    def _create_quarantine_batch_sync(
        self, batch: QuarantineBatch, items: tuple[QuarantineItem, ...]
    ) -> None:
        if batch.state is not QuarantineBatchState.PREPARED or batch.revision != 1:
            raise ValueError("new quarantine batches must start prepared at revision 1")
        if len({item.sequence for item in items}) != len(items):
            raise ValueError("quarantine item sequences must be unique")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            preview = connection.execute(
                "SELECT * FROM retention_previews WHERE id = ?", (batch.preview_id,)
            ).fetchone()
            if (
                preview is None
                or preview["state"] != RetentionPreviewState.CONSUMED.value
                or preview["actor_id"] != batch.actor_id
                or preview["process_instance_id"] != batch.process_instance_id
            ):
                raise ValueError("quarantine batch requires a matching consumed preview")
            connection.execute(
                """
                INSERT INTO quarantine_batches(
                    id, preview_id, batch_type, owner_scope, owner_qq, actor_id,
                    process_instance_id, state, revision, correlation_id, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'prepared', 1, ?, ?, ?)
                """,
                (
                    batch.batch_id,
                    batch.preview_id,
                    batch.batch_type,
                    batch.owner_scope.value,
                    batch.owner_qq,
                    batch.actor_id,
                    batch.process_instance_id,
                    batch.correlation_id,
                    _iso(batch.created_at, "created_at"),
                    _iso(batch.updated_at, "updated_at"),
                ),
            )
            candidate_ids = tuple(json.loads(preview["candidate_ids_json"]))
            if tuple(
                item.artifact_id for item in sorted(items, key=lambda item: item.sequence)
            ) != (candidate_ids):
                raise ValueError(
                    "quarantine items must exactly match the ordered preview candidates"
                )
            for item in items:
                connection.execute(
                    """
                    INSERT INTO quarantine_batch_items(
                        batch_id, artifact_id, sequence, source_relative_path,
                        quarantine_relative_path, expected_digest_sha256, state,
                        error_code, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch.batch_id,
                        item.artifact_id,
                        item.sequence,
                        item.source_relative_path,
                        item.quarantine_relative_path,
                        item.expected_digest_sha256,
                        item.state.value,
                        item.error_code,
                        _iso(batch.updated_at, "updated_at"),
                    ),
                )

    async def transition_quarantine_batch(
        self,
        *,
        batch_id: str,
        expected_revision: int,
        target: QuarantineBatchState,
        failure_code: str = "",
        now: datetime | None = None,
    ) -> QuarantineBatch | None:
        return await asyncio.to_thread(
            self._transition_quarantine_batch_sync,
            batch_id,
            expected_revision,
            target,
            failure_code,
            _utc_now(now),
        )

    def _transition_quarantine_batch_sync(
        self,
        batch_id: str,
        expected_revision: int,
        target: QuarantineBatchState,
        failure_code: str,
        now: datetime,
    ) -> QuarantineBatch | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM quarantine_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            if row is None or int(row["revision"]) != expected_revision:
                connection.rollback()
                return None
            current = QuarantineBatchState(row["state"])
            if not can_transition_quarantine_batch(current, target):
                raise ValueError(
                    f"invalid quarantine transition: {current.value} -> {target.value}"
                )
            cursor = connection.execute(
                """
                UPDATE quarantine_batches
                SET state = ?, revision = revision + 1, failure_code = ?, updated_at = ?
                WHERE id = ? AND revision = ?
                """,
                (
                    target.value,
                    failure_code,
                    _iso(now, "now"),
                    batch_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            updated = connection.execute(
                "SELECT * FROM quarantine_batches WHERE id = ?", (batch_id,)
            ).fetchone()
        return _batch(updated)

    async def set_artifact_retention_state(
        self,
        *,
        artifact_id: str,
        expected_revision: int,
        target: ArtifactRetentionState,
        now: datetime | None = None,
    ) -> ManagedArtifact | None:
        return await asyncio.to_thread(
            self._set_artifact_retention_state_sync,
            artifact_id,
            expected_revision,
            target,
            _utc_now(now),
        )

    def _set_artifact_retention_state_sync(
        self,
        artifact_id: str,
        expected_revision: int,
        target: ArtifactRetentionState,
        now: datetime,
    ) -> ManagedArtifact | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM managed_artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
            if row is None or int(row["revision"]) != expected_revision:
                connection.rollback()
                return None
            artifact = _artifact(row)
            if target is ArtifactRetentionState.CANDIDATE and (
                artifact.verification_state is not ArtifactVerificationState.VERIFIED
                or artifact.reference_state is not ArtifactReferenceState.UNREFERENCED
            ):
                connection.rollback()
                return None
            if artifact.retention_state is target:
                return artifact
            cursor = connection.execute(
                """
                UPDATE managed_artifacts
                SET retention_state = ?, revision = revision + 1, updated_at = ?
                WHERE id = ? AND revision = ?
                """,
                (target.value, _iso(now, "now"), artifact_id, expected_revision),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            updated = connection.execute(
                "SELECT * FROM managed_artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        return _artifact(updated)

    async def list_quarantine_batches(
        self, *, states: tuple[QuarantineBatchState, ...] | None = None
    ) -> list[QuarantineBatch]:
        return await asyncio.to_thread(self._list_quarantine_batches_sync, states)

    def _list_quarantine_batches_sync(
        self, states: tuple[QuarantineBatchState, ...] | None
    ) -> list[QuarantineBatch]:
        with self._connect() as connection:
            if states:
                placeholders = ",".join("?" for _item in states)
                rows = connection.execute(
                    f"SELECT * FROM quarantine_batches WHERE state IN ({placeholders}) "
                    "ORDER BY created_at, id",
                    tuple(item.value for item in states),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM quarantine_batches ORDER BY created_at, id"
                ).fetchall()
        return [_batch(row) for row in rows]

    async def quarantine_batch_items(self, batch_id: str) -> list[QuarantineItem]:
        return await asyncio.to_thread(self._quarantine_batch_items_sync, batch_id)

    def _quarantine_batch_items_sync(self, batch_id: str) -> list[QuarantineItem]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM quarantine_batch_items
                WHERE batch_id = ? ORDER BY sequence
                """,
                (batch_id,),
            ).fetchall()
        return [
            QuarantineItem(
                artifact_id=row["artifact_id"],
                sequence=int(row["sequence"]),
                source_relative_path=row["source_relative_path"],
                quarantine_relative_path=row["quarantine_relative_path"],
                expected_digest_sha256=row["expected_digest_sha256"],
                state=QuarantineItemState(row["state"]),
                error_code=row["error_code"],
            )
            for row in rows
        ]

    async def set_quarantine_item_state(
        self,
        *,
        batch_id: str,
        artifact_id: str,
        expected: QuarantineItemState,
        target: QuarantineItemState,
        error_code: str = "",
        now: datetime | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._set_quarantine_item_state_sync,
            batch_id,
            artifact_id,
            expected,
            target,
            error_code,
            _utc_now(now),
        )

    def _set_quarantine_item_state_sync(
        self,
        batch_id: str,
        artifact_id: str,
        expected: QuarantineItemState,
        target: QuarantineItemState,
        error_code: str,
        now: datetime,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE quarantine_batch_items
                SET state = ?, error_code = ?, updated_at = ?
                WHERE batch_id = ? AND artifact_id = ? AND state = ?
                """,
                (
                    target.value,
                    error_code,
                    _iso(now, "now"),
                    batch_id,
                    artifact_id,
                    expected.value,
                ),
            )
        return cursor.rowcount == 1

    async def finalize_quarantine_batch(
        self,
        *,
        batch_id: str,
        expected_revision: int,
        now: datetime | None = None,
    ) -> QuarantineBatch | None:
        return await asyncio.to_thread(
            self._finalize_quarantine_batch_sync,
            batch_id,
            expected_revision,
            _utc_now(now),
        )

    def _finalize_quarantine_batch_sync(
        self, batch_id: str, expected_revision: int, now: datetime
    ) -> QuarantineBatch | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch = connection.execute(
                "SELECT * FROM quarantine_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            items = connection.execute(
                "SELECT * FROM quarantine_batch_items WHERE batch_id = ?",
                (batch_id,),
            ).fetchall()
            if (
                batch is None
                or batch["state"] != QuarantineBatchState.MOVING.value
                or int(batch["revision"]) != expected_revision
                or not items
                or any(item["state"] != QuarantineItemState.MOVED.value for item in items)
            ):
                connection.rollback()
                return None
            for item in items:
                cursor = connection.execute(
                    """
                    UPDATE managed_artifacts
                    SET retention_state = 'quarantined', quarantine_batch_id = ?,
                        revision = revision + 1, updated_at = ?
                    WHERE id = ? AND retention_state = 'candidate'
                    """,
                    (batch_id, _iso(now, "now"), item["artifact_id"]),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return None
            cursor = connection.execute(
                """
                UPDATE quarantine_batches
                SET state = 'quarantined', revision = revision + 1, updated_at = ?
                WHERE id = ? AND state = 'moving' AND revision = ?
                """,
                (_iso(now, "now"), batch_id, expected_revision),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            updated = connection.execute(
                "SELECT * FROM quarantine_batches WHERE id = ?", (batch_id,)
            ).fetchone()
        return _batch(updated)
