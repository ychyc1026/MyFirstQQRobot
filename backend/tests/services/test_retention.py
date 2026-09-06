import asyncio
import gc
import hashlib
import json
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application import (
    ArtifactInventoryService,
    ManagedRootRegistry,
    MigrationBackupAdapter,
    RetentionError,
    RetentionPolicy,
    RetentionService,
    RetentionTypePolicy,
)
from ych_bot.config import Settings
from ych_bot.domain.managed_artifacts import (
    ArtifactOwnerScope,
    ArtifactReferenceState,
    ArtifactRetentionState,
    ArtifactVerificationState,
    ManagedArtifact,
    ManagedArtifactType,
    QuarantineBatchState,
)
from ych_bot.infrastructure.database import SQLiteRepository

NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
USER_QQ = "10000001"


class ControlledClock:
    def __init__(self, current: datetime = NOW) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class AbruptStop(BaseException):
    pass


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_backup(root: Path, *, index: int, created_at: datetime) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    day = 31 - index
    stem = f"ych-before-v32-202608{day:02d}T080000000000Z"
    backup = root / f"{stem}.sqlite3"
    with sqlite3.connect(backup) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence VALUES('retention')")
    content = backup.read_bytes()
    manifest = backup.with_suffix(".manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "format": "ych-migration-backup-v1",
                "backup_file": backup.name,
                "sha256": _digest(content),
                "byte_count": len(content),
                "source_schema_version": 31,
                "target_schema_version": 32,
                "created_at": created_at.isoformat(),
                "integrity_result": "ok",
            }
        ),
        encoding="utf-8",
    )
    return backup, manifest


async def _migration_retention(
    tmp_path: Path,
    *,
    clock: ControlledClock | None = None,
    move_file=None,
) -> tuple[SQLiteRepository, RetentionService, list[tuple[Path, Path]]]:
    active_clock = clock or ControlledClock()
    repository = SQLiteRepository(tmp_path / "retention.sqlite3")
    await repository.initialize()
    backup_root = tmp_path / "storage" / "backups" / "migrations"
    created = (NOW, NOW - timedelta(days=1), NOW - timedelta(days=2), NOW - timedelta(days=100))
    pairs = [
        _write_backup(backup_root, index=index, created_at=value)
        for index, value in enumerate(created)
    ]
    roots = ManagedRootRegistry(tmp_path)
    inventory = ArtifactInventoryService(
        repository,
        adapters=(MigrationBackupAdapter(roots, clock=active_clock),),
        clock=active_clock,
    )
    service = RetentionService(
        repository,
        inventory,
        roots,
        process_instance_id="process-a",
        clock=active_clock,
        move_file=move_file,
    )
    return repository, service, pairs


def _privacy_artifact(tmp_path: Path) -> tuple[Path, ManagedArtifact]:
    path = tmp_path / "storage" / "exports" / "old-export.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = b'{"private":"content"}'
    path.write_bytes(content)
    return path, ManagedArtifact(
        artifact_id="old-privacy-export",
        artifact_type=ManagedArtifactType.PRIVACY_EXPORT,
        owner_scope=ArtifactOwnerScope.USER,
        owner_qq=USER_QQ,
        relative_path=path.relative_to(tmp_path).as_posix(),
        bundle_key="old-export",
        size_bytes=len(content),
        digest_sha256=_digest(content),
        manifest_type="ych-privacy-bundle-v1",
        created_at=NOW - timedelta(days=100),
        verification_state=ArtifactVerificationState.VERIFIED,
        reference_state=ArtifactReferenceState.UNREFERENCED,
        retention_state=ArtifactRetentionState.RETAIN,
    )


