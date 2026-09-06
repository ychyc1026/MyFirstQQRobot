import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
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
    RetentionPreview,
    RetentionPreviewState,
    artifact_evidence_revision,
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
)
from ych_bot.infrastructure.database import SQLiteRepository

BOT_QQ = "2000000002"
USER_A = "10000001"
USER_B = "10000002"
NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)


def seed_scopes(database_path: Path) -> None:
    now = NOW.isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO bots(
                qq, label, quota_user_reply, created_at, updated_at
            ) VALUES(?, 'test', 'quota exceeded', ?, ?)
            """,
            (BOT_QQ, now, now),
        )
        connection.executemany(
            """
            INSERT INTO users(qq_id, first_seen_at, last_seen_at) VALUES(?, ?, ?)
            """,
            ((USER_A, now, now), (USER_B, now, now)),
        )


def readiness_decision(*, decision_id: str, revision: int = 1) -> ReadinessDecision:
    freshness = EvidenceFreshness(observed_at=NOW, expires_at=NOW + timedelta(minutes=2))
    blocker = ReadinessBlocker(
        code=ReadinessReasonCode.NETWORK_GATE_CLOSED,
        probe_code="onebot.connection",
        capability_scope=CapabilityScope.QQ_REPLY,
        safe_detail="outbound network gate is closed",
    )
    return ReadinessDecision(
        decision_id=decision_id,
        bot_qq=BOT_QQ,
        process_instance_id="process-a",
        profile=ReadinessProfile.CONTROLLED_REAL_EFFECT,
        capability_scope=CapabilityScope.QQ_REPLY,
        scope_hash="scope-hash",
        revision=revision,
        status=ReadinessDecisionStatus.BLOCKED,
        evaluated_at=NOW,
        blockers=(blocker,),
        probes=(
            ReadinessProbeResult(
                probe_code="onebot.connection",
                status=ProbeStatus.BLOCKED,
                capability_scope=CapabilityScope.QQ_REPLY,
                freshness=freshness,
                source="onebot-provider",
                source_revision="connection-0",
                safe_detail="not connected",
                remediation_code="connect_expected_bot",
                evidence=(("configured", "false"),),
            ),
        ),
        correlation_id="correlation-a",
    )


def artifact(
    *,
    artifact_id: str,
    owner_qq: str | None,
    path: str,
    revision: int = 1,
) -> ManagedArtifact:
    scope = ArtifactOwnerScope.USER if owner_qq else ArtifactOwnerScope.SYSTEM
    return ManagedArtifact(
        artifact_id=artifact_id,
        artifact_type=(
            ManagedArtifactType.PRIVACY_EXPORT if owner_qq else ManagedArtifactType.MIGRATION_BACKUP
        ),
        owner_scope=scope,
        owner_qq=owner_qq,
        relative_path=path,
        bundle_key=f"bundle-{artifact_id}",
        size_bytes=128,
        digest_sha256=f"digest-{artifact_id}",
        manifest_type="test-v1",
        created_at=NOW,
        verification_state=ArtifactVerificationState.VERIFIED,
        verification_revision=1,
        reference_state=ArtifactReferenceState.UNREFERENCED,
        reference_revision=1,
        retention_state=ArtifactRetentionState.CANDIDATE,
        revision=revision,
    )


@pytest.mark.asyncio
async def test_readiness_decisions_are_immutable_process_and_bot_scoped(tmp_path: Path) -> None:
    database_path = tmp_path / "readiness.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()
    seed_scopes(database_path)

    decision = readiness_decision(decision_id="decision-1")
    await repository.create_readiness_decision(decision)
    restored = await repository.readiness_decision(decision.decision_id)

    assert restored == decision
    assert await repository.list_readiness_decisions(
        bot_qq=BOT_QQ, process_instance_id="process-a"
    ) == [decision]
    assert (
        await repository.list_readiness_decisions(bot_qq=BOT_QQ, process_instance_id="process-b")
        == []
    )
    with pytest.raises(sqlite3.IntegrityError):
        await repository.create_readiness_decision(
            readiness_decision(decision_id="different-id-same-revision")
        )


@pytest.mark.asyncio
async def test_schema_defaults_deny_readiness_and_retention_and_enforce_keys(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "defaults.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT COUNT(*) FROM readiness_decisions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM retention_previews").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM quarantine_batches").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO readiness_decisions(
                    id, bot_qq, process_instance_id, profile, capability_scope,
                    scope_hash, revision, status, evaluated_at
                ) VALUES('missing-bot', '99999999', 'process', 'local_start',
                         'local_runtime', 'scope', 1, 'passed', ?)
                """,
                (NOW.isoformat(),),
            )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.asyncio
async def test_artifact_upsert_is_revision_guarded_and_user_isolated(tmp_path: Path) -> None:
    database_path = tmp_path / "artifacts.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()
    seed_scopes(database_path)

    first = artifact(artifact_id="artifact-a", owner_qq=USER_A, path="exports/a.zip")
    second_user = artifact(artifact_id="artifact-b", owner_qq=USER_B, path="exports/b.zip")
    assert await repository.upsert_managed_artifact(first, now=NOW) == first
    assert await repository.upsert_managed_artifact(second_user, now=NOW) == second_user

    stale_update = replace(first, size_bytes=256, revision=2)
    assert (
        await repository.upsert_managed_artifact(
            stale_update, expected_revision=99, now=NOW + timedelta(seconds=1)
        )
        is None
    )
    updated = await repository.upsert_managed_artifact(
        stale_update, expected_revision=1, now=NOW + timedelta(seconds=1)
    )
    assert updated == stale_update
    assert await repository.list_managed_artifacts(
        owner_scope=ArtifactOwnerScope.USER, owner_qq=USER_A
    ) == [stale_update]
    assert await repository.list_managed_artifacts(
        owner_scope=ArtifactOwnerScope.USER, owner_qq=USER_B
    ) == [second_user]

    duplicate_identity = replace(stale_update, artifact_id="different-id", revision=1)
    assert await repository.upsert_managed_artifact(duplicate_identity, now=NOW) is None


@pytest.mark.asyncio
async def test_reference_preview_and_batch_compare_and_set_are_single_use(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "retention.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()
    seed_scopes(database_path)
    managed = artifact(artifact_id="artifact-a", owner_qq=USER_A, path="exports/a.zip")
    await repository.upsert_managed_artifact(managed, now=NOW)

    reference = ArtifactReference(
        reference_id="reference-a",
        artifact_id=managed.artifact_id,
        reference_type="privacy_job",
        reference_key="job-a",
        state=ArtifactReferenceState.UNREFERENCED,
        revision=1,
        observed_at=NOW,
    )
    assert await repository.upsert_artifact_reference(reference) == reference
    next_reference = replace(
        reference,
        state=ArtifactReferenceState.PROTECTED,
        revision=2,
        observed_at=NOW + timedelta(seconds=1),
    )
    assert await repository.upsert_artifact_reference(next_reference, expected_revision=7) is None
    assert (
        await repository.upsert_artifact_reference(next_reference, expected_revision=1)
        == next_reference
    )

    preview = RetentionPreview(
        preview_id="preview-a",
        token_hash="hashed-random-handle",
        actor_id="admin-session-a",
        actor_source="dashboard",
        process_instance_id="process-a",
        policy_revision=1,
        candidate_ids=(managed.artifact_id,),
        evidence_revisions=((managed.artifact_id, artifact_evidence_revision(managed)),),
        total_bytes=managed.size_bytes,
        target_batch_type="privacy-retention",
        expires_at=NOW + timedelta(minutes=5),
        correlation_id="correlation-a",
    )
    await repository.create_retention_preview(preview, now=NOW)
    assert (
        await repository.consume_retention_preview(
            token_hash=preview.token_hash,
            actor_id="wrong-actor",
            process_instance_id=preview.process_instance_id,
            expected_revision=1,
            policy_revision=1,
            now=NOW,
        )
        is None
    )
    consumed = await repository.consume_retention_preview(
        token_hash=preview.token_hash,
        actor_id=preview.actor_id,
        process_instance_id=preview.process_instance_id,
        expected_revision=1,
        policy_revision=1,
        now=NOW,
    )
    assert consumed is not None
    assert consumed.state is RetentionPreviewState.CONSUMED
    assert consumed.revision == 2
    assert (
        await repository.consume_retention_preview(
            token_hash=preview.token_hash,
            actor_id=preview.actor_id,
            process_instance_id=preview.process_instance_id,
            expected_revision=2,
            policy_revision=1,
            now=NOW,
        )
        is None
    )

    batch = QuarantineBatch(
        batch_id="batch-a",
        preview_id=preview.preview_id,
        batch_type=preview.target_batch_type,
        owner_scope=ArtifactOwnerScope.USER,
        owner_qq=USER_A,
        actor_id=preview.actor_id,
        process_instance_id=preview.process_instance_id,
        state=QuarantineBatchState.PREPARED,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
        correlation_id="correlation-a",
    )
    item = QuarantineItem(
        artifact_id=managed.artifact_id,
        sequence=1,
        source_relative_path=managed.relative_path,
        quarantine_relative_path="trash/batch-a/a.zip",
        expected_digest_sha256=managed.digest_sha256,
    )
    await repository.create_quarantine_batch(batch, (item,))
    moving = await repository.transition_quarantine_batch(
        batch_id=batch.batch_id,
        expected_revision=1,
        target=QuarantineBatchState.MOVING,
        now=NOW + timedelta(seconds=1),
    )
    assert moving is not None
    assert moving.state is QuarantineBatchState.MOVING
    assert moving.revision == 2
    assert (
        await repository.transition_quarantine_batch(
            batch_id=batch.batch_id,
            expected_revision=1,
            target=QuarantineBatchState.QUARANTINED,
            now=NOW + timedelta(seconds=2),
        )
        is None
    )


def test_contracts_reject_stale_time_and_unsafe_paths() -> None:
    freshness = EvidenceFreshness(observed_at=NOW, expires_at=NOW + timedelta(seconds=1))
    assert freshness.status_at(NOW + timedelta(seconds=2)) is ProbeStatus.STALE
    with pytest.raises(ValueError, match="managed root"):
        artifact(artifact_id="escape", owner_qq=None, path="..\\outside.db")
