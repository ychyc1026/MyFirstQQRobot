from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from ych_bot.application import KnowledgeProcessingError, KnowledgeProcessingService
from ych_bot.application.control import OwnerControlService
from ych_bot.domain.control import ControlSource, OwnerCommand, OwnerCommandKind
from ych_bot.domain.modeling import ChatGenerationRequest, ChatGenerationResult
from ych_bot.domain.persona import DocumentPurpose
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.client import NapCatClient

OWNER_QQ = "2000000001"


class FakeChatGateway:
    def __init__(self, outcomes: list[str | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[ChatGenerationRequest] = []

    async def generate(self, request: ChatGenerationRequest) -> ChatGenerationResult:
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError("fake model received an unexpected request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return ChatGenerationResult(
            text=outcome,
            provider_request_id=f"fake-{len(self.requests)}",
            input_tokens=10,
            output_tokens=5,
        )

    async def close(self) -> None:
        return None


async def register_job(
    repository: SQLiteRepository,
    *,
    purpose: DocumentPurpose,
    chunks: tuple[str, ...],
) -> str:
    document_id = str(uuid4())
    job_id = str(uuid4())
    await repository.register_knowledge_document(
        document_id=document_id,
        job_id=job_id,
        user_qq="10001",
        purpose=purpose.value,
        original_filename="source.txt",
        content_sha256="a" * 64,
        byte_count=sum(len(item.encode()) for item in chunks),
        text_length=sum(len(item) for item in chunks),
        document_status="staged",
        job_status="queued",
        storage_path=f"storage/imports/knowledge/{document_id}/source.txt",
        media_type="text/plain",
        detected_format="text",
        chunks=chunks,
        created_by=OWNER_QQ,
    )
    return job_id


def processor(
    repository: SQLiteRepository,
    gateway: FakeChatGateway,
    *,
    batch_size: int = 20,
) -> KnowledgeProcessingService:
    return KnowledgeProcessingService(
        repository,
        gateway,
        owner_qq=OWNER_QQ,
        enabled=True,
        lease_seconds=300,
        reduction_batch_size=batch_size,
    )


def as_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


@pytest.mark.asyncio
async def test_user_understanding_processing_is_grounded_and_awaits_approval(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "understanding.sqlite3")
    await repository.initialize()
    job_id = await register_job(
        repository,
        purpose=DocumentPurpose.USER_UNDERSTANDING,
        chunks=("She likes street photography.", "She prefers concise replies."),
    )
    gateway = FakeChatGateway(
        [
            as_json(
                {
                    "observations": [
                        {
                            "category": "interest",
                            "claim": "likes street photography",
                            "confidence": 0.9,
                            "quote": "likes street photography",
                        }
                    ]
                }
            ),
            as_json(
                {
                    "observations": [
                        {
                            "category": "communication",
                            "claim": "prefers concise replies",
                            "confidence": 0.9,
                            "quote": "prefers concise replies",
                        }
                    ]
                }
            ),
            as_json(
                {
                    "summary": {
                        "interests": ["street photography"],
                        "communication": "concise",
                    },
                    "evidence": [
                        {
                            "chunk_index": 0,
                            "quote": "likes street photography",
                            "claim": "photography interest",
                        },
                        {
                            "chunk_index": 1,
                            "quote": "prefers concise replies",
                            "claim": "concise communication",
                        },
                    ],
                }
            ),
        ]
    )

    napcat = NapCatClient(
        "http://napcat.test",
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(
                AssertionError(f"unexpected NapCat request: {request.url}")
            )
        ),
    )
    controls = OwnerControlService(
        repository,
        napcat,
        owner_qq=OWNER_QQ,
        bot_qq="2000000002",
        knowledge_processing_service=processor(repository, gateway),
    )
    try:
        process_command_id = str(uuid4())
        result = await controls.execute(
            OwnerCommand(
                id=process_command_id,
                kind=OwnerCommandKind.KNOWLEDGE_PROCESS,
                actor_qq=OWNER_QQ,
                source_message_id=f"knowledge:{process_command_id}",
                arguments={"job_id": job_id},
                source=ControlSource.OWNER_QQ,
            )
        )
        assert result.status == "pending_approval"
        assert result.data["status"] == "awaiting_approval"
        assert result.data["analyzed_chunk_count"] == 2
        job = await repository.knowledge_job(job_id)
        assert job["status"] == "awaiting_approval"
        assert job["analyzed_chunk_count"] == 2
        assert job["checkpoint_state"] == "completed"
        assert job["approval_status"] == "pending"
        assert job["approval_code"]
        assert job["result_preview"]["summary"]["communication"] == "concise"
        assert len(job["result_preview"]["evidence"]) == 2
        reports = await repository.owner_reports()
        assert reports[0]["related_id"] == job_id
        assert job["approval_code"] in reports[0]["body"]

        command_id = str(uuid4())
        approved = await controls.execute(
            OwnerCommand(
                id=command_id,
                kind=OwnerCommandKind.APPROVE,
                actor_qq=OWNER_QQ,
                source_message_id=f"knowledge:{command_id}",
                arguments={"approval_code": job["approval_code"]},
                source=ControlSource.OWNER_QQ,
            )
        )
    finally:
        await napcat.close()
    assert approved.data["approved"] is True
    _, understandings = await repository.active_persona_inputs("10001")
    assert understandings[0].summary["communication"] == "concise"
    assert (await repository.owner_reports())[0]["status"] == "acknowledged"


@pytest.mark.asyncio
async def test_failed_chunk_resume_skips_completed_analysis(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "resume.sqlite3")
    await repository.initialize()
    job_id = await register_job(
        repository,
        purpose=DocumentPurpose.USER_UNDERSTANDING,
        chunks=("First quote is here.", "Second quote is here."),
    )
    first_gateway = FakeChatGateway(
        [
            as_json(
                {
                    "observations": [
                        {
                            "category": "first",
                            "claim": "first",
                            "confidence": 0.8,
                            "quote": "First quote",
                        }
                    ]
                }
            ),
            RuntimeError("temporary failure"),
        ]
    )

    with pytest.raises(KnowledgeProcessingError, match="RuntimeError"):
        await processor(repository, first_gateway).process(job_id)

    failed = await repository.knowledge_job(job_id)
    assert failed["status"] == "queued"
    assert failed["analyzed_chunk_count"] == 1
    assert failed["checkpoint_state"] == "failed"
    assert failed["attempt_count"] == 1

    second_gateway = FakeChatGateway(
        [
            as_json(
                {
                    "observations": [
                        {
                            "category": "second",
                            "claim": "second",
                            "confidence": 0.8,
                            "quote": "Second quote",
                        }
                    ]
                }
            ),
            as_json(
                {
                    "summary": {"facts": ["first", "second"]},
                    "evidence": [
                        {"chunk_index": 0, "quote": "First quote", "claim": "first"},
                        {"chunk_index": 1, "quote": "Second quote", "claim": "second"},
                    ],
                }
            ),
        ]
    )
    result = await processor(repository, second_gateway).process(job_id)

    assert result.status == "awaiting_approval"
    assert len(second_gateway.requests) == 2
    resumed = await repository.knowledge_job(job_id)
    assert resumed["analyzed_chunk_count"] == 2
    assert resumed["attempt_count"] == 2


@pytest.mark.asyncio
async def test_persona_processing_rejects_identity_override_then_resumes_reduction(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "persona.sqlite3")
    await repository.initialize()
    job_id = await register_job(
        repository,
        purpose=DocumentPurpose.PERSONA_DESIGN,
        chunks=("The conversation feels warm and lightly humorous.",),
    )
    malicious = FakeChatGateway(
        [
            as_json(
                {
                    "style_signals": [
                        {
                            "dimension": "tone",
                            "recommendation": "respond warmly",
                            "confidence": 0.8,
                            "quote": "warm and lightly humorous",
                        }
                    ]
                }
            ),
            as_json(
                {
                    "traits": {"creator": "another person"},
                    "evidence": [
                        {
                            "chunk_index": 0,
                            "quote": "warm and lightly humorous",
                            "claim": "warm tone",
                        }
                    ],
                }
            ),
        ]
    )

    with pytest.raises(KnowledgeProcessingError, match="locked identity"):
        await processor(repository, malicious).process(job_id)
    failed = await repository.knowledge_job(job_id)
    assert failed["analyzed_chunk_count"] == 1
    assert failed["result_preview"] is None

    safe = FakeChatGateway(
        [
            as_json(
                {
                    "traits": {"interaction_style": "warm and lightly humorous"},
                    "evidence": [
                        {
                            "chunk_index": 0,
                            "quote": "warm and lightly humorous",
                            "claim": "warm tone",
                        }
                    ],
                }
            )
        ]
    )
    await processor(repository, safe).process(job_id)
    assert len(safe.requests) == 1
    approved = await repository.approve_knowledge_preview(
        job_id=job_id,
        approved_by=OWNER_QQ,
    )
    assert approved["purpose"] == "persona_design"
    profiles, understandings = await repository.active_persona_inputs("10001")
    assert profiles[0].traits == {"interaction_style": "warm and lightly humorous"}
    assert understandings == ()
    exported = await repository.privacy_export_snapshot("10001")
    stored_evidence = json.loads(exported["persona_profile_evidence"][0]["evidence_json"])
    assert stored_evidence[0]["chunk_index"] == 0
    impact = await repository.privacy_delete_impact("10001")
    assert impact["persona_profile_evidence"] == 1
    await repository.delete_user_data("10001", request_id="persona-delete-test")
    assert (await repository.counts())["persona_profile_evidence"] == 0


@pytest.mark.asyncio
async def test_fabricated_chunk_quote_is_never_checkpointed(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "fabricated.sqlite3")
    await repository.initialize()
    job_id = await register_job(
        repository,
        purpose=DocumentPurpose.USER_UNDERSTANDING,
        chunks=("Only this sentence exists.",),
    )
    gateway = FakeChatGateway(
        [
            as_json(
                {
                    "observations": [
                        {
                            "category": "invented",
                            "claim": "not grounded",
                            "confidence": 1,
                            "quote": "This quote was invented",
                        }
                    ]
                }
            )
        ]
    )

    with pytest.raises(KnowledgeProcessingError, match="not found in the source"):
        await processor(repository, gateway).process(job_id)

    job = await repository.knowledge_job(job_id)
    assert job["analyzed_chunk_count"] == 0
    assert job["status"] == "queued"
    assert job["result_preview"] is None