@pytest.mark.asyncio
async def test_classification_orders_integrity_references_and_policy(tmp_path: Path) -> None:
    repository, service, pairs = await _migration_retention(tmp_path)
    privacy_path, privacy = _privacy_artifact(tmp_path)
    await repository.upsert_managed_artifact(privacy, now=NOW)

    artifacts = await service.refresh_classification()

    migration = [
        item for item in artifacts if item.artifact_type is ManagedArtifactType.MIGRATION_BACKUP
    ]
    assert len(migration) == 4
    assert sum(item.retention_state is ArtifactRetentionState.CANDIDATE for item in migration) == 1
    assert all(
        item.retention_state is ArtifactRetentionState.RETAIN
        for item in migration
        if item.reference_state is ArtifactReferenceState.PROTECTED
    )
    stored_privacy = await repository.managed_artifact(privacy.artifact_id)
    assert stored_privacy is not None
    assert stored_privacy.retention_state is ArtifactRetentionState.RETAIN
    assert privacy_path.is_file()

    pairs[-1][0].write_bytes(b"tampered")
    artifacts = await service.refresh_classification()
    damaged = next(item for item in artifacts if item.bundle_key == pairs[-1][0].stem)
    assert damaged.verification_state is ArtifactVerificationState.INVALID
    assert damaged.retention_state is ArtifactRetentionState.BLOCKED

    explicit = RetentionService(
        repository,
        ArtifactInventoryService(repository, adapters=()),
        ManagedRootRegistry(tmp_path),
        process_instance_id="process-a",
        clock=ControlledClock(),
        policy=RetentionPolicy(
            revision=2,
            rules=(
                RetentionTypePolicy(ManagedArtifactType.MIGRATION_BACKUP, 90, keep_latest=3),
                RetentionTypePolicy(ManagedArtifactType.PRIVACY_EXPORT, 30),
            ),
        ),
    )
    artifacts = await explicit.refresh_classification()
    configured_privacy = next(item for item in artifacts if item.artifact_id == privacy.artifact_id)
    assert configured_privacy.retention_state is ArtifactRetentionState.CANDIDATE


@pytest.mark.asyncio
async def test_preview_is_hashed_single_use_and_quarantines_complete_bundle(
    tmp_path: Path,
) -> None:
    repository, service, pairs = await _migration_retention(tmp_path)
    preview = await service.create_preview(
        actor_id="session-hash",
        actor_source="dashboard",
        target_batch_type="migration_backups",
        owner_scope=ArtifactOwnerScope.SYSTEM,
    )
    with sqlite3.connect(repository.path) as connection:
        stored_hash = connection.execute(
            "SELECT token_hash FROM retention_previews WHERE id = ?",
            (preview["preview_id"],),
        ).fetchone()[0]
    assert stored_hash != preview["confirmation_token"]
    assert preview["confirmation_token"] not in repository.path.read_bytes().decode(
        "utf-8", errors="ignore"
    )

    result = await service.confirm(
        confirmation_token=preview["confirmation_token"],
        actor_id="session-hash",
        expected_revision=1,
    )

    assert result["status"] == "quarantined"
    assert result["permanent_deletion"] is False
    assert not pairs[-1][0].exists()
    assert not pairs[-1][1].exists()
    quarantine = tmp_path / "storage" / "trash" / "managed-artifacts" / result["batch_id"] / "0001"
    assert (quarantine / pairs[-1][0].name).is_file()
    assert (quarantine / pairs[-1][1].name).is_file()
    artifact = (
        await repository.list_managed_artifacts(
            owner_scope=ArtifactOwnerScope.SYSTEM, owner_qq=None
        )
    )[-1]
    quarantined = next(
        item
        for item in await repository.all_managed_artifacts()
        if item.bundle_key == pairs[-1][0].stem
    )
    assert quarantined.retention_state is ArtifactRetentionState.QUARANTINED
    assert artifact is not None
    batches = await repository.list_quarantine_batches()
    assert batches[-1].state is QuarantineBatchState.QUARANTINED
    with pytest.raises(RetentionError, match="no longer usable"):
        await service.confirm(
            confirmation_token=preview["confirmation_token"],
            actor_id="session-hash",
            expected_revision=1,
        )
    with sqlite3.connect(repository.path) as connection:
        audits = connection.execute(
            "SELECT action, details_json FROM audit_log WHERE action LIKE 'retention.%'"
        ).fetchall()
        report_runtime = connection.execute(
            """
            SELECT owner_report_runtime.delivery_policy,
                   owner_report_runtime.delivery_status
            FROM owner_report_runtime
            JOIN owner_reports ON owner_reports.id = owner_report_runtime.report_id
            WHERE owner_reports.category = 'managed_artifact_retention'
            """
        ).fetchall()
    assert {row[0] for row in audits} >= {
        "retention.preview_created",
        "retention.quarantined",
        "retention.confirm_rejected",
    }
    assert preview["confirmation_token"] not in "".join(row[1] for row in audits)
    assert report_runtime
    assert all(row == ("dashboard_only", "suppressed") for row in report_runtime)


@pytest.mark.asyncio
async def test_preview_rejects_actor_expiry_policy_and_candidate_mutation(
    tmp_path: Path,
) -> None:
    clock = ControlledClock()
    repository, service, pairs = await _migration_retention(tmp_path, clock=clock)
    preview = await service.create_preview(
        actor_id="actor-a",
        actor_source="dashboard",
        target_batch_type="migration_backups",
    )
    with pytest.raises(RetentionError, match="does not match"):
        await service.confirm(
            confirmation_token=preview["confirmation_token"],
            actor_id="actor-b",
            expected_revision=1,
        )
    assert pairs[-1][0].is_file()

    changed_policy = RetentionService(
        repository,
        service._inventory,
        service._roots,
        process_instance_id="process-a",
        clock=clock,
        policy=RetentionPolicy(revision=2),
    )
    with pytest.raises(RetentionError, match="policy changed"):
        await changed_policy.confirm(
            confirmation_token=preview["confirmation_token"],
            actor_id="actor-a",
            expected_revision=1,
        )

    pairs[-1][0].write_bytes(b"mutated after preview")
    with pytest.raises(RetentionError, match="stale"):
        await service.confirm(
            confirmation_token=preview["confirmation_token"],
            actor_id="actor-a",
            expected_revision=1,
        )
    assert pairs[-1][0].is_file()

    repository2, service2, _pairs2 = await _migration_retention(tmp_path / "expiry", clock=clock)
    expiring = await service2.create_preview(
        actor_id="actor-a",
        actor_source="dashboard",
        target_batch_type="migration_backups",
    )
    clock.current = NOW + timedelta(minutes=6)
    with pytest.raises(RetentionError, match="expired"):
        await service2.confirm(
            confirmation_token=expiring["confirmation_token"],
            actor_id="actor-a",
            expected_revision=1,
        )
    with sqlite3.connect(repository2.path) as connection:
        assert (
            connection.execute(
                "SELECT state FROM retention_previews WHERE id = ?",
                (expiring["preview_id"],),
            ).fetchone()[0]
            == "expired"
        )


