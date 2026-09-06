from datetime import UTC, datetime
from pathlib import Path

import pytest
from ych_bot.application.qualification import (
    QualificationPlanningError,
    QualificationPlanningService,
)
from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationExecutionMode,
    QualificationReasonCode,
    QualificationRouteRevision,
    QualificationRunState,
)
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.qualification.catalog import load_bundled_qualification_catalog
from ych_bot.qualification.fakes import FailIfConstructed
from ych_bot.qualification.prices import load_bundled_qualification_price_catalog

NOW = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)


class FrozenClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


def _revision(
    capability: QualificationCapability,
    model_identifier: str,
) -> QualificationRouteRevision:
    return QualificationRouteRevision(
        capability=capability,
        provider_protocol="openai_compatible",
        sanitized_base_host="api.siliconflow.cn",
        model_identifier=model_identifier,
        generation_settings=(("temperature", "0.2"),),
        suite_version="2026.09.05",
        price_catalog_revision="siliconflow-2026-09-05",
        protection_policy_revision=f"{capability.value}-protection-v1",
    )


async def _service(tmp_path: Path) -> tuple[SQLiteRepository, QualificationPlanningService]:
    repository = SQLiteRepository(tmp_path / "qualify-plan.sqlite3")
    await repository.initialize()
    service = QualificationPlanningService(
        repository,
        process_instance_id="proc-plan",
        clock=FrozenClock(NOW),
        fixture_catalog=load_bundled_qualification_catalog(),
        price_catalog=load_bundled_qualification_price_catalog(),
    )
    return repository, service


@pytest.mark.asyncio
async def test_live_preview_returns_handle_once_and_server_owned_cost(
    tmp_path: Path,
) -> None:
    repository, service = await _service(tmp_path)
    offer = await service.preview_controlled_live(
        actor_id="admin",
        capability=QualificationCapability.CHAT,
        route_revision=_revision(QualificationCapability.CHAT, "Qwen/Qwen3.5-35B-A3B"),
        fixture_ids=("chat-identity-001", "chat-style-001"),
        max_input_tokens=1000,
        max_output_tokens=500,
    )
    stored = await repository.qualification_preview(offer.preview_id)

    assert offer.confirmation_handle
    assert offer.confirmation_handle != offer.preview_id
    assert offer.fixture_count == 2
    assert offer.max_requests == 2
    assert offer.conservative_max_cost == offer.planned_max_cost
    assert offer.currency == "CNY"
    assert offer.effect_isolation == "no_qq_no_outbox_no_qzone"
    assert "api_key" not in offer.as_sanitized_dict()
    assert stored is not None
    assert stored.confirmation_handle_hash != offer.confirmation_handle
    assert stored.ceilings.conservative_max_cost == offer.planned_max_cost


@pytest.mark.asyncio
async def test_confirmation_is_single_use_and_does_not_call_providers(
    tmp_path: Path,
) -> None:
    repository, service = await _service(tmp_path)
    chat = FailIfConstructed("chat")
    offer = await service.preview_controlled_live(
        actor_id="admin",
        capability=QualificationCapability.CHAT,
        route_revision=_revision(QualificationCapability.CHAT, "Qwen/Qwen3.5-35B-A3B"),
        fixture_ids=("chat-identity-001",),
        max_input_tokens=1000,
        max_output_tokens=500,
    )
    run = await service.confirm_controlled_live(
        actor_id="admin",
        confirmation_handle=offer.confirmation_handle,
        idempotency_key="live-chat-1",
    )
    with pytest.raises(QualificationPlanningError) as reused:
        await service.confirm_controlled_live(
            actor_id="admin",
            confirmation_handle=offer.confirmation_handle,
            idempotency_key="live-chat-2",
        )
    cases = await repository.qualification_cases(run.run_id)
    vision = await repository.qualification_decision(QualificationCapability.VISION)

    assert run.state is QualificationRunState.PREPARED
    assert run.execution_mode is QualificationExecutionMode.CONTROLLED_LIVE
    assert run.capability is QualificationCapability.CHAT
    assert cases[0].fixture_id == "chat-identity-001"
    assert reused.value.reason_code is QualificationReasonCode.CONFIRMATION_REUSED
    assert vision.qualifies is False
    assert chat.name == "chat"


@pytest.mark.asyncio
async def test_foreign_actor_and_unbounded_model_cannot_start_paid_run(
    tmp_path: Path,
) -> None:
    repository, service = await _service(tmp_path)
    offer = await service.preview_controlled_live(
        actor_id="admin",
        capability=QualificationCapability.CHAT,
        route_revision=_revision(QualificationCapability.CHAT, "Qwen/Qwen3.5-35B-A3B"),
        fixture_ids=("chat-identity-001",),
        max_input_tokens=1000,
        max_output_tokens=500,
    )
    with pytest.raises(QualificationPlanningError) as foreign:
        await service.confirm_controlled_live(
            actor_id="other-admin",
            confirmation_handle=offer.confirmation_handle,
            idempotency_key="live-foreign",
        )
    with pytest.raises(QualificationPlanningError) as unbounded:
        await service.preview_controlled_live(
            actor_id="admin",
            capability=QualificationCapability.CHAT,
            route_revision=_revision(QualificationCapability.CHAT, "Qwen/Qwen2.5-7B-Instruct"),
            fixture_ids=("chat-identity-001",),
            max_input_tokens=1000,
            max_output_tokens=500,
            client_unit_price="0.01",
        )
    runs = await repository.qualification_run("missing")

    assert foreign.value.reason_code is QualificationReasonCode.CONFIRMATION_ACTOR_MISMATCH
    assert unbounded.value.reason_code is QualificationReasonCode.COST_UNBOUNDED
    assert runs is None
