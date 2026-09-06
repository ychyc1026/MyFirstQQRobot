"""Fake-mode qualification run setup shared by runner and acceptance tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationCeilings,
    QualificationExecutionMode,
    QualificationPreview,
    QualificationPreviewState,
    QualificationRouteRevision,
    QualificationRun,
    QualificationRunState,
)
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.qualification.catalog import load_bundled_qualification_catalog
from ych_bot.qualification.fakes import FailIfConstructed, FakeChatGateway
from ych_bot.qualification.runner import QualificationRunner

NOW = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
SUITE_BY_CAPABILITY = {
    QualificationCapability.CHAT: "chat-shadow-v1",
    QualificationCapability.VISION: "vision-shadow-v1",
    QualificationCapability.STATS: "stats-shadow-v1",
    QualificationCapability.IMAGE: "image-shadow-v1",
}


def handle(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def revision(
    capability: QualificationCapability = QualificationCapability.CHAT,
    *,
    model_identifier: str = "fake-route",
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


def ceilings(**overrides: object) -> QualificationCeilings:
    values: dict[str, object] = {
        "max_requests": 4,
        "max_input_tokens": 200,
        "max_output_tokens": 100,
        "max_images": None,
        "conservative_max_cost": Decimal("0.10"),
    }
    values.update(overrides)
    return QualificationCeilings(**values)  # type: ignore[arg-type]


async def prepare_run(
    tmp_path: Path,
    *,
    capability: QualificationCapability = QualificationCapability.CHAT,
    fixture_ids: tuple[str, ...] = ("chat-identity-001",),
    run_ceilings: QualificationCeilings | None = None,
    expires_at: datetime | None = None,
    run_id: str = "run-fake",
    preview_id: str = "preview-fake",
    chat: object | None = None,
    vision: object | None = None,
    stats: object | None = None,
    image: object | None = None,
) -> tuple[SQLiteRepository, QualificationRunner, object, object, object, object]:
    catalog = load_bundled_qualification_catalog()
    repository = SQLiteRepository(tmp_path / f"{run_id}.sqlite3")
    await repository.initialize()
    route_revision = revision(capability)
    await repository.save_qualification_route_revision(route_revision, now=NOW)
    suite = catalog.suite(SUITE_BY_CAPABILITY[capability])
    preview_handle = handle(preview_id)
    await repository.create_qualification_preview(
        QualificationPreview(
            preview_id=preview_id,
            confirmation_handle_hash=preview_handle,
            actor_id="admin",
            process_instance_id="proc",
            capability=capability,
            route_revision=route_revision,
            suite=suite,
            policy_revision=f"{capability.value}-protection-v1",
            fixture_ids=fixture_ids,
            ceilings=run_ceilings or ceilings(max_requests=len(fixture_ids)),
            execution_mode=QualificationExecutionMode.FAKE,
            expires_at=expires_at or NOW + timedelta(minutes=10),
            state=QualificationPreviewState.READY,
        ),
        now=NOW,
    )
    consumed = await repository.consume_qualification_preview(
        confirmation_handle_hash=preview_handle,
        actor_id="admin",
        process_instance_id="proc",
        route_fingerprint=route_revision.fingerprint,
        suite_id=suite.suite_id,
        suite_version=suite.version,
        policy_revision=f"{capability.value}-protection-v1",
        now=NOW,
    )
    assert consumed is not None
    await repository.create_qualification_run(
        QualificationRun(
            run_id=run_id,
            preview_id=preview_id,
            actor_id="admin",
            process_instance_id="proc",
            capability=capability,
            route_revision=route_revision,
            suite=suite,
            execution_mode=QualificationExecutionMode.FAKE,
            state=QualificationRunState.PREPARED,
            idempotency_key=f"fake-{run_id}",
            created_at=NOW,
        ),
        now=NOW,
    )
    chat_gateway = chat if chat is not None else FakeChatGateway()
    vision_gateway = vision if vision is not None else FailIfConstructed("vision")
    stats_gateway = stats if stats is not None else FailIfConstructed("stats")
    image_gateway = image if image is not None else FailIfConstructed("image")
    if capability is not QualificationCapability.CHAT and chat is None:
        chat_gateway = FailIfConstructed("chat")
    runner = QualificationRunner(
        repository=repository,
        catalog=catalog,
        chat_gateway=chat_gateway,
        vision_gateway=vision_gateway,
        stats_gateway=stats_gateway,
        image_gateway=image_gateway,
    )
    return repository, runner, chat_gateway, vision_gateway, stats_gateway, image_gateway
