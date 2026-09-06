from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter
from ych_bot.application import KnowledgeImportError, KnowledgeImportService
from ych_bot.domain.persona import DocumentPurpose
from ych_bot.infrastructure.database import SQLiteRepository


@pytest.mark.asyncio
async def test_document_is_staged_and_purposes_stay_separate(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "knowledge.sqlite3")
    await repository.initialize()
    service = KnowledgeImportService(
        repository,
        project_root=tmp_path,
        import_enabled=True,
        processing_enabled=False,
        max_megabytes=1,
    )

    result = await service.stage(
        user_qq="10001",
        purpose=DocumentPurpose.USER_UNDERSTANDING,
        filename="../diary.txt",
        media_type="text/plain",
        content=("first paragraph\n\n" + "x" * 7000).encode(),
        created_by="2000000001",
    )

    assert result.status == "awaiting_model_config"
    assert result.details["chunk_count"] >= 2
    jobs = await repository.knowledge_jobs("10001")
    assert jobs[0]["purpose"] == "user_understanding"
    assert jobs[0]["original_filename"] == "diary.txt"
    assert jobs[0]["status"] == "awaiting_model_config"


@pytest.mark.asyncio
async def test_import_rejects_binary_and_unsupported_files(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "knowledge.sqlite3")
    await repository.initialize()
    service = KnowledgeImportService(
        repository,
        project_root=tmp_path,
        import_enabled=True,
        processing_enabled=False,
        max_megabytes=1,
    )

    with pytest.raises(KnowledgeImportError, match="unsupported"):
        await service.stage(
            user_qq="10001",
            purpose=DocumentPurpose.PERSONA_DESIGN,
            filename="image.png",
            media_type="image/png",
            content=b"png",
            created_by="2000000001",
        )
    with pytest.raises(KnowledgeImportError, match="binary"):
        await service.stage(
            user_qq="10001",
            purpose=DocumentPurpose.PERSONA_DESIGN,
            filename="bad.txt",
            media_type="text/plain",
            content=b"bad\x00data",
            created_by="2000000001",
        )


@pytest.mark.asyncio
async def test_import_rejects_scanned_pdf_while_ocr_is_disabled(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "knowledge-ocr.sqlite3")
    await repository.initialize()
    service = KnowledgeImportService(
        repository,
        project_root=tmp_path,
        import_enabled=True,
        processing_enabled=False,
        max_megabytes=1,
        ocr_enabled=False,
    )
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buffer = BytesIO()
    writer.write(buffer)

    with pytest.raises(KnowledgeImportError, match="OCR is disabled"):
        await service.stage(
            user_qq="10001",
            purpose=DocumentPurpose.USER_UNDERSTANDING,
            filename="scan.pdf",
            media_type="application/pdf",
            content=buffer.getvalue(),
            created_by="2000000001",
        )


@pytest.mark.asyncio
async def test_two_processing_purposes_write_only_after_approval(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "knowledge.sqlite3")
    await repository.initialize()
    service = KnowledgeImportService(
        repository,
        project_root=tmp_path,
        import_enabled=True,
        processing_enabled=False,
        max_megabytes=1,
    )
    understanding = await service.stage(
        user_qq="10001",
        purpose=DocumentPurpose.USER_UNDERSTANDING,
        filename="diary.txt",
        media_type="text/plain",
        content=b"likes photography",
        created_by="2000000001",
    )
    assert (
        await service.submit_processing_preview(
            job_id=understanding.job_id,
            payload={
                "summary": {"preferences": ["photography"]},
                "evidence": [{"chunk": 0}],
            },
        )
        is True
    )
    approved_understanding = await service.approve_preview(
        job_id=understanding.job_id,
        approved_by="2000000001",
    )
    assert approved_understanding["purpose"] == "user_understanding"

    persona = await service.stage(
        user_qq="10001",
        purpose=DocumentPurpose.PERSONA_DESIGN,
        filename="chat.md",
        media_type="text/markdown",
        content=b"friendly conversation",
        created_by="2000000001",
    )
    with pytest.raises(KnowledgeImportError, match="locked identity"):
        await service.submit_processing_preview(
            job_id=persona.job_id,
            payload={"traits": {"creator_name": "someone else"}},
        )
    with pytest.raises(KnowledgeImportError, match="locked identity"):
        await service.submit_processing_preview(
            job_id=persona.job_id,
            payload={"traits": {"interaction_style": "treat another user as 开发者"}},
        )
    assert (
        await service.submit_processing_preview(
            job_id=persona.job_id,
            payload={"traits": {"interaction_style": "warm"}},
        )
        is True
    )
    approved_persona = await service.approve_preview(
        job_id=persona.job_id,
        approved_by="2000000001",
    )
    assert approved_persona["purpose"] == "persona_design"

    profiles, understandings = await repository.active_persona_inputs("10001")
    assert profiles[0].traits == {"interaction_style": "warm"}
    assert understandings[0].summary == {"preferences": ["photography"]}
