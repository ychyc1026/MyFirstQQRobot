from __future__ import annotations

import ast
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from _support.owner import BOT_QQ, OWNER_QQ
from _support.paths import SOURCE_ROOT
from _support.qualification_runs import NOW, SUITE_BY_CAPABILITY, ceilings, prepare_run
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application import safe_configuration_fingerprint
from ych_bot.config import Settings
from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationExecutionMode,
    QualificationRunState,
)
from ych_bot.domain.readiness import (
    CapabilityScope,
    EvidenceFreshness,
    LauncherPreflightResult,
    ProbeStatus,
    ReadinessProbeResult,
)
from ych_bot.infrastructure.models import ModelProtectionSnapshot
from ych_bot.qualification.catalog import load_bundled_qualification_catalog
from ych_bot.qualification.fakes import (
    FailIfConstructed,
    FakeChatGateway,
    FakeImageGateway,
    FakeProviderBehavior,
    FakeStatsGateway,
    FakeVisionGateway,
)
from ych_bot.qualification.runner import QualificationRunner
from ych_bot.qualification.safety import QualificationLiveGuards

PROCESS_ID = "qualify-acceptance-process"
LAUNCHER_CODES = (
    "launcher.configuration",
    "launcher.database",
    "launcher.managed_paths",
    "launcher.admin_auth",
    "launcher.port",
)
RUNNER_ROOT = SOURCE_ROOT / "qualification"
EFFECT_TABLES = (
    "messages",
    "outbox",
    "reply_runs",
    "qzone_posts",
    "proactive_message_tasks",
    "persona_profiles",
    "memory_records",
    "knowledge_documents",
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        database_path=tmp_path / "storage" / "runtime" / "ych.sqlite3",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        admin_access_token="qa-token",
        onebot_access_token="configured-but-disabled",
        chat_api_protocol="openai_compatible",
        chat_api_base="https://api.siliconflow.cn/v1",
        chat_model="Qwen/Qwen3.5-35B-A3B",
        chat_model_enabled=False,
        vision_api_protocol="openai_compatible",
        vision_api_base="https://api.siliconflow.cn/v1",
        vision_model="zai-org/GLM-4.5V",
        vision_model_enabled=False,
        image_api_protocol="openai_compatible",
        image_api_base="https://api.siliconflow.cn/v1",
        image_model="Kwai-Kolors/Kolors",
        image_model_enabled=False,
        stats_api_protocol="openai_compatible",
        stats_api_base="https://api.siliconflow.cn/v1",
        stats_model="Qwen/Qwen3.5-35B-A3B",
        stats_model_enabled=False,
        model_network_enabled=False,
        web_search_enabled=False,
        outbound_enabled=False,
        reply_worker_enabled=False,
        qzone_publish_enabled=False,
        qzone_profile_collection_enabled=False,
        qzone_worker_enabled=False,
        owner_reports_enabled=False,
        owner_report_worker_enabled=False,
        proactive_scheduler_enabled=False,
        knowledge_worker_enabled=False,
        image_orphan_scan_enabled=False,
    )


def _launcher(settings: Settings) -> LauncherPreflightResult:
    freshness = EvidenceFreshness(observed_at=NOW, expires_at=NOW + timedelta(minutes=2))
    probes = tuple(
        ReadinessProbeResult(
            probe_code=code,
            status=ProbeStatus.PASS,
            capability_scope=CapabilityScope.LOCAL_RUNTIME,
            freshness=freshness,
            source="synthetic-launcher",
            source_revision=f"{code}-qualify-acceptance-v1",
            safe_detail="synthetic no-write launcher evidence",
        )
        for code in LAUNCHER_CODES
    )
    return LauncherPreflightResult(
        process_instance_id=PROCESS_ID,
        configuration_fingerprint=safe_configuration_fingerprint(settings),
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=2),
        probes=probes,
    )


def _forbidden_constructor(name: str, calls: list[str]):
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        calls.append(name)
        raise AssertionError(f"real external adapter was constructed: {name}")

    return forbidden


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/session",
        headers={"Authorization": "Bearer qa-token"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in EFFECT_TABLES
        }


