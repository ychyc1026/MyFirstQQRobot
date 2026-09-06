"""Local privacy export and deletion jobs with preview and backup safeguards."""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ych_bot.infrastructure.database import SQLiteRepository


class PrivacyJobError(RuntimeError):
    """A privacy job cannot be safely executed in its current state."""


@dataclass(frozen=True, slots=True)
class PrivacyJobResult:
    request_id: str
    status: str
    details: dict[str, Any]


class PrivacyJobService:
    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        project_root: Path,
        enabled: bool,
    ) -> None:
        self._repository = repository
        self._project_root = project_root.resolve()
        self._enabled = enabled
        self._export_dir = self._project_root / "storage" / "exports"
        self._backup_dir = self._project_root / "storage" / "privacy-backups"

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def preview(self, request_id: str) -> dict[str, Any]:
        request = await self._repository.privacy_request(request_id)
        if request is None:
            raise PrivacyJobError("privacy request not found")
        if request["request_kind"] != "delete":
            return {
                "request_id": request_id,
                "request_kind": request["request_kind"],
                "impact": None,
                "note": "export does not delete stored records",
            }
        impact = await self._repository.privacy_delete_impact(request["user_qq"])
        await self._repository.store_privacy_delete_preview(request_id, impact)
        return {
            "request_id": request_id,
            "request_kind": "delete",
            "impact": impact,
            "backup_required": True,
        }

    async def execute(self, request_id: str) -> PrivacyJobResult:
        if not self._enabled:
            raise PrivacyJobError("privacy jobs are disabled by configuration")
        request = await self._repository.claim_privacy_request(request_id)
        if request is None:
            raise PrivacyJobError("privacy request is missing or not pending")

        try:
            kind = request["request_kind"]
            if kind == "export":
                snapshot = await self._repository.privacy_export_snapshot(request["user_qq"])
                details = await self._export(
                    request,
                    artifact_type="export",
                    snapshot=snapshot,
                )
                file_archive = await self._archive_imported_files(
                    request,
                    snapshot,
                    artifact_type="export_file_archive",
                )
                if file_archive is not None:
                    details["file_archive"] = file_archive
            elif kind == "delete":
                await self.preview(request_id)
                snapshot = await self._repository.privacy_export_snapshot(request["user_qq"])
                backup = await self._export(
                    request,
                    artifact_type="pre_delete_backup",
                    snapshot=snapshot,
                )
                file_archive = await self._archive_imported_files(
                    request,
                    snapshot,
                    artifact_type="pre_delete_file_archive",
                )
                deleted = await self._repository.delete_user_data(
                    request["user_qq"],
                    request_id=request_id,
                )
                removed_files = self._remove_imported_files(snapshot)
                details = {
                    "backup": backup,
                    "file_archive": file_archive,
                    "deleted_counts": deleted,
                    "removed_import_files": removed_files,
                }
            else:
                raise PrivacyJobError(f"unsupported privacy request kind: {kind}")
            await self._repository.finish_privacy_request(
                request_id,
                status="completed",
                result=details,
            )
            return PrivacyJobResult(request_id=request_id, status="completed", details=details)
        except Exception as exc:
            await self._repository.finish_privacy_request(
                request_id,
                status="failed",
                result={"error": type(exc).__name__, "message": str(exc)},
            )
            raise

    async def verify_artifact(self, request_id: str) -> dict[str, Any]:
        request = await self._repository.privacy_request(request_id)
        if request is None:
            raise PrivacyJobError("privacy request not found")
        artifacts = await self._repository.privacy_artifacts(request_id)
        if not artifacts:
            raise PrivacyJobError("privacy artifact not found")
        results = [self._verify_single_artifact(request_id, artifact) for artifact in artifacts]
        failed = next((item for item in results if not item["verified"]), None)
        primary = results[0]
        primary["verified"] = failed is None
        primary["reason"] = failed["reason"] if failed else "verified"
        primary["artifacts"] = results
        has_file_archive = any(
            item["artifact"]["artifact_type"].endswith("file_archive") and item["verified"]
            for item in results
        )
        blockers = [
            "relational IDs may conflict with records created after deletion",
            "privacy tombstones and deletion audit records are intentionally irreversible",
        ]
        if not has_file_archive:
            blockers.insert(0, "imported file binaries are not present in a verified archive")
        primary["recovery"] = {
            "automatic_restore_supported": False,
            "mode": "manual_selective_reconstruction",
            "requires_new_owner_approval": True,
            "blockers": blockers,
        }
        return primary

    def _verify_single_artifact(
        self,
        request_id: str,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        relative = Path(artifact["path"])
        if relative.is_absolute():
            raise PrivacyJobError("privacy artifact path must be relative")
        candidate = (self._project_root / relative).resolve()
        expected_root = (
            self._backup_dir.resolve()
            if artifact["artifact_type"].startswith("pre_delete")
            else self._export_dir.resolve()
        )
        if candidate != expected_root and expected_root not in candidate.parents:
            raise PrivacyJobError("privacy artifact escaped its storage directory")
        if not candidate.is_file():
            return self._artifact_verification_result(
                request_id=request_id,
                artifact=artifact,
                verified=False,
                reason="artifact_missing",
            )

        digest = hashlib.sha256()
        byte_count = 0
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                byte_count += len(chunk)
        if byte_count != int(artifact["byte_count"]):
            return self._artifact_verification_result(
                request_id=request_id,
                artifact=artifact,
                verified=False,
                reason="byte_count_mismatch",
            )
        if digest.hexdigest() != artifact["sha256"]:
            return self._artifact_verification_result(
                request_id=request_id,
                artifact=artifact,
                verified=False,
                reason="sha256_mismatch",
            )
        if artifact["artifact_type"].endswith("file_archive"):
            return self._verify_file_archive(request_id, artifact, candidate)
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._artifact_verification_result(
                request_id=request_id,
                artifact=artifact,
                verified=False,
                reason="invalid_utf8_json",
            )
        if payload.get("format") != "ych-privacy-bundle-v1":
            return self._artifact_verification_result(
                request_id=request_id,
                artifact=artifact,
                verified=False,
                reason="unsupported_bundle_format",
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            return self._artifact_verification_result(
                request_id=request_id,
                artifact=artifact,
                verified=False,
                reason="invalid_bundle_data",
            )
        row_count = sum(len(value) for value in data.values() if isinstance(value, list))
        result = self._artifact_verification_result(
            request_id=request_id,
            artifact=artifact,
            verified=True,
            reason="verified",
        )
        result["bundle"] = {
            "format": payload["format"],
            "generated_at": payload.get("generated_at"),
            "data_classes": len(data),
            "row_count": row_count,
        }
        return result

    def _verify_file_archive(
        self,
        request_id: str,
        artifact: dict[str, Any],
        candidate: Path,
    ) -> dict[str, Any]:
        try:
            with zipfile.ZipFile(candidate, "r") as archive:
                if archive.testzip() is not None:
                    raise ValueError("zip CRC verification failed")
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                if manifest.get("format") != "ych-private-file-archive-v1":
                    raise ValueError("unsupported archive manifest")
                files = manifest.get("files")
                if not isinstance(files, list):
                    raise ValueError("invalid archive file list")
                total_bytes = 0
                for record in files:
                    if not isinstance(record, dict):
                        raise ValueError("invalid archive record")
                    archive_path = str(record.get("archive_path") or "")
                    pure_path = Path(archive_path)
                    if not archive_path or pure_path.is_absolute() or ".." in pure_path.parts:
                        raise ValueError("unsafe archive member path")
                    digest = hashlib.sha256()
                    byte_count = 0
                    with archive.open(archive_path, "r") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                            byte_count += len(chunk)
                    if byte_count != int(record.get("byte_count", -1)):
                        raise ValueError("archived file size mismatch")
                    if digest.hexdigest() != record.get("sha256"):
                        raise ValueError("archived file hash mismatch")
                    total_bytes += byte_count
        except (
            KeyError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            zipfile.BadZipFile,
            ValueError,
        ):
            return self._artifact_verification_result(
                request_id=request_id,
                artifact=artifact,
                verified=False,
                reason="invalid_file_archive",
            )
        result = self._artifact_verification_result(
            request_id=request_id,
            artifact=artifact,
            verified=True,
            reason="verified",
        )
        result["file_archive"] = {
            "format": manifest["format"],
            "file_count": len(files),
            "source_bytes": total_bytes,
        }
        return result

    @staticmethod
    def _artifact_verification_result(
        *,
        request_id: str,
        artifact: dict[str, Any],
        verified: bool,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "verified": verified,
            "reason": reason,
            "artifact": artifact,
            "recovery": {
                "automatic_restore_supported": False,
                "mode": "manual_selective_reconstruction",
                "requires_new_owner_approval": True,
                "blockers": [
                    "relational IDs may conflict with records created after deletion",
                    "privacy tombstones and deletion audit records are intentionally irreversible",
                ],
            },
        }

    async def _export(
        self,
        request: dict[str, Any],
        *,
        artifact_type: str,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if snapshot is None:
            snapshot = await self._repository.privacy_export_snapshot(request["user_qq"])
        directory = self._backup_dir if artifact_type == "pre_delete_backup" else self._export_dir
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{request['id']}.json"
        payload = {
            "format": "ych-privacy-bundle-v1",
            "request_id": request["id"],
            "request_kind": request["request_kind"],
            "generated_at": datetime.now(UTC).isoformat(),
            "data": snapshot,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(encoded)
        temporary.replace(destination)
        digest = hashlib.sha256(encoded).hexdigest()
        relative_path = destination.relative_to(self._project_root).as_posix()
        await self._repository.store_privacy_artifact(
            request_id=request["id"],
            owner_qq=request["user_qq"],
            artifact_type=artifact_type,
            path=relative_path,
            sha256=digest,
            byte_count=len(encoded),
        )
        return {
            "artifact_type": artifact_type,
            "path": relative_path,
            "sha256": digest,
            "byte_count": len(encoded),
        }

    async def _archive_imported_files(
        self,
        request: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        artifact_type: str,
    ) -> dict[str, Any] | None:
        blobs = snapshot.get("document_blobs", [])
        if not blobs:
            return None
        documents = {str(item["id"]): item for item in snapshot.get("knowledge_documents", [])}
        imports_root = (self._project_root / "storage" / "imports").resolve()
        records: list[dict[str, Any]] = []
        sources: list[tuple[Path, str]] = []
        for blob in blobs:
            document_id = str(blob["document_id"])
            document = documents.get(document_id)
            if document is None:
                raise PrivacyJobError("document blob has no matching knowledge document")
            relative = Path(str(blob["storage_path"]))
            if relative.is_absolute():
                raise PrivacyJobError("imported document path must be relative")
            candidate = (self._project_root / relative).resolve()
            if imports_root not in candidate.parents or not candidate.is_file():
                raise PrivacyJobError("tracked imported document is missing or outside storage")
            digest = hashlib.sha256()
            byte_count = 0
            with candidate.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    byte_count += len(chunk)
            expected_size = int(blob["byte_count"])
            expected_hash = str(document["content_sha256"])
            if byte_count != expected_size or digest.hexdigest() != expected_hash:
                raise PrivacyJobError("tracked imported document failed integrity verification")
            archive_path = f"files/{document_id}/{candidate.name}"
            records.append(
                {
                    "document_id": document_id,
                    "original_filename": document["original_filename"],
                    "media_type": blob["media_type"],
                    "detected_format": blob["detected_format"],
                    "storage_path": str(blob["storage_path"]),
                    "archive_path": archive_path,
                    "byte_count": byte_count,
                    "sha256": expected_hash,
                }
            )
            sources.append((candidate, archive_path))

        directory = self._backup_dir if artifact_type.startswith("pre_delete") else self._export_dir
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{request['id']}-files.zip"
        temporary = destination.with_suffix(".tmp")
        manifest = {
            "format": "ych-private-file-archive-v1",
            "request_id": request["id"],
            "generated_at": datetime.now(UTC).isoformat(),
            "files": records,
        }
        try:
            with zipfile.ZipFile(
                temporary,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                )
                for source, archive_path in sources:
                    archive.write(source, archive_path)
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
        relative_path = destination.relative_to(self._project_root).as_posix()
        await self._repository.store_privacy_artifact(
            request_id=request["id"],
            owner_qq=request["user_qq"],
            artifact_type=artifact_type,
            path=relative_path,
            sha256=digest.hexdigest(),
            byte_count=byte_count,
        )
        return {
            "artifact_type": artifact_type,
            "path": relative_path,
            "sha256": digest.hexdigest(),
            "byte_count": byte_count,
            "file_count": len(records),
            "source_bytes": sum(item["byte_count"] for item in records),
        }

    def _remove_imported_files(self, snapshot: dict[str, Any]) -> int:
        imports_root = (self._project_root / "storage" / "imports").resolve()
        removed = 0
        for blob in snapshot.get("document_blobs", []):
            candidate = (self._project_root / blob["storage_path"]).resolve()
            if imports_root not in candidate.parents:
                continue
            if candidate.is_file():
                candidate.unlink()
                removed += 1
            parent = candidate.parent
            if parent != imports_root and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        return removed