@pytest.mark.asyncio
async def test_reference_revision_change_invalidates_preview(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reference-change.sqlite3")
    await repository.initialize()
    path, artifact = _privacy_artifact(tmp_path)
    await repository.upsert_managed_artifact(artifact, now=NOW)
    roots = ManagedRootRegistry(tmp_path)
    service = RetentionService(
        repository,
        ArtifactInventoryService(repository, adapters=()),
        roots,
        process_instance_id="process-a",
        clock=ControlledClock(),
        policy=RetentionPolicy(
            rules=(RetentionTypePolicy(ManagedArtifactType.PRIVACY_EXPORT, 30),)
        ),
    )
    preview = await service.create_preview(
        actor_id="actor-a",
        actor_source="dashboard",
        target_batch_type="privacy_exports",
        owner_scope=ArtifactOwnerScope.USER,
        owner_qq=USER_QQ,
    )
    current = await repository.managed_artifact(artifact.artifact_id)
    assert current is not None
    changed = replace(
        current,
        reference_state=ArtifactReferenceState.PROTECTED,
        reference_revision=current.reference_revision + 1,
        revision=current.revision + 1,
    )
    assert (
        await repository.upsert_managed_artifact(
            changed, expected_revision=current.revision, now=NOW
        )
        is not None
    )

    with pytest.raises(RetentionError, match="stale"):
        await service.confirm(
            confirmation_token=preview["confirmation_token"],
            actor_id="actor-a",
            expected_revision=1,
        )
    assert path.is_file()


@pytest.mark.asyncio
async def test_candidate_set_addition_invalidates_preview(tmp_path: Path) -> None:
    _repository, service, pairs = await _migration_retention(tmp_path)
    preview = await service.create_preview(
        actor_id="actor-a",
        actor_source="dashboard",
        target_batch_type="migration_backups",
    )
    _write_backup(
        tmp_path / "storage" / "backups" / "migrations",
        index=4,
        created_at=NOW - timedelta(days=150),
    )

    with pytest.raises(RetentionError, match="stale"):
        await service.confirm(
            confirmation_token=preview["confirmation_token"],
            actor_id="actor-a",
            expected_revision=1,
        )
    assert pairs[-1][0].is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("locked_member", ["database", "sidecar"])
async def test_file_lock_or_sidecar_failure_rolls_back_without_deletion(
    tmp_path: Path,
    locked_member: str,
) -> None:
    def move_with_failure(source: Path, destination: Path) -> None:
        is_sidecar = source.name.endswith(".manifest.json")
        if (locked_member == "database" and not is_sidecar) or (
            locked_member == "sidecar" and is_sidecar and "backups" in source.parts
        ):
            raise PermissionError("simulated Windows file lock")
        source.replace(destination)

    repository, service, pairs = await _migration_retention(tmp_path, move_file=move_with_failure)
    preview = await service.create_preview(
        actor_id="actor-a",
        actor_source="dashboard",
        target_batch_type="migration_backups",
    )

    with pytest.raises(RetentionError, match="rolled back safely"):
        await service.confirm(
            confirmation_token=preview["confirmation_token"],
            actor_id="actor-a",
            expected_revision=1,
        )

    assert pairs[-1][0].is_file()
    assert pairs[-1][1].is_file()
    batches = await repository.list_quarantine_batches()
    assert batches[-1].state is QuarantineBatchState.ROLLED_BACK
    trash_files = list((tmp_path / "storage" / "trash").rglob("*"))
    assert not any(path.is_file() for path in trash_files)


@pytest.mark.asyncio
async def test_restart_reconciles_partial_move_and_blocks_ambiguous_copies(
    tmp_path: Path,
) -> None:
    def interrupt_after_move(source: Path, destination: Path) -> None:
        for attempt in range(10):
            try:
                source.replace(destination)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                gc.collect()
                time.sleep(0.05 * (attempt + 1))
        if source.name.endswith(".sqlite3"):
            raise AbruptStop()

    repository, interrupted, pairs = await _migration_retention(
        tmp_path / "partial", move_file=interrupt_after_move
    )
    preview = await interrupted.create_preview(
        actor_id="actor-a",
        actor_source="dashboard",
        target_batch_type="migration_backups",
    )
    gc.collect()
    with pytest.raises(AbruptStop):
        await interrupted.confirm(
            confirmation_token=preview["confirmation_token"],
            actor_id="actor-a",
            expected_revision=1,
        )
    recovered = RetentionService(
        repository,
        interrupted._inventory,
        interrupted._roots,
        process_instance_id="process-a",
        clock=ControlledClock(),
    )
    outcome = await recovered.reconcile_interrupted_batches()
    assert outcome["rolled_back"] == 1
    assert pairs[-1][0].is_file()
    assert pairs[-1][1].is_file()

    def duplicate_then_interrupt(source: Path, destination: Path) -> None:
        destination.write_bytes(source.read_bytes())
        raise AbruptStop()

    repository2, ambiguous, pairs2 = await _migration_retention(
        tmp_path / "ambiguous", move_file=duplicate_then_interrupt
    )
    preview2 = await ambiguous.create_preview(
        actor_id="actor-a",
        actor_source="dashboard",
        target_batch_type="migration_backups",
    )
    with pytest.raises(AbruptStop):
        await ambiguous.confirm(
            confirmation_token=preview2["confirmation_token"],
            actor_id="actor-a",
            expected_revision=1,
        )
    destination = next(
        path for path in (tmp_path / "ambiguous" / "storage" / "trash").rglob("*.sqlite3")
    )
    blocked = RetentionService(
        repository2,
        ambiguous._inventory,
        ambiguous._roots,
        process_instance_id="process-a",
        clock=ControlledClock(),
    )
    outcome = await blocked.reconcile_interrupted_batches()
    assert outcome["blocked"] == 1
    assert pairs2[-1][0].is_file()
    assert destination.is_file()
    batches = await repository2.list_quarantine_batches()
    assert batches[-1].state is QuarantineBatchState.BLOCKED


@pytest.mark.asyncio
async def test_concurrent_confirmation_moves_at_most_once(tmp_path: Path) -> None:
    _repository, service, _pairs = await _migration_retention(tmp_path)
    preview = await service.create_preview(
        actor_id="actor-a",
        actor_source="dashboard",
        target_batch_type="migration_backups",
    )

    outcomes = await asyncio.gather(
        service.confirm(
            confirmation_token=preview["confirmation_token"],
            actor_id="actor-a",
            expected_revision=1,
        ),
        service.confirm(
            confirmation_token=preview["confirmation_token"],
            actor_id="actor-a",
            expected_revision=1,
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(item, dict) for item in outcomes) == 1
    assert sum(isinstance(item, RetentionError) for item in outcomes) == 1


@pytest.mark.asyncio
async def test_configured_user_retention_works_after_user_row_is_absent(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "privacy-retention.sqlite3")
    await repository.initialize()
    path, artifact = _privacy_artifact(tmp_path)
    await repository.upsert_managed_artifact(artifact, now=NOW)
    roots = ManagedRootRegistry(tmp_path)
    service = RetentionService(
        repository,
        ArtifactInventoryService(repository, adapters=()),
        roots,
        process_instance_id="process-a",
        clock=ControlledClock(),
        policy=RetentionPolicy(
            rules=(RetentionTypePolicy(ManagedArtifactType.PRIVACY_EXPORT, 30),)
        ),
    )
    preview = await service.create_preview(
        actor_id="owner-command",
        actor_source="owner_qq",
        target_batch_type="privacy_exports",
        owner_scope=ArtifactOwnerScope.USER,
        owner_qq=USER_QQ,
    )

    result = await service.confirm(
        confirmation_token=preview["confirmation_token"],
        actor_id="owner-command",
        expected_revision=1,
    )

    assert result["status"] == "quarantined"
    assert not path.exists()
    assert list((tmp_path / "storage" / "trash").rglob("old-export.json"))


def test_legacy_cleanup_api_uses_durable_generalized_quarantine(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    repository = SQLiteRepository(database_path)
    asyncio.run(repository.initialize())
    root = tmp_path / "storage" / "backups" / "migrations"
    for index, created_at in enumerate(
        (NOW, NOW - timedelta(days=1), NOW - timedelta(days=2), NOW - timedelta(days=100))
    ):
        _write_backup(root, index=index, created_at=created_at)
    app = create_app(
        Settings(
            project_root=tmp_path,
            database_path=database_path,
            admin_access_token="dashboard-token",
        ),
        process_instance_id="process-api",
    )

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        preflight = client.get("/api/v1/system/preflight", headers=headers).json()
        token = preflight["backup_health"]["retention"]["cleanup_token"]
        response = client.post(
            "/api/v1/system/backups/cleanup",
            headers=headers,
            json={"cleanup_token": token},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "quarantined"
    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute("SELECT state FROM quarantine_batches").fetchone()[0]
            == "quarantined"
        )
