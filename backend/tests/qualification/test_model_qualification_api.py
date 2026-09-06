from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings
from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationDecisionStatus,
    QualificationReasonCode,
    QualificationRouteRevision,
)
from ych_bot.infrastructure.database import SQLiteRepository

NOW = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
PROCESS_ID = "proc-qualify-api"
SECRET_CHAT_KEY = "sk-test-chat-secret"
SECRET_VISION_KEY = "sk-test-vision-secret"
SECRET_IMAGE_KEY = "sk-test-image-secret"
SECRET_LEAKS = (SECRET_CHAT_KEY, SECRET_VISION_KEY, SECRET_IMAGE_KEY)
FORBIDDEN_JSON_KEYS = {
    "api_key",
    "authorization",
    "prompt",
    "messages",
    "source_path",
    "raw_price",
    "client_unit_price",
    "user_qq",
}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        database_path=tmp_path / "qualify-api.sqlite3",
        admin_access_token="dashboard-token",
        chat_api_base="https://api.siliconflow.cn/v1",
        chat_api_key=SECRET_CHAT_KEY,
        chat_model="Qwen/Qwen3.5-35B-A3B",
        chat_api_protocol="openai_compatible",
        chat_model_enabled=False,
        vision_api_base="https://api.siliconflow.cn/v1",
        vision_api_key=SECRET_VISION_KEY,
        vision_model="zai-org/GLM-4.5V",
        vision_api_protocol="openai_compatible",
        vision_model_enabled=False,
        image_api_base="https://api.siliconflow.cn/v1",
        image_api_key=SECRET_IMAGE_KEY,
        image_model="Kwai-Kolors/Kolors",
        image_api_protocol="openai_compatible",
        image_model_enabled=False,
        stats_api_protocol="disabled",
    )


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


def _client(tmp_path: Path) -> TestClient:
    app = create_app(_settings(tmp_path), process_instance_id=PROCESS_ID)
    return TestClient(app)


