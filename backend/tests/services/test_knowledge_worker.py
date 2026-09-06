from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from ych_bot.application import KnowledgeProcessingService
from ych_bot.domain.modeling import ChatGenerationRequest, ChatGenerationResult
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.workers import KnowledgeProcessingWorker

OWNER_QQ = "2000000001"


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


class FakeChatGateway:
    def __init__(self, outcomes: list[str | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def generate(self, request: ChatGenerationRequest) -> ChatGenerationResult:
        del request
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return ChatGenerationResult(text=outcome)

    async def close(self) -> None:
        return None


async def register_job(repository: SQLiteRepository) -> str:
    document_id = str(uuid4())
    job_id = str(uuid4())
    await repository.register_knowledge_document(
        document_id=document_id,
        job_id=job_id,
        user_qq="10001",
        purpose="user_understanding",
        original_filename="worker.txt",
        content_sha256="c" * 64,
        byte_count=22,
        text_length=22,
        document_status="staged",
        job_status="queued",
        storage_path=f"storage/imports/knowledge/{document_id}/worker.txt",
        media_type="text/plain",
        detected_format="text",
        chunks=("Likes quiet libraries.",),
        created_by=OWNER_QQ,
    )
    return job_id


def worker(
    repository: SQLiteRepository,
    gateway: FakeChatGateway,
    *,
    clock: MutableClock,
    enabled: bool = True,
    max_attempts: int = 3,
    retry_base_seconds: int = 60,
) -> KnowledgeProcessingWorker:
    service = KnowledgeProcessingService(
        repository,
        gateway,
        owner_qq=OWNER_QQ,
        enabled=True,
        lease_seconds=300,
        clock=clock,
    )
    return KnowledgeProcessingWorker(
        repository,
        service,
        configured_enabled=enabled,
        poll_seconds=1,
        max_attempts=max_attempts,
        retry_base_seconds=retry_base_seconds,
        clock=clock,
    )


def encoded(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


@pytest.mark.asyncio
async def test_worker_processes_one_ready_job_and_exposes_safe_status(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "worker.sqlite3")
    await repository.initialize()
    job_id = await register_job(repository)
    clock = MutableClock(datetime.now(UTC))
    gateway = FakeChatGateway(
        [
            encoded(
                {
                    "observations": [
                        {
                            "category": "interest",
                            "claim": "likes quiet libraries",
                            "confidence": 0.9,
                            "quote": "Likes quiet libraries",
                        }
                    ]
                }
            ),
            encoded(
                {
                    "summary": {"interests": ["quiet libraries"]},
                    "evidence": [
                        {
                            "chunk_index": 0,
                            "quote": "Likes quiet libraries",
                            "claim": "library preference",
                        }
                    ],
                }
            ),
        ]
    )
    processing_worker = worker(repository, gateway, clock=clock)

    result = await processing_worker.run_once()

    assert result.status == "completed"
    assert result.job_id == job_id
    assert (await repository.knowledge_job(job_id))["status"] == "awaiting_approval"
    snapshot = processing_worker.snapshot()
    assert snapshot["completed_jobs"] == 1
    assert snapshot["last_job_id"] == job_id
    public = processing_worker.public_snapshot()
    assert "last_job_id" not in public
    assert "last_error_type" not in public


@pytest.mark.asyncio
async def test_worker_applies_backoff_and_stops_after_max_attempts(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "retry.sqlite3")
    await repository.initialize()
    job_id = await register_job(repository)
    clock = MutableClock(datetime.now(UTC))
    gateway = FakeChatGateway([RuntimeError("one"), RuntimeError("two")])
    processing_worker = worker(
        repository,
        gateway,
        clock=clock,
        max_attempts=2,
        retry_base_seconds=60,
    )

    first = await processing_worker.run_once()
    immediate = await processing_worker.run_once()
    clock.advance(seconds=61)
    second = await processing_worker.run_once()
    exhausted = await processing_worker.run_once()

    assert first.status == "failed"
    assert immediate.status == "idle"
    assert immediate.reason == "no_ready_job"
    assert second.status == "failed"
    assert exhausted.status == "idle"
    assert gateway.calls == 2
    snapshot = processing_worker.snapshot()
    assert snapshot["failed_attempts"] == 2
    assert snapshot["exhausted_jobs"] == 1
    job = await repository.knowledge_job(job_id)
    assert job["attempt_count"] == 2
    assert job["status"] == "queued"


@pytest.mark.asyncio
async def test_disabled_and_paused_worker_never_calls_model(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "disabled.sqlite3")
    await repository.initialize()
    await register_job(repository)
    clock = MutableClock(datetime.now(UTC))
    gateway = FakeChatGateway([])
    disabled = worker(repository, gateway, clock=clock, enabled=False)

    assert (await disabled.run_once()).status == "disabled"
    assert disabled.pause() is False
    assert disabled.resume() is False

    enabled = worker(repository, gateway, clock=clock)
    assert enabled.pause() is True
    assert (await enabled.run_once()).status == "disabled"
    assert enabled.resume() is True
    assert gateway.calls == 0


@pytest.mark.asyncio
async def test_worker_pause_survives_process_restart(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "pause-persist.sqlite3")
    await repository.initialize()
    await register_job(repository)
    clock = MutableClock(datetime.now(UTC))
    gateway = FakeChatGateway([])
    first = worker(repository, gateway, clock=clock)

    assert first.pause() is True
    assert first.snapshot()["paused"] is True
    assert (await repository.counts())["worker_runtime_state"] == 1

    restarted = worker(repository, FakeChatGateway([]), clock=clock)
    assert restarted.snapshot()["paused"] is True
    assert restarted.active is False
    assert (await restarted.run_once()).status == "disabled"
    assert restarted.resume() is True
    assert restarted.snapshot()["paused"] is False
    assert restarted.active is True


@pytest.mark.asyncio
async def test_worker_loop_stops_cleanly_without_a_job(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "loop.sqlite3")
    await repository.initialize()
    clock = MutableClock(datetime.now(UTC))
    processing_worker = worker(repository, FakeChatGateway([]), clock=clock)

    task = asyncio.create_task(processing_worker.run_forever())
    await asyncio.sleep(0.02)
    assert processing_worker.snapshot()["loop_running"] is True
    processing_worker.request_stop()
    await asyncio.wait_for(task, timeout=1)

    assert processing_worker.snapshot()["loop_running"] is False
