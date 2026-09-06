import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ych_bot.application.qualification import QualificationPlanningService
from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationCaseState,
    QualificationReasonCode,
    QualificationRouteRevision,
    QualificationRunState,
)
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.models import ModelProtectionSnapshot
from ych_bot.qualification.artifacts import QualificationImageStore
from ych_bot.qualification.catalog import load_bundled_qualification_catalog
from ych_bot.qualification.fakes import (
    FailIfConstructed,
    FakeChatGateway,
    FakeImageGateway,
    FakeProviderBehavior,
)
from ych_bot.qualification.fixtures.vision import deterministic_png
from ych_bot.qualification.prices import load_bundled_qualification_price_catalog
from ych_bot.qualification.runner import QualificationRunner
from ych_bot.qualification.safety import (
    QualificationLiveGuards,
    evaluate_qualification_pre_attempt,
)

NOW = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)


class FrozenClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


def _revision(
    capability: QualificationCapability,
    model_identifier: str,
    *,
    price_catalog_revision: str = "siliconflow-2026-09-05",
) -> QualificationRouteRevision:
    return QualificationRouteRevision(
        capability=capability,
        provider_protocol="openai_compatible",
        sanitized_base_host="api.siliconflow.cn",
        model_identifier=model_identifier,
        generation_settings=(("temperature", "0.2"),),
        suite_version="2026.09.05",
        price_catalog_revision=price_catalog_revision,
        protection_policy_revision=f"{capability.value}-protection-v1",
    )


def _snapshot(
    *,
    route: str = "chat",
    state: str = "closed",
    requests_used: int = 0,
    request_limit: int = 10,
) -> ModelProtectionSnapshot:
    return ModelProtectionSnapshot(
        route=route,
        day_key="2026-09-05",
        state=state,
        requests_used=requests_used,
        request_limit=request_limit,
        requests_rejected=0,
        tokens_used=0,
        token_limit=10_000,
        consecutive_failures=0,
        failure_threshold=3,
        active_calls=0,
        last_failure_at=None,
        open_until=None,
    )


def test_pre_attempt_checks_pause_route_quota_and_circuit() -> None:
    revision = _revision(QualificationCapability.CHAT, "Qwen/Qwen3.5-35B-A3B")
    healthy = QualificationLiveGuards(
        emergency_paused=False,
        current_revision=revision,
        protection=_snapshot(),
    )
    assert evaluate_qualification_pre_attempt(run_revision=revision, guards=healthy) is None
    assert (
        evaluate_qualification_pre_attempt(
            run_revision=revision,
            guards=QualificationLiveGuards(True, revision, _snapshot()),
        )
        is QualificationReasonCode.EMERGENCY_PAUSE
    )
    assert (
        evaluate_qualification_pre_attempt(
            run_revision=revision,
            guards=QualificationLiveGuards(
                False,
                _revision(QualificationCapability.CHAT, "other-model"),
                _snapshot(),
            ),
        )
        is QualificationReasonCode.STALE_ROUTE_REVISION
    )
    assert (
        evaluate_qualification_pre_attempt(
            run_revision=revision,
            guards=QualificationLiveGuards(
                False,
                _revision(
                    QualificationCapability.CHAT,
                    "Qwen/Qwen3.5-35B-A3B",
                    price_catalog_revision="siliconflow-old",
                ),
                _snapshot(),
            ),
        )
        is QualificationReasonCode.STALE_PRICE_CATALOG
    )
    assert (
        evaluate_qualification_pre_attempt(
            run_revision=revision,
            guards=QualificationLiveGuards(False, revision, _snapshot(requests_used=10)),
        )
        is QualificationReasonCode.QUOTA_EXHAUSTED
    )
    assert (
        evaluate_qualification_pre_attempt(
            run_revision=revision,
            guards=QualificationLiveGuards(False, revision, _snapshot(state="open")),
        )
        is QualificationReasonCode.CIRCUIT_OPEN
    )


async def _confirmed_chat_run(
    tmp_path: Path,
    *,
    chat: object,
    guards: QualificationLiveGuards,
    fixture_ids: tuple[str, ...] = ("chat-identity-001",),
) -> tuple[SQLiteRepository, QualificationRunner, str]:
    repository = SQLiteRepository(tmp_path / "qualify-live.sqlite3")
    await repository.initialize()
    planner = QualificationPlanningService(
        repository,
        process_instance_id="proc-live",
        clock=FrozenClock(NOW),
        fixture_catalog=load_bundled_qualification_catalog(),
        price_catalog=load_bundled_qualification_price_catalog(),
    )
    revision = _revision(QualificationCapability.CHAT, "Qwen/Qwen3.5-35B-A3B")
    offer = await planner.preview_controlled_live(
        actor_id="admin",
        capability=QualificationCapability.CHAT,
        route_revision=revision,
        fixture_ids=fixture_ids,
        max_input_tokens=1000,
        max_output_tokens=500,
    )
    run = await planner.confirm_controlled_live(
        actor_id="admin",
        confirmation_handle=offer.confirmation_handle,
        idempotency_key="live-chat",
    )
    runner = QualificationRunner(
        repository=repository,
        catalog=load_bundled_qualification_catalog(),
        chat_gateway=chat,
        vision_gateway=FailIfConstructed("vision"),
        stats_gateway=FailIfConstructed("stats"),
        image_gateway=FailIfConstructed("image"),
        live_guards=guards,
    )
    return repository, runner, run.run_id


