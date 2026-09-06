"""Safe local staging for long documents before model-backed processing."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from ych_bot.domain.knowledge import KnowledgeDraftError, validate_knowledge_draft
from ych_bot.domain.persona import DocumentPurpose
from ych_bot.infrastructure.database import SQLiteRepository

from .document_parsing import DocumentParseError, DocumentTextExtractor, PdfOcrEngine


class KnowledgeImportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class KnowledgeImportResult:
    document_id: str
    job_id: str
    status: str
    details: dict[str, object]


class KnowledgeImportService:
    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        project_root: Path,
        import_enabled: bool,
        processing_enabled: bool,
        max_megabytes: int,
        max_pages: int = 2000,
        ocr_enabled: bool = False,
        ocr_engine: PdfOcrEngine | None = None,
    ) -> None:
        self._repository = repository
        self._root = project_root.resolve()
        self._import_enabled = import_enabled
        self._processing_enabled = processing_enabled
        self._max_bytes = max_megabytes * 1024 * 1024
        self._storage = self._root / "storage" / "imports" / "knowledge"
        self._extractor = DocumentTextExtractor(
            max_pages=max_pages,
            max_expanded_bytes=min(max(self._max_bytes * 10, 10 * 1024 * 1024), 500 * 1024 * 1024),
            ocr_enabled=ocr_enabled,
            ocr_engine=ocr_engine,
        )

    async def stage(
        self,
        *,
        user_qq: str,
        purpose: DocumentPurpose,
        filename: str,
        media_type: str,
        content: bytes,
        created_by: str,
    ) -> KnowledgeImportResult:
        if not self._import_enabled:
            raise KnowledgeImportError("document import is disabled")
        if not user_qq.isdigit():
            raise KnowledgeImportError("user_qq must contain digits only")
        safe_name = Path(filename or "document.txt").name
        if not content:
            raise KnowledgeImportError("document must not be empty")
        if len(content) > self._max_bytes:
            raise KnowledgeImportError("document exceeds configured size limit")
        try:
            parsed = self._extractor.extract(filename=safe_name, content=content)
        except DocumentParseError as exc:
            raise KnowledgeImportError(str(exc)) from exc
        normalized = parsed.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise KnowledgeImportError("document contains no readable text")

        document_id = str(uuid4())
        job_id = str(uuid4())
        digest = hashlib.sha256(content).hexdigest()
        destination_dir = (self._storage / document_id).resolve()
        if self._storage.resolve() not in destination_dir.parents:
            raise KnowledgeImportError("invalid storage destination")
        destination_dir.mkdir(parents=True, exist_ok=False)
        destination = destination_dir / safe_name
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(destination)
            chunks = tuple(_chunk_text(normalized))
            relative_path = destination.relative_to(self._root).as_posix()
            job_status = "queued" if self._processing_enabled else "awaiting_model_config"
            document_status = "staged"
            details = await self._repository.register_knowledge_document(
                document_id=document_id,
                job_id=job_id,
                user_qq=user_qq,
                purpose=purpose.value,
                original_filename=safe_name,
                content_sha256=digest,
                byte_count=len(content),
                text_length=len(normalized),
                document_status=document_status,
                job_status=job_status,
                storage_path=relative_path,
                media_type=media_type or "application/octet-stream",
                detected_format=parsed.detected_format,
                chunks=chunks,
                created_by=created_by,
            )
            details["parse_metadata"] = parsed.metadata
        except Exception:
            if temporary.exists():
                temporary.unlink()
            if destination.exists():
                destination.unlink()
            if destination_dir.exists() and not any(destination_dir.iterdir()):
                destination_dir.rmdir()
            raise
        return KnowledgeImportResult(
            document_id=document_id,
            job_id=job_id,
            status=job_status,
            details=details,
        )

    async def submit_processing_preview(
        self,
        *,
        job_id: str,
        payload: dict[str, object],
    ) -> bool:
        job = await self._repository.knowledge_job(job_id)
        if job is None:
            raise KnowledgeImportError("knowledge job not found")
        try:
            purpose = DocumentPurpose(job["purpose"])
            draft = validate_knowledge_draft(purpose, payload)
        except (ValueError, KnowledgeDraftError) as exc:
            raise KnowledgeImportError(str(exc)) from exc
        return await self._repository.store_knowledge_preview(job_id, draft.payload)

    async def approve_preview(
        self,
        *,
        job_id: str,
        approved_by: str,
    ) -> dict[str, object]:
        job = await self._repository.knowledge_job(job_id)
        if job is None or job.get("result_preview") is None:
            raise KnowledgeImportError("knowledge preview not found")
        try:
            validate_knowledge_draft(
                DocumentPurpose(job["purpose"]),
                job["result_preview"],
            )
        except (ValueError, KnowledgeDraftError) as exc:
            raise KnowledgeImportError(str(exc)) from exc
        result = await self._repository.approve_knowledge_preview(
            job_id=job_id,
            approved_by=approved_by,
        )
        if result is None:
            raise KnowledgeImportError("knowledge preview is not awaiting approval")
        return result


def _chunk_text(text: str, *, target_chars: int = 6000) -> list[str]:
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", text) if item.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for paragraph in paragraphs:
        if len(paragraph) > target_chars:
            if current:
                chunks.append("\n\n".join(current))
                current, current_size = [], 0
            chunks.extend(
                paragraph[index : index + target_chars]
                for index in range(0, len(paragraph), target_chars)
            )
            continue
        extra = len(paragraph) + (2 if current else 0)
        if current and current_size + extra > target_chars:
            chunks.append("\n\n".join(current))
            current, current_size = [], 0
        current.append(paragraph)
        current_size += extra
    if current:
        chunks.append("\n\n".join(current))
    return chunks
