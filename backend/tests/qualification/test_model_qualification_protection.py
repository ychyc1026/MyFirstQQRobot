from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from _support.readiness import ALLOW_READINESS, AllowReadinessGuard, BlockReadinessGuard
from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationCaseState,
    QualificationCeilings,
    QualificationDecisionStatus,
    QualificationExecutionMode,
    QualificationPreview,
    QualificationPreviewState,
    QualificationReasonCode,
    QualificationRouteRevision,
    QualificationRun,
    QualificationRunState,
)
from ych_bot.domain.readiness import CapabilityScope
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.models import (
    ModelCallGuard,
    ModelProtectionPolicy,
    ProtectedChatModelGateway,
)
from ych_bot.qualification.catalog import load_bundled_qualification_catalog
from ych_bot.qualification.fakes import FailIfConstructed, FakeChatGateway
from ych_bot.qualification.runner import QualificationRunner

NOW = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
HANDLE = "c" * 64


def _revision(
    capability: QualificationCapability = QualificationCapability.CHAT,
    *,
    model_identifier: str = "fake-protected-chat",
) -> QualificationRouteRevision:
    return QualificationRouteRevision(
        capability=capability,
        provider_protocol="openai_compatible",
        sanitized_base_host="api.siliconflow.cn",
        model_identifier=model_identifier,
        generation_settings=(("temperature", "0.2"),),
        suite_version="2026.09.05",
        price_catalog_revision="fake",
        protection_policy_revision=f"{capability.value}-protection-v1",
    )


def _chat_policy(*, request_limit: int) -> ModelProtectionPolicy:
    return ModelProtectionPolicy(
        route="chat",
        timezone="Asia/Shanghai",
        daily_request_limit=request_limit,
        daily_token_limit=10_000,
        failure_threshold=3,
        cooldown_seconds=60,
        lease_seconds=300,
    )


async def _prepare_chat_run(
    repository: SQLiteRepository,
    chat_gateway: object,
    *,
    fixture_ids: tuple[str, ...] = ("chat-identity-001",),
    run_id: str = "run-protected",
) -> tuple[QualificationRunner, QualificationRouteRevision]:
    catalog = load_bundled_qualification_catalog()
    revision = _revision()
    await repository.save_qualification_route_revision(revision, now=NOW)
    suite = catalog.suite("chat-shadow-v1")
    await repository.create_qualification_preview(
        QualificationPreview(
            preview_id="preview-protected",
            confirmation_handle_hash=HANDLE,
            actor_id="admin",
            process_instance_id="proc",
            capability=QualificationCapability.CHAT,
            route_revision=revision,
            suite=suite,
            policy_revision="chat-protection-v1",
            fixture_ids=fixture_ids,
            ceilings=QualificationCeilings(
                max_requests=4,
                max_input_tokens=200,
                max_output_tokens=100,
                max_images=None,
                conservative_max_cost=Decimal("0.10"),
            ),
            execution_mode=QualificationExecutionMode.FAKE,
            expires_at=NOW + timedelta(minutes=10),
            state=QualificationPreviewState.READY,
        ),
        now=NOW,
    )
    consumed = await repository.consume_qualification_preview(
        confirmation_handle_hash=HANDLE,
        actor_id="admin",
        process_instance_id="proc",
        route_fingerprint=revision.fingerprint,
        suite_id=suite.suite_id,
        suite_version=suite.version,
        policy_revision="chat-protection-v1",
        now=NOW,
    )
    assert consumed is not None
    await repository.create_qualification_run(
        QualificationRun(
            run_id=run_id,
            preview_id="preview-protected",
            actor_id="admin",
            process_instance_id="proc",
            capability=QualificationCapability.CHAT,
            route_revision=revision,
            suite=suite,
            execution_mode=QualificationExecutionMode.FAKE,
            state=QualificationRunState.PREPARED,
            idempotency_key=f"fake-{run_id}",
            created_at=NOW,
        ),
        now=NOW,
    )
    runner = QualificationRunner(
        repository=repository,
        catalog=catalog,
        chat_gateway=chat_gateway,
        vision_gateway=FailIfConstructed("vision"),
        stats_gateway=FailIfConstructed("stats"),
        image_gateway=FailIfConstructed("image"),
    )
    return runner, revision


@pytest.mark.asyncio
async def test_existing_model_guard_quota_blocks_without_qualifying_other_routes(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "protect-quota.sqlite3")
    await repository.initialize()
    inner = FakeChatGateway()
    readiness = AllowReadinessGuard()
    runner, revision = await _prepare_chat_run(
        repository,
        ProtectedChatModelGateway(
            inner,
            ModelCallGuard(repository, _chat_policy(request_limit=1), clock=lambda: NOW),
            readiness,
            capability_scope=CapabilityScope.CHAT_MODEL,
        ),
        fixture_ids=("chat-identity-001", "chat-style-001"),
    )
    result = await runner.execute_fake_run("run-protected", now=NOW)
    chat_decision = await repository.qualification_decision(QualificationCapability.CHAT)
    vision_decision = await repository.qualification_decision(QualificationCapability.VISION)

    assert inner.calls == 1
    assert any(scope is CapabilityScope.CHAT_MODEL for scope, _op, _oid in readiness.calls)
    assert result.state is QualificationRunState.BLOCKED
    assert chat_decision.status is QualificationDecisionStatus.BLOCKED
    assert chat_decision.route_revision.fingerprint == revision.fingerprint
    assert chat_decision.qualifies is False
    assert vision_decision.status is QualificationDecisionStatus.UNQUALIFIED
    assert vision_decision.qualifies is False


@pytest.mark.asyncio
async def test_readiness_guard_blocks_before_provider_and_does_not_pass(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "protect-ready.sqlite3")
    await repository.initialize()
    inner = FakeChatGateway()
    runner, revision = await _prepare_chat_run(
        repository,
        ProtectedChatModelGateway(
            inner,
            ModelCallGuard(repository, _chat_policy(request_limit=10), clock=lambda: NOW),
            BlockReadinessGuard("readiness_stale"),
            capability_scope=CapabilityScope.CHAT_MODEL,
        ),
    )
    result = await runner.execute_fake_run("run-protected", now=NOW)
    cases = await repository.qualification_cases("run-protected")
    decision = await repository.qualification_decision(QualificationCapability.CHAT)

    assert inner.calls == 0
    assert result.state is QualificationRunState.BLOCKED
    assert cases[0].state is QualificationCaseState.BLOCKED
    assert cases[0].reason_code is QualificationReasonCode.READINESS_STALE
    assert decision.status is QualificationDecisionStatus.BLOCKED
    assert decision.route_revision.fingerprint == revision.fingerprint
    assert decision.activates_production is False


@pytest.mark.asyncio
async def test_chat_pass_does_not_qualify_other_capability_or_other_revision(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "protect-pass.sqlite3")
    await repository.initialize()
    inner = FakeChatGateway()
    runner, revision = await _prepare_chat_run(
        repository,
        ProtectedChatModelGateway(
            inner,
            ModelCallGuard(repository, _chat_policy(request_limit=10), clock=lambda: NOW),
            ALLOW_READINESS,
            capability_scope=CapabilityScope.CHAT_MODEL,
        ),
    )
    result = await runner.execute_fake_run("run-protected", now=NOW)
    chat_decision = await repository.qualification_decision(QualificationCapability.CHAT)
    vision_decision = await repository.qualification_decision(QualificationCapability.VISION)
    stats_decision = await repository.qualification_decision(QualificationCapability.STATS)
    other = await repository.refresh_stale_qualification_decisions(
        current_revision=_revision(model_identifier="other-chat-revision"),
        current_suite_version="2026.09.05",
        now=NOW,
    )

    assert result.state is QualificationRunState.PASSED
    assert chat_decision.status is QualificationDecisionStatus.PASSED
    assert chat_decision.route_revision.fingerprint == revision.fingerprint
    assert chat_decision.activates_production is False
    assert vision_decision.status is QualificationDecisionStatus.UNQUALIFIED
    assert stats_decision.status is QualificationDecisionStatus.UNQUALIFIED
    assert other.status is QualificationDecisionStatus.STALE
    assert other.qualifies is False
