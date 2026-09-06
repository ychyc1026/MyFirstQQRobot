"""Read-only database preflight and mandatory pre-migration backup."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ych_bot.infrastructure.database.sqlite import LATEST_SCHEMA_VERSION


class DatabasePreflightError(RuntimeError):
    """The database cannot be migrated without risking user data."""


class DatabasePreflightService:
    def __init__(
        self,
        *,
        database_path: Path,
        project_root: Path,
        backup_enabled: bool,
    ) -> None:
        self._database_path = database_path.resolve()
        self._project_root = project_root.resolve()
        self._backup_enabled = backup_enabled
        self._backup_root = self._project_root / "storage" / "backups" / "migrations"
        self._startup_backup: dict[str, Any] | None = None
        self._cleanup_secret = secrets.token_bytes(32)

    async def inspect(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._inspect_sync)

    async def ensure_backup_before_migration(self) -> dict[str, Any]:
        inspection = await self.inspect()
        if not inspection["database_exists"] or not inspection["needs_migration"]:
            return inspection
        if not inspection["integrity_ok"]:
            raise DatabasePreflightError("database integrity check failed before migration")
        if not self._backup_enabled:
            raise DatabasePreflightError(
                "database migration requires a verified backup; migration backup is disabled"
            )
        self._startup_backup = await asyncio.to_thread(
            self._create_backup_sync,
            inspection["current_schema_version"],
        )
        return {**inspection, "startup_backup": self._startup_backup}

    async def quarantine_retention_candidates(self, cleanup_token: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._quarantine_retention_candidates_sync, cleanup_token)

    async def retention_candidates_for_token(self, cleanup_token: str) -> list[dict[str, Any]]:
        """Validate the legacy preview token without changing the filesystem."""
        return await asyncio.to_thread(self._retention_candidates_for_token_sync, cleanup_token)

    def _inspect_sync(self) -> dict[str, Any]:
        backup_health = self._backup_health()
        base: dict[str, Any] = {
            "database_exists": self._database_path.is_file(),
            "database_name": self._database_path.name,
            "latest_schema_version": LATEST_SCHEMA_VERSION,
            "backup_enabled": self._backup_enabled,
            "startup_backup": self._startup_backup,
            "backup_health": backup_health,
        }
        if not base["database_exists"]:
            return {
                **base,
                "database_bytes": 0,
                "modified_at": None,
                "current_schema_version": 0,
                "applied_versions": [],
                "needs_migration": False,
                "backup_required": False,
                "integrity_ok": True,
                "integrity_result": "new_database",
                "foreign_key_violations": 0,
                "journal_mode": None,
                "ready": True,
                "startup_action": "create_new_database",
                "latest_backup": self._latest_backup_manifest(),
            }
        stat = self._database_path.stat()
        try:
            with closing(self._read_only_connection()) as connection:
                integrity_row = connection.execute("PRAGMA quick_check").fetchone()
                integrity_result = str(integrity_row[0]) if integrity_row else "missing_result"
                foreign_key_violations = len(
                    connection.execute("PRAGMA foreign_key_check").fetchall()
                )
                journal_row = connection.execute("PRAGMA journal_mode").fetchone()
                journal_mode = str(journal_row[0]) if journal_row else None
                migration_table = connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'schema_migrations'
                    """
                ).fetchone()
                versions = (
                    [
                        int(row[0])
                        for row in connection.execute(
                            "SELECT version FROM schema_migrations ORDER BY version"
                        ).fetchall()
                    ]
                    if migration_table
                    else []
                )
        except sqlite3.DatabaseError as exc:
            return {
                **base,
                "database_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                "current_schema_version": 0,
                "applied_versions": [],
                "needs_migration": True,
                "backup_required": True,
                "integrity_ok": False,
                "integrity_result": type(exc).__name__,
                "foreign_key_violations": None,
                "journal_mode": None,
                "ready": False,
                "startup_action": "blocked",
                "latest_backup": self._latest_backup_manifest(),
            }
        current = max(versions, default=0)
        integrity_ok = integrity_result == "ok" and foreign_key_violations == 0
        needs_migration = current < LATEST_SCHEMA_VERSION
        return {
            **base,
            "database_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            "current_schema_version": current,
            "applied_versions": versions,
            "needs_migration": needs_migration,
            "backup_required": needs_migration,
            "integrity_ok": integrity_ok,
            "integrity_result": integrity_result,
            "foreign_key_violations": foreign_key_violations,
            "journal_mode": journal_mode,
            "ready": integrity_ok and (not needs_migration or self._backup_enabled),
            "startup_action": "backup_then_migrate" if needs_migration else "no_migration",
            "latest_backup": self._latest_backup_manifest(),
        }

    def _create_backup_sync(self, current_schema_version: int) -> dict[str, Any]:
        self._backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        stem = f"ych-before-v{LATEST_SCHEMA_VERSION}-{stamp}"
        destination = self._backup_root / f"{stem}.sqlite3"
        temporary = self._backup_root / f".{stem}.tmp"
        try:
            with (
                closing(self._read_only_connection()) as source,
                closing(sqlite3.connect(temporary)) as target,
            ):
                source.backup(target)
                target.execute("PRAGMA journal_mode = DELETE")
                check = target.execute("PRAGMA quick_check").fetchone()
                if check is None or check[0] != "ok":
                    raise DatabasePreflightError("migration backup integrity check failed")
                target.commit()
            temporary.replace(destination)
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise
        digest = hashlib.sha256()
        byte_count = 0
        with destination.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                byte_count += len(chunk)
        manifest = {
            "format": "ych-migration-backup-v1",
            "backup_file": destination.name,
            "sha256": digest.hexdigest(),
            "byte_count": byte_count,
            "source_schema_version": current_schema_version,
            "target_schema_version": LATEST_SCHEMA_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "integrity_result": "ok",
        }
        manifest_path = self._backup_root / f"{stem}.manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return manifest

    def _latest_backup_manifest(self) -> dict[str, Any] | None:
        if not self._backup_root.is_dir():
            return None
        candidates = sorted(self._backup_root.glob("*.manifest.json"), reverse=True)
        for candidate in candidates:
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            backup_name = payload.get("backup_file")
            backup_path = self._backup_root / str(backup_name)
            if (
                payload.get("format") == "ych-migration-backup-v1"
                and backup_name
                and backup_path.parent == self._backup_root
                and backup_path.is_file()
            ):
                return payload
        return None

    def _backup_health(self) -> dict[str, Any]:
        """Reverify every managed migration backup and preview safe retention candidates."""
        policy = {"keep_latest": 3, "minimum_age_days": 90, "automatic_cleanup": False}
        if not self._backup_root.is_dir():
            return {
                "verified": True,
                "backup_count": 0,
                "verified_count": 0,
                "total_bytes": 0,
                "items": [],
                "retention": {**policy, "candidate_count": 0, "candidate_bytes": 0},
            }
        manifests = sorted(self._backup_root.glob("*.manifest.json"), reverse=True)
        cutoff = datetime.now(UTC) - timedelta(days=policy["minimum_age_days"])
        items: list[dict[str, Any]] = []
        for index, manifest_path in enumerate(manifests):
            result: dict[str, Any] = {
                "manifest_file": manifest_path.name,
                "verified": False,
                "reason": "invalid_manifest",
                "retention_candidate": False,
            }
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                backup_name = payload.get("backup_file")
                backup_path = self._backup_root / str(backup_name)
                created_at = datetime.fromisoformat(str(payload["created_at"]))
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                if (
                    payload.get("format") != "ych-migration-backup-v1"
                    or not backup_name
                    or backup_path.parent != self._backup_root
                    or not backup_path.is_file()
                ):
                    raise ValueError("missing_or_unsafe_backup")
                digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
                byte_count = backup_path.stat().st_size
                if digest != payload.get("sha256") or byte_count != payload.get("byte_count"):
                    raise ValueError("hash_or_size_mismatch")
                with closing(
                    sqlite3.connect(backup_path.as_uri() + "?mode=ro", uri=True)
                ) as connection:
                    connection.execute("PRAGMA query_only = ON")
                    check = connection.execute("PRAGMA quick_check").fetchone()
                if check is None or check[0] != "ok":
                    raise ValueError("integrity_check_failed")
                candidate = index >= policy["keep_latest"] and created_at < cutoff
                result.update(
                    {
                        "backup_file": backup_path.name,
                        "created_at": created_at.isoformat(),
                        "byte_count": byte_count,
                        "verified": True,
                        "reason": "ok",
                        "retention_candidate": candidate,
                    }
                )
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                result["reason"] = str(exc) or type(exc).__name__
            items.append(result)
        candidates = [item for item in items if item["retention_candidate"]]
        cleanup_token = self._cleanup_token(candidates) if candidates else None
        return {
            "verified": all(item["verified"] for item in items),
            "backup_count": len(items),
            "verified_count": sum(bool(item["verified"]) for item in items),
            "total_bytes": sum(int(item.get("byte_count", 0)) for item in items),
            "items": items,
            "retention": {
                **policy,
                "candidate_count": len(candidates),
                "candidate_bytes": sum(int(item["byte_count"]) for item in candidates),
                "cleanup_token": cleanup_token,
            },
        }

    def _cleanup_token(self, candidates: list[dict[str, Any]]) -> str:
        evidence = [
            [item["manifest_file"], item["backup_file"], item["byte_count"]] for item in candidates
        ]
        payload = json.dumps(evidence, separators=(",", ":"), sort_keys=True).encode()
        return hmac.new(self._cleanup_secret, payload, hashlib.sha256).hexdigest()

    def _quarantine_retention_candidates_sync(self, cleanup_token: str) -> dict[str, Any]:
        candidates = self._retention_candidates_for_token_sync(cleanup_token)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        quarantine = self._project_root / "storage" / "trash" / "migration-backups" / stamp
        quarantine.mkdir(parents=True, exist_ok=False)
        moved: list[tuple[Path, Path]] = []
        try:
            for item in candidates:
                for name in (item["backup_file"], item["manifest_file"]):
                    source = self._backup_root / name
                    destination = quarantine / name
                    source.replace(destination)
                    moved.append((source, destination))
        except Exception:
            for source, destination in reversed(moved):
                if destination.exists():
                    destination.replace(source)
            if quarantine.exists() and not any(quarantine.iterdir()):
                quarantine.rmdir()
            raise
        return {
            "status": "quarantined",
            "candidate_count": len(candidates),
            "candidate_bytes": sum(int(item["byte_count"]) for item in candidates),
            "files": [item["backup_file"] for item in candidates],
            "quarantine_id": stamp,
            "recoverable": True,
        }

    def _retention_candidates_for_token_sync(self, cleanup_token: str) -> list[dict[str, Any]]:
        health = self._backup_health()
        candidates = [item for item in health["items"] if item["retention_candidate"]]
        expected = self._cleanup_token(candidates) if candidates else ""
        if not candidates:
            raise DatabasePreflightError("no verified retention candidates")
        if not hmac.compare_digest(cleanup_token, expected):
            raise DatabasePreflightError("cleanup preview changed; request a new token")
        return candidates

    def _read_only_connection(self) -> sqlite3.Connection:
        uri = self._database_path.as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection
