import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from ych_bot.application import PrivacyJobError, PrivacyJobService
from ych_bot.domain.identity import FriendState
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.parser import parse_message_event


def private_event() -> dict:
    return {
        "time": 1_700_000_000,
        "self_id": 2000000002,
        "post_type": "message",
        "message_type": "private",
        "message_id": 909,
        "user_id": 123456789,
        "message": [{"type": "text", "data": {"text": "private test"}}],
    }


async def create_request(
    repository: SQLiteRepository,
    *,
    kind: str,
) -> str:
    request_id = str(uuid4())
    await repository.create_privacy_request(
        request_id=request_id,
        user_qq="123456789",
        request_kind=kind,
        requested_by="2000000001",
        reason="test",
    )
    return request_id


@pytest.mark.asyncio
async def test_privacy_jobs_are_disabled_by_default(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "privacy.sqlite3")
    await repository.initialize()
    request_id = await create_request(repository, kind="export")
    service = PrivacyJobService(repository, project_root=tmp_path, enabled=False)

    with pytest.raises(PrivacyJobError, match="disabled"):
        await service.execute(request_id)

    request = await repository.privacy_request(request_id)
    assert request is not None
    assert request["status"] == "pending"


@pytest.mark.asyncio
async def test_export_and_delete_are_verified_and_backed_up(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "privacy.sqlite3")
    await repository.initialize()
    message = parse_message_event(private_event(), expected_bot_qq="2000000002")
    assert await repository.store_inbound(message) is True
    await repository.set_friend_state(
        user_qq="123456789",
        state=FriendState.EXISTING_FRIEND,
        updated_by="2000000001",
        reason="test",
    )
    run_id = str(uuid4())
    candidate_id = str(uuid4())
    await repository.start_inference_run(
        run_id=run_id,
        source_message_id=message.id,
        conversation_key=message.conversation_key,
        actor_qq="123456789",
        mode="shadow",
        prompt_hash="test-prompt-hash",
        model_route="fake-chat",
    )
    await repository.finish_inference_success(
        run_id=run_id,
        candidate_id=candidate_id,
        source_message_id=message.id,
        conversation_kind="private",
        target_id="123456789",
        content="shadow reply",
        provider_request_id="fake-request",
        input_tokens=10,
        output_tokens=3,
        safety_flags=(),
    )
    document_id = str(uuid4())
    knowledge_job_id = str(uuid4())
    source_content = b"private note"
    source_path = tmp_path / "storage" / "imports" / "knowledge" / document_id / "private.txt"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(source_content)
    await repository.register_knowledge_document(
        document_id=document_id,
        job_id=knowledge_job_id,
        user_qq="123456789",
        purpose="user_understanding",
        original_filename="private.txt",
        content_sha256=hashlib.sha256(source_content).hexdigest(),
        byte_count=len(source_content),
        text_length=12,
        document_status="staged",
        job_status="queued",
        storage_path=f"storage/imports/knowledge/{document_id}/private.txt",
        media_type="text/plain",
        detected_format="text",
        chunks=("private note",),
        created_by="2000000001",
    )
    lease_token = str(uuid4())
    now = datetime.now(UTC)
    bundle = await repository.claim_knowledge_job(
        job_id=knowledge_job_id,
        lease_token=lease_token,
        now=now,
        lease_expires_at=now + timedelta(minutes=5),
    )
    assert bundle is not None
    assert await repository.store_knowledge_chunk_analysis(
        job_id=knowledge_job_id,
        lease_token=lease_token,
        lease_expires_at=now + timedelta(minutes=5),
        chunk_id=bundle["chunks"][0]["id"],
        chunk_index=0,
        purpose="user_understanding",
        result={"observations": []},
        provider_request_id="fake-knowledge",
    )
    knowledge_approval_id = str(uuid4())
    knowledge_report_id = str(uuid4())
    assert await repository.complete_knowledge_job(
        job_id=knowledge_job_id,
        lease_token=lease_token,
        preview={"summary": {"note": "private"}, "evidence": []},
        approval_id=knowledge_approval_id,
        approval_code="ABC123",
        requested_to="2000000001",
        report_id=knowledge_report_id,
    )
    service = PrivacyJobService(repository, project_root=tmp_path, enabled=True)

    export_id = await create_request(repository, kind="export")
    export_result = await service.execute(export_id)
    export_path = tmp_path / export_result.details["path"]
    assert export_path.exists()
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["format"] == "ych-privacy-bundle-v1"
    assert exported["data"]["messages"][0]["plain_text"] == "private test"
    assert exported["data"]["inference_runs"][0]["id"] == run_id
    assert exported["data"]["reply_candidates"][0]["id"] == candidate_id
    assert exported["data"]["knowledge_job_checkpoints"][0]["job_id"] == knowledge_job_id
    assert exported["data"]["knowledge_chunk_analyses"][0]["job_id"] == knowledge_job_id
    assert exported["data"]["knowledge_approval_requests"][0]["id"] == knowledge_approval_id
    assert exported["data"]["knowledge_owner_reports"][0]["id"] == knowledge_report_id
    assert exported["data"]["owner_report_runtime"][0]["report_id"] == knowledge_report_id
    assert exported["data"]["owner_report_events"]
    export_archive = tmp_path / export_result.details["file_archive"]["path"]
    assert export_archive.exists()
    assert export_result.details["file_archive"]["file_count"] == 1
    assert source_path.exists()
    export_verification = await service.verify_artifact(export_id)
    assert export_verification["verified"] is True
    assert export_verification["bundle"]["format"] == "ych-privacy-bundle-v1"
    assert len(export_verification["artifacts"]) == 2
    assert any(
        item.get("file_archive", {}).get("file_count") == 1
        for item in export_verification["artifacts"]
    )
    assert export_verification["recovery"]["automatic_restore_supported"] is False

    delete_id = await create_request(repository, kind="delete")
    preview = await service.preview(delete_id)
    assert preview["impact"]["messages"] == 1
    assert preview["impact"]["inference_runs"] == 1
    assert preview["impact"]["reply_candidates"] == 1
    assert preview["impact"]["knowledge_job_checkpoints"] == 1
    assert preview["impact"]["knowledge_chunk_analyses"] == 1
    assert preview["impact"]["approval_requests"] == 1
    assert preview["impact"]["owner_reports"] == 1
    assert preview["impact"]["owner_report_runtime"] == 1
    assert preview["impact"]["owner_report_events"] >= 1
    delete_result = await service.execute(delete_id)
    backup_path = tmp_path / delete_result.details["backup"]["path"]
    assert backup_path.exists()
    file_backup_path = tmp_path / delete_result.details["file_archive"]["path"]
    assert file_backup_path.exists()
    assert source_path.exists() is False
    assert delete_result.details["removed_import_files"] == 1
    assert delete_result.details["deleted_counts"]["messages"] == 1
    backup_verification = await service.verify_artifact(delete_id)
    assert backup_verification["verified"] is True
    assert backup_verification["artifact"]["artifact_type"] == "pre_delete_backup"
    assert len(backup_verification["artifacts"]) == 2

    detail = await repository.user_detail("123456789")
    assert detail["relationship"] is None
    assert (await repository.counts())["messages"] == 0
    assert (await repository.counts())["inference_runs"] == 0
    assert (await repository.counts())["reply_candidates"] == 0
    assert (await repository.counts())["knowledge_job_checkpoints"] == 0
    assert (await repository.counts())["knowledge_chunk_analyses"] == 0
    assert (await repository.counts())["approval_requests"] == 0
    assert (await repository.counts())["owner_reports"] == 0
    assert (await repository.counts())["owner_report_runtime"] == 0
    assert (await repository.counts())["owner_report_events"] == 0
    requests = await repository.privacy_requests()
    assert all(item["user_qq"].startswith("deleted:") for item in requests)


@pytest.mark.asyncio
async def test_privacy_artifact_verification_detects_tampering(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "privacy-tamper.sqlite3")
    await repository.initialize()
    request_id = await create_request(repository, kind="export")
    service = PrivacyJobService(repository, project_root=tmp_path, enabled=True)
    result = await service.execute(request_id)
    artifact_path = tmp_path / result.details["path"]
    artifact_path.write_text("{}", encoding="utf-8")

    verification = await service.verify_artifact(request_id)

    assert verification["verified"] is False
    assert verification["reason"] == "byte_count_mismatch"
    assert verification["recovery"]["automatic_restore_supported"] is False


@pytest.mark.asyncio
async def test_delete_stops_before_database_change_when_import_file_is_tampered(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "privacy-source-tamper.sqlite3")
    await repository.initialize()
    document_id = str(uuid4())
    job_id = str(uuid4())
    original = b"original private file"
    source_path = tmp_path / "storage" / "imports" / "knowledge" / document_id / "note.txt"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(original)
    await repository.register_knowledge_document(
        document_id=document_id,
        job_id=job_id,
        user_qq="123456789",
        purpose="user_understanding",
        original_filename="note.txt",
        content_sha256=hashlib.sha256(original).hexdigest(),
        byte_count=len(original),
        text_length=len(original),
        document_status="staged",
        job_status="awaiting_model_config",
        storage_path=f"storage/imports/knowledge/{document_id}/note.txt",
        media_type="text/plain",
        detected_format="text",
        chunks=("original private file",),
        created_by="2000000001",
    )
    source_path.write_bytes(b"tampered")
    request_id = await create_request(repository, kind="delete")
    service = PrivacyJobService(repository, project_root=tmp_path, enabled=True)

    with pytest.raises(PrivacyJobError, match="integrity verification"):
        await service.execute(request_id)

    assert source_path.read_bytes() == b"tampered"
    assert (await repository.privacy_delete_impact("123456789"))["knowledge_documents"] == 1
    request = await repository.privacy_request(request_id)
    assert request is not None
    assert request["status"] == "failed"
