import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from ych_bot.application import (
    ArtifactInventoryService,
    ImportedSourceAdapter,
    ManagedRootRegistry,
    MigrationBackupAdapter,
    PathConfinementError,
    PrivacyArtifactAdapter,
    PrivacyJobService,
    QuarantineInventoryAdapter,
)
from ych_bot.domain.managed_artifacts import (
    ArtifactOwnerScope,
    ArtifactReferenceState,
    ArtifactRetentionState,
    ArtifactVerificationState,
    ManagedArtifact,
    ManagedArtifactType,
)
from ych_bot.infrastructure.database import SQLiteRepository

NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
USER_A = "10000001"
USER_B = "10000002"
OWNER_QQ = "2000000001"


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _filesystem_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _digest(path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }


def _migration_backup(root: Path, *, stamp: str, created_at: datetime) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    stem = f"ych-before-v31-{stamp}"
    backup = root / f"{stem}.sqlite3"
    with sqlite3.connect(backup) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence VALUES('verified')")
    content = backup.read_bytes()
    manifest = root / f"{stem}.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "ych-migration-backup-v1",
                "backup_file": backup.name,
                "sha256": _digest(content),
                "byte_count": len(content),
                "source_schema_version": 30,
                "target_schema_version": 31,
                "created_at": created_at.isoformat(),
                "integrity_result": "ok",
            }
        ),
        encoding="utf-8",
    )
    return backup, manifest


async def _privacy_request(
    repository: SQLiteRepository, user_qq: str, *, kind: str = "export"
) -> str:
    request_id = str(uuid4())
    await repository.create_privacy_request(
        request_id=request_id,
        user_qq=user_qq,
        request_kind=kind,
        requested_by=OWNER_QQ,
        reason="inventory test",
    )
    return request_id


@pytest.mark.asyncio
async def test_managed_roots_reject_escape_and_resolve_only_registered_ids(
    tmp_path: Path,
) -> None:
    imports = tmp_path / "storage" / "imports"
    imports.mkdir(parents=True)
    source = imports / "safe.txt"
    source.write_text("safe", encoding="utf-8")
    roots = ManagedRootRegistry(tmp_path)

    assert roots.validate("imports", "storage/imports/safe.txt") == source
    for unsafe in (
        "storage/imports/../outside.txt",
        "../storage/imports/safe.txt",
        "C:/outside.txt",
        "/storage/imports/safe.txt",
    ):
        with pytest.raises(PathConfinementError):
            roots.validate("imports", unsafe, require_exists=False)

    repository = SQLiteRepository(tmp_path / "inventory.sqlite3")
    await repository.initialize()
    artifact = ManagedArtifact(
        artifact_id="registered-artifact",
        artifact_type=ManagedArtifactType.IMPORTED_SOURCE,
        owner_scope=ArtifactOwnerScope.USER,
        owner_qq=USER_A,
        relative_path="storage/imports/safe.txt",
        bundle_key="safe",
        size_bytes=4,
        digest_sha256=_digest(b"safe"),
        manifest_type="imported-source:text",
        created_at=NOW,
        verification_state=ArtifactVerificationState.VERIFIED,
        reference_state=ArtifactReferenceState.UNREFERENCED,
        retention_state=ArtifactRetentionState.RETAIN,
    )
    await repository.upsert_managed_artifact(artifact, now=NOW)
    assert await roots.resolve_artifact(repository, artifact.artifact_id) == source
    with pytest.raises(PathConfinementError, match="unknown"):
        await roots.resolve_artifact(repository, "storage/imports/safe.txt")

    metadata_failure = ManagedRootRegistry(
        tmp_path,
        reparse_detector=lambda _path: (_ for _ in ()).throw(OSError("denied")),
    )
    with pytest.raises(PathConfinementError, match="metadata"):
        metadata_failure.validate("imports", "storage/imports/safe.txt")

    external = tmp_path.parent / f"outside-{uuid4()}"
    external.mkdir()
    link = imports / "escape-link"
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError:
        pass
    else:
        (external / "secret.txt").write_text("private", encoding="utf-8")
        with pytest.raises(PathConfinementError):
            roots.validate("imports", "storage/imports/escape-link/secret.txt")