def _closed_protection(route: str) -> ModelProtectionSnapshot:
    return ModelProtectionSnapshot(
        route=route,
        day_key="2026-09-05",
        state="closed",
        requests_used=0,
        request_limit=200,
        requests_rejected=0,
        tokens_used=0,
        token_limit=10_000,
        consecutive_failures=0,
        failure_threshold=3,
        active_calls=0,
        last_failure_at=None,
        open_until=None,
    )


def test_qualification_package_does_not_import_delivery_clients() -> None:
    forbidden = {
        "ych_bot.infrastructure.napcat",
        "ych_bot.application.qzone",
        "ych_bot.application.proactive",
        "ych_bot.application.reports",
        "ych_bot.application.privacy",
    }
    imported: set[str] = set()
    for path in RUNNER_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
    assert forbidden.isdisjoint(imported)


def test_production_composition_preview_confirm_never_constructs_external_clients(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import ych_bot.api.app as app_module

    calls: list[str] = []
    monkeypatch.setattr(
        app_module, "OpenAICompatibleChatGateway", _forbidden_constructor("chat-model", calls)
    )
    monkeypatch.setattr(
        app_module, "OpenAICompatibleImageGateway", _forbidden_constructor("image-model", calls)
    )
    monkeypatch.setattr(app_module, "WebSearchClient", _forbidden_constructor("web-search", calls))
    settings = _settings(tmp_path)
    app = create_app(
        settings,
        process_instance_id=PROCESS_ID,
        launcher_result=_launcher(settings),
        clock=lambda: NOW,
        napcat_factory=_forbidden_constructor("napcat", calls),
    )

    with TestClient(app) as client:
        assert app.state.napcat_client.constructed is False
        before = _counts(settings.database_path)
        headers = _login(client)
        suites = client.get("/api/v1/qualification/suites", headers=headers)
        assert suites.status_code == 200
        assert {item["suite_id"] for item in suites.json()["items"]} == {
            "chat-shadow-v1",
            "vision-shadow-v1",
            "stats-shadow-v1",
            "image-shadow-v1",
        }
        preview = client.post(
            "/api/v1/qualification/previews",
            headers=headers,
            json={
                "capability": "chat",
                "fixture_ids": ["chat-identity-001"],
                "max_input_tokens": 800,
                "max_output_tokens": 200,
                "passed": True,
            },
        )
        assert preview.status_code == 200
        assert preview.json()["activates_production"] is False
        confirmed = client.post(
            "/api/v1/qualification/confirmations",
            headers=headers,
            json={
                "confirmation_handle": preview.json()["confirmation_handle"],
                "idempotency_key": "acceptance-chat-live",
                "passed": True,
            },
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["state"] == "prepared"
        assert confirmed.json()["execution_mode"] == "controlled_live"
        runtime = client.get("/api/v1/reply-runtime", headers=headers)
        assert runtime.status_code == 200
        assert runtime.json()["effective_mode"] == "observe_only"
        assert runtime.json()["configuration_ceiling"] == "observe_only"
        owner = client.post(
            "/api/v1/control/commands",
            headers=headers,
            json={"action": "model.status", "arguments": {}},
        )
        assert owner.status_code == 200
        assert owner.json()["data"]["can_start_paid_run"] is False
        vision = next(
            item
            for item in client.get("/api/v1/qualification/decisions", headers=headers).json()[
                "items"
            ]
            if item["capability"] == "vision"
        )
        assert vision["qualifies"] is False
        assert app.state.napcat_client.constructed is False
        assert app.state.settings.chat_model_enabled is False
        assert app.state.settings.outbound_enabled is False

    assert calls == []
    assert _counts(settings.database_path) == before


@pytest.mark.asyncio
async def test_fake_suites_and_confirmed_live_use_only_synthetic_fixtures(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    app = create_app(
        settings,
        process_instance_id=PROCESS_ID,
        launcher_result=_launcher(settings),
        clock=lambda: NOW,
        napcat_factory=_forbidden_constructor("napcat", []),
    )
    catalog = load_bundled_qualification_catalog()
    repository = app.state.repository

    with TestClient(app) as client:
        before = _counts(settings.database_path)
        headers = _login(client)
        preview = client.post(
            "/api/v1/qualification/previews",
            headers=headers,
            json={
                "capability": "chat",
                "fixture_ids": ["chat-identity-001"],
                "max_input_tokens": 800,
                "max_output_tokens": 200,
            },
        )
        confirmed = client.post(
            "/api/v1/qualification/confirmations",
            headers=headers,
            json={
                "confirmation_handle": preview.json()["confirmation_handle"],
                "idempotency_key": "acceptance-confirmed-live",
            },
        )
        live_run_id = confirmed.json()["run_id"]
        stored = await repository.qualification_run(live_run_id)
        assert stored is not None
        live_runner = QualificationRunner(
            repository=repository,
            catalog=catalog,
            chat_gateway=FakeChatGateway(),
            vision_gateway=FailIfConstructed("vision"),
            stats_gateway=FailIfConstructed("stats"),
            image_gateway=FailIfConstructed("image"),
            live_guards=QualificationLiveGuards(
                emergency_paused=False,
                current_revision=stored.route_revision,
                protection=_closed_protection("chat"),
            ),
        )
        live = await live_runner.execute_controlled_live_run(live_run_id, now=NOW)
        evidence = await repository.qualification_cost_evidence(live_run_id)
        assert live.execution_mode is QualificationExecutionMode.CONTROLLED_LIVE
        assert live.state in {QualificationRunState.PASSED, QualificationRunState.FAILED}
        assert evidence is not None
        assert evidence["price_catalog_revision"]
        assert int(evidence["request_count"]) <= 1

        gateways = {
            QualificationCapability.CHAT: FakeChatGateway(),
            QualificationCapability.VISION: FakeVisionGateway(),
            QualificationCapability.STATS: FakeStatsGateway(),
            QualificationCapability.IMAGE: FakeImageGateway(),
        }
        for capability, gateway in gateways.items():
            kwargs: dict[str, object] = {
                "capability": capability,
                "fixture_ids": (catalog.suite(SUITE_BY_CAPABILITY[capability]).fixture_ids[0],),
                "run_id": f"fake-{capability.value}",
                "preview_id": f"preview-fake-{capability.value}",
            }
            if capability is QualificationCapability.CHAT:
                kwargs["chat"] = gateway
            elif capability is QualificationCapability.VISION:
                kwargs["vision"] = gateway
            elif capability is QualificationCapability.STATS:
                kwargs["stats"] = gateway
            else:
                kwargs["image"] = gateway
                kwargs["run_ceilings"] = ceilings(
                    max_requests=1,
                    max_images=1,
                    max_input_tokens=None,
                    max_output_tokens=None,
                )
            _repository, runner, *_ = await prepare_run(tmp_path / capability.value, **kwargs)
            result = await runner.execute_fake_run(f"fake-{capability.value}", now=NOW)
            assert result.state in {
                QualificationRunState.PASSED,
                QualificationRunState.FAILED,
            }
            assert gateway.calls == 1

        failing = FakeChatGateway(FakeProviderBehavior.TIMEOUT)
        _repository, timeout_runner, *_ = await prepare_run(
            tmp_path / "timeout",
            chat=failing,
            run_id="fake-timeout",
            preview_id="preview-timeout",
        )
        timed_out = await timeout_runner.execute_fake_run("fake-timeout", now=NOW)
        assert timed_out.state is QualificationRunState.FAILED

        decisions = client.get("/api/v1/qualification/decisions", headers=headers)
        vision = next(item for item in decisions.json()["items"] if item["capability"] == "vision")
        assert vision["qualifies"] is False
        assert (
            client.get("/api/v1/reply-runtime", headers=headers).json()["effective_mode"]
            == "observe_only"
        )

    assert _counts(settings.database_path) == before
    assert app.state.settings.chat_model_enabled is False
