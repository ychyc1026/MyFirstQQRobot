import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application import DatabasePreflightError, DatabasePreflightService
from ych_bot.config import Settings
from ych_bot.infrastructure.database import SQLiteRepository


def create_legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?, 'legacy')",
            ((version,) for version in range(1, 5)),
        )
        connection.execute("CREATE TABLE legacy_data(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_data(value) VALUES('must survive backup')")


@pytest.mark.asyncio
async def test_preflight_creates_verified_backup_before_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "storage" / "runtime" / "ych.sqlite3"
    database_path.parent.mkdir(parents=True)
    create_legacy_database(database_path)
    before = hashlib.sha256(database_path.read_bytes()).hexdigest()
    service = DatabasePreflightService(
        database_path=database_path,
        project_root=tmp_path,
        backup_enabled=True,
    )

    inspection = await service.ensure_backup_before_migration()

    assert inspection["current_schema_version"] == 4
    assert inspection["needs_migration"] is True
    assert inspection["integrity_ok"] is True
    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before
    backup = inspection["startup_backup"]
    backup_path = tmp_path / "storage" / "backups" / "migrations" / backup["backup_file"]
    assert backup_path.is_file()
    assert hashlib.sha256(backup_path.read_bytes()).hexdigest() == backup["sha256"]
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM legacy_data").fetchone()[0] == (
            "must survive backup"
        )
    manifest_path = backup_path.with_suffix(".manifest.json")
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["source_schema_version"] == 4

    health = (await service.inspect())["backup_health"]
    assert health["verified"] is True
    assert health["backup_count"] == 1
    assert health["verified_count"] == 1
    assert health["retention"]["candidate_count"] == 0


@pytest.mark.asyncio
async def test_preflight_blocks_migration_when_required_backup_is_disabled(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    create_legacy_database(database_path)
    service = DatabasePreflightService(
        database_path=database_path,
        project_root=tmp_path,
        backup_enabled=False,
    )

    with pytest.raises(DatabasePreflightError, match="requires a verified backup"):
        await service.ensure_backup_before_migration()

    with sqlite3.connect(database_path) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert versions == [(1,), (2,), (3,), (4,)]


def test_preflight_api_is_authenticated_and_reports_latest_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "ready.sqlite3"
    repository = SQLiteRepository(database_path)
    import asyncio

    asyncio.run(repository.initialize())
    app = create_app(
        Settings(
            project_root=tmp_path,
            database_path=database_path,
            admin_access_token="dashboard-token",
        )
    )

    with TestClient(app) as client:
        assert client.get("/api/v1/system/preflight").status_code == 401
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = client.get("/api/v1/system/preflight", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["current_schema_version"] == 34
    assert body["latest_schema_version"] == 34
    assert body["needs_migration"] is False
    assert body["integrity_ok"] is True
    assert body["backup_health"]["verified"] is True
    assert body["backup_health"]["retention"]["automatic_cleanup"] is False


@pytest.mark.asyncio
async def test_preflight_reports_tampered_backup_without_deleting_it(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    create_legacy_database(database_path)
    service = DatabasePreflightService(
        database_path=database_path,
        project_root=tmp_path,
        backup_enabled=True,
    )
    created = await service.ensure_backup_before_migration()
    backup = created["startup_backup"]
    backup_path = tmp_path / "storage" / "backups" / "migrations" / backup["backup_file"]
    backup_path.write_bytes(backup_path.read_bytes() + b"tampered")

    health = (await service.inspect())["backup_health"]

    assert health["verified"] is False
    assert health["verified_count"] == 0
    assert health["items"][0]["reason"] == "hash_or_size_mismatch"
    assert backup_path.is_file()


@pytest.mark.asyncio
async def test_retention_cleanup_requires_fresh_token_and_quarantines(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    create_legacy_database(database_path)
    service = DatabasePreflightService(
        database_path=database_path, project_root=tmp_path, backup_enabled=True
    )
    created = await service.ensure_backup_before_migration()
    root = tmp_path / "storage" / "backups" / "migrations"
    source = root / created["startup_backup"]["backup_file"]
    for index in range(4):
        backup = root / f"ych-before-v28-2020010{index}T000000000000Z.sqlite3"
        backup.write_bytes(source.read_bytes())
        manifest = {
            **created["startup_backup"],
            "backup_file": backup.name,
            "created_at": f"2020-01-0{index + 1}T00:00:00+00:00",
        }
        backup.with_suffix(".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    retention = (await service.inspect())["backup_health"]["retention"]
    assert retention["candidate_count"] == 2

    result = await service.quarantine_retention_candidates(retention["cleanup_token"])

    assert result["status"] == "quarantined"
    assert result["candidate_count"] == 2
    assert result["recoverable"] is True
    quarantine = tmp_path / "storage" / "trash" / "migration-backups" / result["quarantine_id"]
    assert len(list(quarantine.glob("*.sqlite3"))) == 2
    assert (await service.inspect())["backup_health"]["retention"]["candidate_count"] == 0
    with pytest.raises(DatabasePreflightError, match="no verified"):
        await service.quarantine_retention_candidates(retention["cleanup_token"])