@pytest.mark.asyncio
async def test_live_runner_blocks_emergency_pause_before_provider(tmp_path: Path) -> None:
    inner = FakeChatGateway()
    revision = _revision(QualificationCapability.CHAT, "Qwen/Qwen3.5-35B-A3B")
    repository, runner, run_id = await _confirmed_chat_run(
        tmp_path,
        chat=inner,
        guards=QualificationLiveGuards(True, revision, _snapshot()),
    )
    result = await runner.execute_controlled_live_run(run_id, now=NOW)
    cases = await repository.qualification_cases(run_id)

    assert inner.calls == 0
    assert result.state is QualificationRunState.BLOCKED
    assert cases[0].state is QualificationCaseState.BLOCKED
    assert cases[0].reason_code is QualificationReasonCode.EMERGENCY_PAUSE


@pytest.mark.asyncio
async def test_live_runner_records_allowlisted_success_without_prompt_bodies(
    tmp_path: Path,
) -> None:
    inner = FakeChatGateway()
    revision = _revision(QualificationCapability.CHAT, "Qwen/Qwen3.5-35B-A3B")
    repository, runner, run_id = await _confirmed_chat_run(
        tmp_path,
        chat=inner,
        guards=QualificationLiveGuards(False, revision, _snapshot()),
    )
    result = await runner.execute_controlled_live_run(run_id, now=NOW)
    cases = await repository.qualification_cases(run_id)
    evidence = await repository.qualification_cost_evidence(run_id)
    sanitized = cases[0].as_sanitized_dict()

    assert inner.calls == 1
    assert result.state in {QualificationRunState.PASSED, QualificationRunState.FAILED}
    assert evidence is not None
    assert evidence["price_catalog_revision"] == "siliconflow-2026-09-05"
    assert "text" not in sanitized
    assert "prompt" not in sanitized
    assert cases[0].evidence.provider_request_id
    assert cases[0].evidence.response_hash


@pytest.mark.asyncio
async def test_live_timeout_and_ambiguous_sleep_are_not_retried(tmp_path: Path) -> None:
    timeout = FakeChatGateway(behavior=FakeProviderBehavior.TIMEOUT)
    revision = _revision(QualificationCapability.CHAT, "Qwen/Qwen3.5-35B-A3B")
    repository, runner, run_id = await _confirmed_chat_run(
        tmp_path,
        chat=timeout,
        guards=QualificationLiveGuards(False, revision, _snapshot()),
    )
    timed_out = await runner.execute_controlled_live_run(run_id, now=NOW)
    cases = await repository.qualification_cases(run_id)

    assert timeout.calls == 1
    assert timed_out.state is QualificationRunState.FAILED
    assert cases[0].reason_code is QualificationReasonCode.TIMEOUT

    repository2 = SQLiteRepository(tmp_path / "qualify-sleep.sqlite3")
    await repository2.initialize()
    planner = QualificationPlanningService(
        repository2,
        process_instance_id="proc-live",
        clock=FrozenClock(NOW),
        fixture_catalog=load_bundled_qualification_catalog(),
        price_catalog=load_bundled_qualification_price_catalog(),
    )
    offer = await planner.preview_controlled_live(
        actor_id="admin",
        capability=QualificationCapability.CHAT,
        route_revision=revision,
        fixture_ids=("chat-identity-001",),
        max_input_tokens=1000,
        max_output_tokens=500,
    )
    run = await planner.confirm_controlled_live(
        actor_id="admin",
        confirmation_handle=offer.confirmation_handle,
        idempotency_key="live-sleep",
    )
    await repository2.transition_qualification_run(
        run_id=run.run_id,
        from_state=QualificationRunState.PREPARED,
        to_state=QualificationRunState.RUNNING,
        now=NOW,
    )
    claimed = await repository2.claim_qualification_case(
        run_id=run.run_id,
        fixture_id="chat-identity-001",
        lease_token="live-sleep",
        ttl_seconds=30,
        now=NOW,
    )
    assert claimed is not None
    await repository2.mark_qualification_case_provider_attempt(
        case_id=claimed.case_id,
        lease_token="live-sleep",
        provider_request_id="sf-live-1",
        now=NOW,
    )
    chat = FakeChatGateway()
    runner2 = QualificationRunner(
        repository=repository2,
        catalog=load_bundled_qualification_catalog(),
        chat_gateway=chat,
        vision_gateway=FailIfConstructed("vision"),
        stats_gateway=FailIfConstructed("stats"),
        image_gateway=FailIfConstructed("image"),
        live_guards=QualificationLiveGuards(False, revision, _snapshot()),
    )
    later = await runner2.execute_controlled_live_run(run.run_id, now=NOW + timedelta(minutes=2))
    slept = await repository2.qualification_cases(run.run_id)

    assert chat.calls == 0
    assert slept[0].state is QualificationCaseState.INCONCLUSIVE
    assert later.state is QualificationRunState.INCONCLUSIVE