@pytest.mark.asyncio
async def test_migration_adapter_preserves_policy_and_blocks_bad_sidecars(
    tmp_path: Path,
) -> None:
    backup_root = tmp_path / "storage" / "backups" / "migrations"
    created = (NOW, NOW - timedelta(days=1), NOW - timedelta(days=2), NOW - timedelta(days=100))
    pairs = [
        _migration_backup(
            backup_root,
            stamp=f"202608{31 - index:02d}T080000000000Z",
            created_at=value,
        )
        for index, value in enumerate(created)
    ]
    adapter = MigrationBackupAdapter(ManagedRootRegistry(tmp_path), clock=lambda: NOW)

    observations = await adapter.discover()
    assert len(observations) == 4
    protected = [item for item in observations if item.references]
    assert len(protected) == 3
    assert all(item.retention_state is ArtifactRetentionState.RETAIN for item in protected)
    oldest = next(item for item in observations if item.created_at == created[-1])
    assert oldest.retention_state is ArtifactRetentionState.CANDIDATE
    assert oldest.verification_state is ArtifactVerificationState.VERIFIED

    pairs[0][0].write_bytes(b"tampered")
    missing_manifest = backup_root / "missing.manifest.json"
    missing_manifest.write_text(
        json.dumps(
            {
                "format": "ych-migration-backup-v1",
                "backup_file": "missing.sqlite3",
                "sha256": "absent",
                "byte_count": 1,
                "created_at": NOW.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    orphan = backup_root / "orphan.sqlite3"
    orphan.write_bytes(b"not adopted without a sidecar")
    bad_manifest = backup_root / "invalid.manifest.json"
    bad_manifest.write_text("{broken", encoding="utf-8")

    observations = await adapter.discover()
    by_bundle = {item.bundle_key: item for item in observations}
    assert by_bundle[pairs[0][1].name.removesuffix(".manifest.json")].verification_state is (
        ArtifactVerificationState.INVALID
    )
    assert by_bundle["missing"].verification_state is ArtifactVerificationState.MISSING
    assert by_bundle["orphan"].manifest_type == "missing-migration-manifest"
    assert by_bundle["orphan"].retention_state is ArtifactRetentionState.BLOCKED
    assert by_bundle["invalid"].verification_state is ArtifactVerificationState.INVALID


@pytest.mark.asyncio
async def test_privacy_adapter_verifies_zip_and_isolates_users(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "privacy.sqlite3")
    await repository.initialize()
    document_id = str(uuid4())
    job_id = str(uuid4())
    private_content = b"private source that must not enter inventory DTOs"
    source = tmp_path / "storage" / "imports" / "knowledge" / document_id / "note.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(private_content)
    await repository.register_knowledge_document(
        document_id=document_id,
        job_id=job_id,
        user_qq=USER_A,
        purpose="user_understanding",
        original_filename="note.txt",
        content_sha256=_digest(private_content),
        byte_count=len(private_content),
        text_length=len(private_content),
        document_status="staged",
        job_status="queued",
        storage_path=source.relative_to(tmp_path).as_posix(),
        media_type="text/plain",
        detected_format="text",
        chunks=(private_content.decode(),),
        created_by=OWNER_QQ,
    )
    service = PrivacyJobService(repository, project_root=tmp_path, enabled=True)
    request_a = await _privacy_request(repository, USER_A)
    await service.execute(request_a)
    request_b = await _privacy_request(repository, USER_B)
    await service.execute(request_b)
    roots = ManagedRootRegistry(tmp_path)
    adapter = PrivacyArtifactAdapter(roots, repository)

    observations = await adapter.discover()
    user_a = [item for item in observations if item.owner_qq == USER_A]
    assert len(user_a) == 2
    assert all(item.verification_state is ArtifactVerificationState.VERIFIED for item in user_a)
    assert {item.owner_qq for item in observations} == {USER_A, USER_B}

    inventory = ArtifactInventoryService(repository, adapters=(adapter,), clock=lambda: NOW)
    result = await inventory.reconcile()
    assert private_content.decode() not in json.dumps(result)
    assert (
        len(
            await repository.list_managed_artifacts(
                owner_scope=ArtifactOwnerScope.USER,
                owner_qq=USER_A,
            )
        )
        == 2
    )
    assert (
        len(
            await repository.list_managed_artifacts(
                owner_scope=ArtifactOwnerScope.USER,
                owner_qq=USER_B,
            )
        )
        == 1
    )

    archive_record = next(
        item
        for item in await repository.privacy_artifacts(request_a)
        if item["artifact_type"] == "export_file_archive"
    )
    archive = tmp_path / archive_record["path"]
    archive.write_bytes(b"not a zip")
    await repository.store_privacy_artifact(
        request_id=request_a,
        owner_qq=USER_A,
        artifact_type="export_file_archive",
        path=archive_record["path"],
        sha256=_digest(archive.read_bytes()),
        byte_count=archive.stat().st_size,
    )
    broken = next(
        item
        for item in await adapter.discover()
        if item.owner_qq == USER_A and item.relative_path == archive_record["path"]
    )
    assert broken.verification_state is ArtifactVerificationState.INVALID
    assert broken.safe_reason == "invalid_zip"


@pytest.mark.asyncio
async def test_deletion_backup_keeps_explicit_owner_after_user_pseudonymization(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "privacy-delete.sqlite3")
    await repository.initialize()
    service = PrivacyJobService(repository, project_root=tmp_path, enabled=True)
    request_id = await _privacy_request(repository, USER_A, kind="delete")

    await service.execute(request_id)

    request = await repository.privacy_request(request_id)
    assert request is not None
    assert request["user_qq"].startswith("deleted:")
    observations = await PrivacyArtifactAdapter(
        ManagedRootRegistry(tmp_path), repository
    ).discover()
    assert len(observations) == 1
    assert observations[0].artifact_type is ManagedArtifactType.PRIVACY_DELETION_BACKUP
    assert observations[0].owner_scope is ArtifactOwnerScope.USER
    assert observations[0].owner_qq == USER_A
    assert observations[0].verification_state is ArtifactVerificationState.VERIFIED


@pytest.mark.asyncio
async def test_import_inventory_excludes_unrelated_files_and_reconciles_idempotently(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "imports.sqlite3")
    await repository.initialize()
    tracked = tmp_path / "storage" / "imports" / "knowledge" / "doc-a" / "source.txt"
    tracked.parent.mkdir(parents=True)
    tracked.write_bytes(b"tracked")
    unrelated = tmp_path / "storage" / "imports" / "unrelated.txt"
    unrelated.write_bytes(b"never adopt me")
    await repository.register_knowledge_document(
        document_id="doc-a",
        job_id="job-a",
        user_qq=USER_A,
        purpose="user_understanding",
        original_filename="source.txt",
        content_sha256=_digest(b"tracked"),
        byte_count=len(b"tracked"),
        text_length=7,
        document_status="staged",
        job_status="queued",
        storage_path=tracked.relative_to(tmp_path).as_posix(),
        media_type="text/plain",
        detected_format="text",
        chunks=("tracked",),
        created_by=OWNER_QQ,
    )
    outside = tmp_path / "storage" / "outside.txt"
    outside.write_bytes(b"outside")
    await repository.register_knowledge_document(
        document_id="doc-escape",
        job_id="job-escape",
        user_qq=USER_A,
        purpose="user_understanding",
        original_filename="outside.txt",
        content_sha256=_digest(b"outside"),
        byte_count=len(b"outside"),
        text_length=7,
        document_status="staged",
        job_status="queued",
        storage_path="storage/imports/../outside.txt",
        media_type="text/plain",
        detected_format="text",
        chunks=("outside",),
        created_by=OWNER_QQ,
    )
    adapter = ImportedSourceAdapter(ManagedRootRegistry(tmp_path), repository)
    discovered = await adapter.discover()
    assert len(discovered) == 2
    assert unrelated.relative_to(tmp_path).as_posix() not in {
        item.relative_path for item in discovered
    }
    anomaly = next(
        item
        for item in discovered
        if item.verification_state is ArtifactVerificationState.ANOMALOUS
    )
    assert ".." not in anomaly.relative_path
    assert anomaly.retention_state is ArtifactRetentionState.BLOCKED

    inventory = ArtifactInventoryService(repository, adapters=(adapter,), clock=lambda: NOW)
    before = _filesystem_snapshot(tmp_path / "storage")
    first = await inventory.reconcile()
    after_first = _filesystem_snapshot(tmp_path / "storage")
    artifacts_first = await repository.all_managed_artifacts()
    second = await inventory.reconcile()
    artifacts_second = await repository.all_managed_artifacts()

    assert before == after_first
    assert first["filesystem_mutated"] is False
    assert first["created"] == 2
    assert second["unchanged"] == 2
    assert artifacts_first == artifacts_second
    tracked_artifact = next(item for item in artifacts_first if item.bundle_key == "doc-a")
    assert tracked_artifact.reference_state is ArtifactReferenceState.PROTECTED

    tracked.unlink()
    missing = await inventory.reconcile()
    missing_artifact = await repository.managed_artifact(tracked_artifact.artifact_id)
    assert missing["updated"] == 1
    assert missing_artifact is not None
    assert missing_artifact.verification_state is ArtifactVerificationState.MISSING
    missing_revision = missing_artifact.revision
    repeated = await inventory.reconcile()
    repeated_artifact = await repository.managed_artifact(tracked_artifact.artifact_id)
    assert repeated["unchanged"] == 2
    assert repeated_artifact is not None
    assert repeated_artifact.revision == missing_revision


class _SourceRepository:
    def __init__(self, records: dict[str, list[dict[str, object]]]) -> None:
        self.records = records

    async def managed_artifact_source_records(self) -> dict[str, list[dict[str, object]]]:
        return self.records


@pytest.mark.asyncio
async def test_quarantine_discovery_tracks_known_batches_only(tmp_path: Path) -> None:
    trash = tmp_path / "storage" / "trash"
    managed = trash / "batch-a" / "item.bin"
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"managed quarantine")
    unrelated = trash / "random.bin"
    unrelated.write_bytes(b"unrelated")
    legacy_root = trash / "migration-backups" / "legacy-batch"
    legacy_root.mkdir(parents=True)
    legacy = legacy_root / "legacy.sqlite3"
    legacy.write_bytes(b"legacy quarantine")
    (legacy_root / "legacy.manifest.json").write_text(
        json.dumps(
            {
                "format": "ych-migration-backup-v1",
                "backup_file": legacy.name,
                "sha256": _digest(legacy.read_bytes()),
                "byte_count": legacy.stat().st_size,
                "created_at": NOW.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    records: dict[str, list[dict[str, object]]] = {
        "privacy": [],
        "imports": [],
        "quarantine": [
            {
                "batch_id": "batch-a",
                "batch_state": "moving",
                "owner_scope": "user",
                "owner_qq": USER_A,
                "updated_at": NOW.isoformat(),
                "artifact_id": "source-artifact",
                "sequence": 1,
                "source_relative_path": "storage/exports/source.json",
                "quarantine_relative_path": managed.relative_to(tmp_path).as_posix(),
                "expected_digest_sha256": _digest(managed.read_bytes()),
                "item_state": "moved",
            }
        ],
    }
    adapter = QuarantineInventoryAdapter(ManagedRootRegistry(tmp_path), _SourceRepository(records))

    observations = await adapter.discover()

    assert len(observations) == 2
    assert unrelated.relative_to(tmp_path).as_posix() not in {
        item.relative_path for item in observations
    }
    database_item = next(item for item in observations if item.bundle_key == "batch-a")
    assert database_item.verification_state is ArtifactVerificationState.VERIFIED
    assert database_item.references
    assert database_item.references[0].state is ArtifactReferenceState.PROTECTED
    assert any(item.manifest_type == "legacy-migration-quarantine-v1" for item in observations)