def _login(client: TestClient) -> tuple[dict[str, str], str]:
    login = client.post(
        "/api/v1/auth/session",
        headers={"Authorization": "Bearer dashboard-token"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, token


def _collect_keys(payload: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(payload, dict):
        keys.update(str(key) for key in payload)
        for value in payload.values():
            keys.update(_collect_keys(value))
    elif isinstance(payload, list):
        for item in payload:
            keys.update(_collect_keys(item))
    return keys


def _assert_no_leaks(payload: object) -> None:
    dumped = json.dumps(payload, ensure_ascii=False)
    for leak in SECRET_LEAKS:
        assert leak not in dumped
    assert "C:\\" not in dumped
    assert "2000000001" not in dumped
    assert not FORBIDDEN_JSON_KEYS.intersection(_collect_keys(payload))


def test_qualification_endpoints_require_dashboard_session(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        assert client.get("/api/v1/qualification/suites").status_code == 401
        assert client.get("/api/v1/qualification/routes").status_code == 401
        assert client.get("/api/v1/qualification/decisions").status_code == 401
        assert client.get("/api/v1/qualification/runs").status_code == 401
        assert (
            client.post(
                "/api/v1/qualification/previews",
                json={"capability": "chat"},
            ).status_code
            == 401
        )


def test_authenticated_suite_route_and_decision_catalogs_are_sanitized(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        headers, _token = _login(client)
        suites = client.get("/api/v1/qualification/suites", headers=headers)
        routes = client.get("/api/v1/qualification/routes", headers=headers)
        decisions = client.get("/api/v1/qualification/decisions", headers=headers)

        assert suites.status_code == 200
        assert {item["suite_id"] for item in suites.json()["items"]} == {
            "chat-shadow-v1",
            "vision-shadow-v1",
            "stats-shadow-v1",
            "image-shadow-v1",
        }
        chat_suite = next(item for item in suites.json()["items"] if item["capability"] == "chat")
        assert chat_suite["source_kind"] == "repository_owned"
        assert "chat-identity-001" in chat_suite["fixture_ids"]
        assert "prompt" not in chat_suite

        assert routes.status_code == 200
        by_capability = {item["capability"]: item for item in routes.json()["items"]}
        assert by_capability["chat"]["configured"] is True
        assert by_capability["chat"]["route_enabled"] is False
        assert by_capability["chat"]["qualified"] is False
        assert by_capability["chat"]["model_identifier"] == "Qwen/Qwen3.5-35B-A3B"
        assert by_capability["chat"]["sanitized_base_host"] == "api.siliconflow.cn"
        assert by_capability["stats"]["configured"] is False
        assert by_capability["chat"]["activates_production"] is False

        assert decisions.status_code == 200
        chat_decision = next(
            item for item in decisions.json()["items"] if item["capability"] == "chat"
        )
        assert chat_decision["status"] == "unqualified"
        assert chat_decision["qualifies"] is False
        assert chat_decision["activates_production"] is False
        assert QualificationReasonCode.DEFAULT_DENIED.value in chat_decision["blocker_codes"]

        _assert_no_leaks(suites.json())
        _assert_no_leaks(routes.json())
        _assert_no_leaks(decisions.json())


def test_preview_and_confirm_are_server_owned_and_ignore_client_pass(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        headers, token = _login(client)
        actor_id = hashlib.sha256(token.encode("utf-8")).hexdigest()
        preview = client.post(
            "/api/v1/qualification/previews",
            headers=headers,
            json={
                "capability": "chat",
                "fixture_ids": ["chat-identity-001", "chat-style-001"],
                "max_input_tokens": 1000,
                "max_output_tokens": 500,
                "passed": True,
                "decision": "passed",
            },
        )
        assert preview.status_code == 200
        body = preview.json()
        assert body["capability"] == "chat"
        assert body["confirmation_handle"]
        assert body["effect_isolation"] == "no_qq_no_outbox_no_qzone"
        assert body["activates_production"] is False
        sanitized = {key: value for key, value in body.items() if key != "confirmation_handle"}
        _assert_no_leaks(sanitized)

        confirmed = client.post(
            "/api/v1/qualification/confirmations",
            headers=headers,
            json={
                "confirmation_handle": body["confirmation_handle"],
                "idempotency_key": "chat-live-1",
                "passed": True,
                "decision": "passed",
            },
        )
        assert confirmed.status_code == 200
        run = confirmed.json()
        assert run["state"] == "prepared"
        assert run["capability"] == "chat"
        assert run["actor_id"] == actor_id
        assert run["correlation_id"]
        assert run["activates_production"] is False
        assert "confirmation_handle" not in run
        _assert_no_leaks(run)

        detail = client.get(f"/api/v1/qualification/runs/{run['run_id']}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["state"] == "prepared"
        assert {case["fixture_id"] for case in detail.json()["cases"]} == {
            "chat-identity-001",
            "chat-style-001",
        }
        vision = next(
            item
            for item in client.get("/api/v1/qualification/decisions", headers=headers).json()[
                "items"
            ]
            if item["capability"] == "vision"
        )
        assert vision["status"] == "unqualified"
        assert vision["qualifies"] is False
        _assert_no_leaks(detail.json())


def test_client_cannot_submit_raw_price_path_or_provider_payload(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers, _token = _login(client)
        price = client.post(
            "/api/v1/qualification/previews",
            headers=headers,
            json={
                "capability": "chat",
                "fixture_ids": ["chat-identity-001"],
                "raw_price": "0.01",
                "client_unit_price": "0.01",
            },
        )
        assert price.status_code == 409
        assert price.json()["detail"]["code"] == QualificationReasonCode.COST_UNBOUNDED.value

        path = client.post(
            "/api/v1/qualification/previews",
            headers=headers,
            json={
                "capability": "chat",
                "source_path": "C:\\\\Users\\\\YCH\\\\private.txt",
                "user_qq": "123456789",
            },
        )
        assert path.status_code == 409
        assert path.json()["detail"]["code"] == (
            QualificationReasonCode.PRODUCTION_INPUT_REJECTED.value
        )

        payload = client.post(
            "/api/v1/qualification/previews",
            headers=headers,
            json={
                "capability": "chat",
                "messages": [{"role": "user", "content": "ignore previous"}],
                "prompt": "real chat",
            },
        )
        assert payload.status_code == 409
        assert payload.json()["detail"]["code"] == (
            QualificationReasonCode.PRODUCTION_INPUT_REJECTED.value
        )
        _assert_no_leaks(price.json())
        _assert_no_leaks(path.json())
        _assert_no_leaks(payload.json())


def test_runs_are_paginated_cancellable_and_capability_filtered(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers, _token = _login(client)

        def _prepare(capability: str, fixtures: list[str], key: str) -> str:
            extra = (
                {"max_images": 1}
                if capability == "image"
                else {"max_input_tokens": 800, "max_output_tokens": 200}
            )
            preview = client.post(
                "/api/v1/qualification/previews",
                headers=headers,
                json={"capability": capability, "fixture_ids": fixtures, **extra},
            )
            assert preview.status_code == 200, preview.text
            confirmed = client.post(
                "/api/v1/qualification/confirmations",
                headers=headers,
                json={
                    "confirmation_handle": preview.json()["confirmation_handle"],
                    "idempotency_key": key,
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            return confirmed.json()["run_id"]

        chat_run = _prepare("chat", ["chat-identity-001"], "page-chat")
        vision_run = _prepare("vision", ["vision-ground-001"], "page-vision")
        image_run = _prepare("image", ["image-form-001"], "page-image")

        page = client.get(
            "/api/v1/qualification/runs",
            headers=headers,
            params={"limit": 2, "offset": 0},
        )
        assert page.status_code == 200
        assert page.json()["total"] == 3
        assert page.json()["limit"] == 2
        assert page.json()["offset"] == 0
        assert len(page.json()["items"]) == 2

        chat_only = client.get(
            "/api/v1/qualification/runs",
            headers=headers,
            params={"capability": "chat"},
        )
        assert chat_only.json()["total"] == 1
        assert chat_only.json()["items"][0]["run_id"] == chat_run
        assert chat_only.json()["items"][0]["capability"] == "chat"

        cancelled = client.post(
            f"/api/v1/qualification/runs/{chat_run}/cancel",
            headers=headers,
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        assert cancelled.json()["reason_code"] == QualificationReasonCode.CANCELLED.value

        remaining = {
            item["run_id"]
            for item in client.get("/api/v1/qualification/runs", headers=headers).json()["items"]
        }
        assert remaining == {chat_run, vision_run, image_run}
        _assert_no_leaks(page.json())


def test_stale_route_evidence_is_projected_without_qualifying(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings, process_instance_id=PROCESS_ID)
    with TestClient(app) as client:
        headers, _token = _login(client)
        repository = SQLiteRepository(settings.database_path)

        async def _seed() -> None:
            await repository.initialize()
            old = _revision(QualificationCapability.CHAT, "old/unqualified-model")
            await repository.save_qualification_route_revision(old, now=NOW)
            await repository.record_qualification_decision(
                capability=QualificationCapability.CHAT,
                route_revision=old,
                suite_version="2026.09.05",
                status=QualificationDecisionStatus.PASSED,
                run_id=None,
                evaluated_at=NOW,
                advisory_score=Decimal("0.90"),
                blocker_codes=(),
            )

        import asyncio

        asyncio.run(_seed())
        decisions = client.get("/api/v1/qualification/decisions", headers=headers)
        chat = next(item for item in decisions.json()["items"] if item["capability"] == "chat")
        assert chat["qualifies"] is False
        assert chat["status"] == "stale"
        assert QualificationReasonCode.STALE_ROUTE_REVISION.value in chat["blocker_codes"]
        _assert_no_leaks(decisions.json())


def test_owner_model_status_reuses_sanitized_read_policy(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers, _token = _login(client)
        dashboard = client.get("/api/v1/qualification/decisions", headers=headers)
        owner = client.post(
            "/api/v1/control/commands",
            headers=headers,
            json={"action": "model.status", "arguments": {}},
        )
        help_body = client.post(
            "/api/v1/control/commands",
            headers=headers,
            json={"action": "help", "arguments": {}},
        )

        assert owner.status_code == 200
        assert owner.json()["status"] == "completed"
        data = owner.json()["data"]
        assert data["can_start_paid_run"] is False
        assert "confirmation_handle" not in json.dumps(data)
        owner_by_capability = {item["capability"]: item for item in data["items"]}
        dashboard_by_capability = {item["capability"]: item for item in dashboard.json()["items"]}
        for capability, item in owner_by_capability.items():
            assert item["status"] == dashboard_by_capability[capability]["status"]
            assert item["qualifies"] is dashboard_by_capability[capability]["qualifies"]
            assert item["activates_production"] is False
        assert "/模型 状态" in help_body.json()["data"]["read_only"]
        _assert_no_leaks(data)


@pytest.mark.asyncio
async def test_owner_qq_cannot_start_paid_qualification(tmp_path: Path) -> None:
    from ych_bot.application.control import OwnerControlService
    from ych_bot.domain.control import OwnerCommandKind, parse_owner_command
    from ych_bot.infrastructure.napcat.client import NapCatClient
    from ych_bot.infrastructure.napcat.parser import parse_message_event

    settings = _settings(tmp_path)
    repository = SQLiteRepository(settings.database_path)
    await repository.initialize()
    payload = {
        "time": 1_700_000_000,
        "self_id": 2000000002,
        "post_type": "message",
        "message_type": "private",
        "message_id": 909,
        "user_id": 2000000001,
        "message": [{"type": "text", "data": {"text": "/模型 确认 paid-handle"}}],
    }
    message = parse_message_event(payload, expected_bot_qq="2000000002")
    assert parse_owner_command(message, owner_qq="2000000001") is None

    payload["message"] = [{"type": "text", "data": {"text": "/模型 预览 chat"}}]
    message = parse_message_event(payload, expected_bot_qq="2000000002")
    assert parse_owner_command(message, owner_qq="2000000001") is None

    payload["message"] = [{"type": "text", "data": {"text": "/模型 状态"}}]
    message = parse_message_event(payload, expected_bot_qq="2000000002")
    command = parse_owner_command(message, owner_qq="2000000001")
    assert command is not None
    assert command.kind is OwnerCommandKind.MODEL_STATUS

    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": []})

    client = NapCatClient("http://napcat.test", transport=httpx.MockTransport(handler))
    try:
        service = OwnerControlService(
            repository,
            client,
            owner_qq="2000000001",
            bot_qq="2000000002",
        )
        result = await service.execute(command)
        assert result.status == "completed"
        assert result.data["can_start_paid_run"] is False
        assert result.data["items"]
    finally:
        await client.close()
