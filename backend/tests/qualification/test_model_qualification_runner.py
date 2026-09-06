import ast
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from _support.paths import SOURCE_ROOT
from _support.qualification_runs import (
    NOW,
    ceilings,
    prepare_run,
)
from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationCaseState,
    QualificationReasonCode,
    QualificationRunState,
)
from ych_bot.domain.modeling import ModelRole
from ych_bot.domain.system_identity import CORE_IDENTITY
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.qualification.fakes import (
    FailIfConstructed,
    FakeChatGateway,
    FakeImageGateway,
    FakeStatsGateway,
    FakeVisionGateway,
)
from ych_bot.qualification.runner import QualificationRunner

RUNNER_ROOT = SOURCE_ROOT / "qualification"


async def _ready_run(
    tmp_path: Path,
) -> tuple[SQLiteRepository, QualificationRunner, FakeChatGateway]:
    repository, runner, chat, _vision, _stats, _image = await prepare_run(tmp_path)
    assert isinstance(chat, FakeChatGateway)
    return repository, runner, chat


def test_runner_source_does_not_import_delivery_or_user_data_clients() -> None:
    forbidden = {
        "ych_bot.infrastructure.napcat",
        "ych_bot.application.qzone",
        "ych_bot.application.proactive",
        "ych_bot.application.reports",
        "ych_bot.application.history",
    }
    imported: set[str] = set()
    for path in RUNNER_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
    assert forbidden.isdisjoint(imported)


@pytest.mark.asyncio
async def test_fake_runner_uses_catalog_only_and_does_not_touch_outbox(tmp_path: Path) -> None:
    repository, runner, chat = await _ready_run(tmp_path)
    result = await runner.execute_fake_run("run-fake", now=NOW)
    cases = await repository.qualification_cases("run-fake")

    assert result.state in {
        QualificationRunState.PASSED,
        QualificationRunState.FAILED,
    }
    assert chat.calls == 1
    assert cases[0].fixture_id == "chat-identity-001"
    assert chat.last_request is not None
    roles = [message.role for message in chat.last_request.messages]
    assert roles[0] is ModelRole.SYSTEM
    assert CORE_IDENTITY.creator_name in chat.last_request.messages[0].content
    assert roles[-1] is ModelRole.USER
    assert any(item.check_code == "creator_identity" for item in cases[0].check_results)
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM qualification_check_results").fetchone()[0] > 0
        )


@pytest.mark.asyncio
async def test_request_ceiling_blocks_remaining_cases(tmp_path: Path) -> None:
    repository, runner, chat, *_ = await prepare_run(
        tmp_path,
        fixture_ids=("chat-identity-001", "chat-style-001"),
        run_ceilings=ceilings(max_requests=1, max_output_tokens=100),
    )
    result = await runner.execute_fake_run("run-fake", now=NOW)
    cases = {item.fixture_id: item for item in await repository.qualification_cases("run-fake")}

    assert chat.calls == 1
    assert result.state is QualificationRunState.BLOCKED
    assert cases["chat-identity-001"].state is QualificationCaseState.PASSED
    assert cases["chat-style-001"].state is QualificationCaseState.BLOCKED
    assert cases["chat-style-001"].reason_code is QualificationReasonCode.CEILING_EXHAUSTED


@pytest.mark.asyncio
async def test_token_ceiling_blocks_after_observed_usage(tmp_path: Path) -> None:
    repository, runner, chat, *_ = await prepare_run(
        tmp_path,
        fixture_ids=("chat-identity-001", "chat-style-001"),
        run_ceilings=ceilings(max_requests=4, max_input_tokens=8, max_output_tokens=12),
    )
    result = await runner.execute_fake_run("run-fake", now=NOW)
    cases = {item.fixture_id: item for item in await repository.qualification_cases("run-fake")}

    assert chat.calls == 1
    assert result.state is QualificationRunState.BLOCKED
    assert cases["chat-style-001"].state is QualificationCaseState.BLOCKED
    assert cases["chat-style-001"].reason_code is QualificationReasonCode.CEILING_EXHAUSTED