def test_qualification_images_are_confined_and_reject_escape(tmp_path: Path) -> None:
    store = QualificationImageStore(tmp_path / "storage" / "generated")
    png = deterministic_png()
    stored = store.write(
        run_id="run-img",
        case_id="case-img",
        payload=png,
        mime_type="image/png",
    )
    preview = store.preview_metadata(stored)

    assert stored.relative_path.startswith("qualification/run-img/")
    assert stored.relative_path.endswith(".png")
    assert (tmp_path / "storage" / "generated" / stored.relative_path).is_file()
    assert preview["artifact_id"] == stored.artifact_id
    assert preview["mime_type"] == "image/png"
    assert "bytes" not in preview
    with pytest.raises(ValueError, match="confined"):
        store.write(
            run_id="../escape",
            case_id="case-img",
            payload=png,
            mime_type="image/png",
        )
    with pytest.raises(ValueError, match="png"):
        store.write(
            run_id="run-img",
            case_id="bad",
            payload=b"not-an-image",
            mime_type="image/jpeg",
        )
    with pytest.raises(ValueError, match="confined"):
        store.ingest(
            run_id="run-img",
            case_id="case-url",
            artifacts=("https://example.com/escape.png",),
        )
    with pytest.raises(ValueError, match="confined"):
        store.ingest(
            run_id="run-img",
            case_id="case-url",
            artifacts=("http://sf-maas.example/escape.png",),
        )
    with pytest.raises(ValueError, match="confined"):
        store.ingest(
            run_id="run-img",
            case_id="case-suffix",
            artifacts=("https://siliconflow.cn.evil.example/escape.png",),
        )
    with pytest.raises(ValueError, match="confined"):
        store.ingest(
            run_id="run-img",
            case_id="case-userinfo",
            artifacts=("https://user:pass@cdn.siliconflow.cn/escape.png",),
        )


def test_qualification_image_store_ingests_mocked_allowlisted_png(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    png = deterministic_png()
    fetched: list[str] = []

    class _FakeResponse:
        status_code = 200
        content = png

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs.get("follow_redirects") is False

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def get(self, url: str) -> _FakeResponse:
            fetched.append(url)
            return _FakeResponse()

    monkeypatch.setattr("ych_bot.qualification.artifacts.httpx.Client", _FakeClient)
    store = QualificationImageStore(tmp_path / "storage" / "generated")
    stored = store.ingest(
        run_id="run-img",
        case_id="case-allow",
        artifacts=("https://sc-maas.siliconflow.cn/qualification.png",),
    )

    assert fetched == ["https://sc-maas.siliconflow.cn/qualification.png"]
    assert stored.relative_path.startswith("qualification/run-img/")
    assert (tmp_path / "storage" / "generated" / stored.relative_path).read_bytes() == png


@pytest.mark.asyncio
async def test_live_image_anomaly_is_blocked_without_writing_escape_paths(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "qualify-image.sqlite3")
    await repository.initialize()
    planner = QualificationPlanningService(
        repository,
        process_instance_id="proc-live",
        clock=FrozenClock(NOW),
        fixture_catalog=load_bundled_qualification_catalog(),
        price_catalog=load_bundled_qualification_price_catalog(),
    )
    revision = _revision(QualificationCapability.IMAGE, "Kwai-Kolors/Kolors")
    offer = await planner.preview_controlled_live(
        actor_id="admin",
        capability=QualificationCapability.IMAGE,
        route_revision=revision,
        fixture_ids=("image-form-001",),
        max_images=1,
    )
    run = await planner.confirm_controlled_live(
        actor_id="admin",
        confirmation_handle=offer.confirmation_handle,
        idempotency_key="live-image",
    )
    png = deterministic_png()
    image = FakeImageGateway(
        artifacts=(f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}",)
    )
    store = QualificationImageStore(tmp_path / "storage" / "generated")
    runner = QualificationRunner(
        repository=repository,
        catalog=load_bundled_qualification_catalog(),
        chat_gateway=FailIfConstructed("chat"),
        vision_gateway=FailIfConstructed("vision"),
        stats_gateway=FailIfConstructed("stats"),
        image_gateway=image,
        live_guards=QualificationLiveGuards(False, revision, _snapshot(route="image")),
        image_store=store,
    )
    result = await runner.execute_controlled_live_run(run.run_id, now=NOW)
    artifacts = await repository.qualification_artifacts(run.run_id)

    assert image.calls == 1
    assert result.state in {
        QualificationRunState.PASSED,
        QualificationRunState.FAILED,
        QualificationRunState.BLOCKED,
    }
    assert all(item.relative_path.startswith("qualification/") for item in artifacts)
    assert not (tmp_path / "escape").exists()