@pytest.mark.asyncio
async def test_image_ceiling_is_independent_of_chat(tmp_path: Path) -> None:
    image = FakeImageGateway()
    repository, runner, chat, _vision, _stats, image_gateway = await prepare_run(
        tmp_path,
        capability=QualificationCapability.IMAGE,
        fixture_ids=("image-form-001", "image-mime-001"),
        run_ceilings=ceilings(
            max_requests=4,
            max_input_tokens=None,
            max_output_tokens=None,
            max_images=1,
        ),
        image=image,
    )
    result = await runner.execute_fake_run("run-fake", now=NOW)
    cases = {item.fixture_id: item for item in await repository.qualification_cases("run-fake")}

    assert isinstance(chat, FailIfConstructed)
    assert image_gateway.calls == 1
    assert result.state is QualificationRunState.BLOCKED
    assert cases["image-mime-001"].state is QualificationCaseState.BLOCKED
    assert cases["image-mime-001"].reason_code is QualificationReasonCode.CEILING_EXHAUSTED


@pytest.mark.asyncio
async def test_expired_preview_blocks_without_provider_calls(tmp_path: Path) -> None:
    repository, runner, chat, *_ = await prepare_run(
        tmp_path,
        expires_at=NOW + timedelta(minutes=1),
    )
    result = await runner.execute_fake_run("run-fake", now=NOW + timedelta(minutes=2))
    cases = await repository.qualification_cases("run-fake")

    assert chat.calls == 0
    assert result.state is QualificationRunState.BLOCKED
    assert all(item.state is QualificationCaseState.BLOCKED for item in cases)
    assert cases[0].reason_code is QualificationReasonCode.PREVIEW_EXPIRED


@pytest.mark.asyncio
async def test_fake_runner_executes_only_the_run_capability(tmp_path: Path) -> None:
    vision = FakeVisionGateway()
    stats = FakeStatsGateway()
    repository, runner, chat, vision_gateway, stats_gateway, _image = await prepare_run(
        tmp_path,
        capability=QualificationCapability.VISION,
        fixture_ids=("vision-ground-001",),
        vision=vision,
        stats=stats,
    )
    result = await runner.execute_fake_run("run-fake", now=NOW)

    assert isinstance(chat, FailIfConstructed)
    assert vision_gateway.calls == 1
    assert stats_gateway.calls == 0
    assert vision_gateway.last_request is not None
    image_urls = vision_gateway.last_request.messages[-1].image_urls
    assert len(image_urls) == 1
    assert image_urls[0].startswith("data:image/png;base64,")
    assert result.state in {QualificationRunState.PASSED, QualificationRunState.FAILED}


@pytest.mark.asyncio
async def test_cancelled_run_is_not_resumed(tmp_path: Path) -> None:
    repository, runner, chat, *_ = await prepare_run(
        tmp_path,
        fixture_ids=("chat-identity-001", "chat-style-001"),
    )
    await repository.transition_qualification_run(
        run_id="run-fake",
        from_state=QualificationRunState.PREPARED,
        to_state=QualificationRunState.RUNNING,
        now=NOW,
    )
    cancelled = await repository.cancel_qualification_run("run-fake", now=NOW)
    result = await runner.execute_fake_run("run-fake", now=NOW)

    assert cancelled is not None
    assert result.state is QualificationRunState.CANCELLED
    assert chat.calls == 0


@pytest.mark.asyncio
async def test_sleep_reconciliation_does_not_retry_ambiguous_provider_attempt(
    tmp_path: Path,
) -> None:
    repository, runner, chat, *_ = await prepare_run(tmp_path)
    await repository.transition_qualification_run(
        run_id="run-fake",
        from_state=QualificationRunState.PREPARED,
        to_state=QualificationRunState.RUNNING,
        now=NOW,
    )
    claimed = await repository.claim_qualification_case(
        run_id="run-fake",
        fixture_id="chat-identity-001",
        lease_token="lease-sleep",
        ttl_seconds=30,
        now=NOW,
    )
    assert claimed is not None
    await repository.mark_qualification_case_provider_attempt(
        case_id=claimed.case_id,
        lease_token="lease-sleep",
        provider_request_id="sf-req-sleep",
        now=NOW,
    )
    later = NOW + timedelta(minutes=2)
    result = await runner.execute_fake_run("run-fake", now=later)
    cases = await repository.qualification_cases("run-fake")

    assert chat.calls == 0
    assert cases[0].state is QualificationCaseState.INCONCLUSIVE
    assert cases[0].reason_code is QualificationReasonCode.AMBIGUOUS_INTERRUPTION
    assert result.state is QualificationRunState.INCONCLUSIVE
