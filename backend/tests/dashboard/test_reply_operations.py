from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application import (
    ReplyOperationsService,
    ReplyOrchestrationService,
    ReplyPipelineFlags,
)
from ych_bot.config import Settings
from ych_bot.domain.models import MessageSegment
from ych_bot.domain.reply_pipeline import (
    ContextManifest,
    ContextPolicyDecision,
    ContextScope,
    ContextSection,
    ContextSourceClass,
    ReplyBubble,
    ReplyPlan,
    ReplyRunStage,
)
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.parser import parse_message_event

BOT_QQ = "2000000002"
OWNER_QQ = "2000000001"
USER_QQ = "123456789"


def event(message_id: int, now: datetime, text: str = "hello user") -> dict:
    return {
        "time": int(now.timestamp()),
        "self_id": int(BOT_QQ),
        "post_type": "message",
        "message_type": "private",
        "message_id": message_id,
        "user_id": int(USER_QQ),
        "message": [{"type": "text", "data": {"text": text}}],
    }


def flags(**overrides: bool) -> ReplyPipelineFlags:
    values = {
        "ingestion_enabled": True,
        "chat_model_configured": False,
        "model_network_enabled": False,
        "chat_route_enabled": False,
        "outbound_enabled": False,
        "onebot_token_configured": False,
        "reply_worker_wired": False,
    }
    values.update(overrides)
    return ReplyPipelineFlags(**values)


async def register_run(
    repository: SQLiteRepository,
    *,
    now: datetime,
    message_id: int,
    policy_snapshot: dict | None = None,
) -> tuple[str, str]:
    message = parse_message_event(event(message_id, now), expected_bot_qq=BOT_QQ)
    assert await repository.store_inbound(message)
    orchestration = ReplyOrchestrationService(repository, settle_seconds=0)
    registered = await orchestration.register_message(
        message,
        observed_at=now,
        policy_snapshot=policy_snapshot,
    )
    claimed = await orchestration.claim_ready(worker_id="ops-test-worker", now=now)
    assert claimed is not None
    return str(registered["run"]["id"]), str(claimed["lease"]["lease_token"])


async def seed_api_run(repository: SQLiteRepository) -> tuple[str, str]:
    return await register_run(
        repository,
        now=datetime.now(UTC).replace(microsecond=0),
        message_id=7002,
    )


@pytest.mark.asyncio
async def test_reply_operations_detail_exposes_evidence_without_sensitive_payloads(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "reply-operations.sqlite3")
    await repository.initialize()
    now = datetime.now(UTC).replace(microsecond=0)
    run_id, lease_token = await register_run(
        repository,
        now=now,
        message_id=7001,
        policy_snapshot={"mode": "fake", "secret": "POLICY_SECRET_VALUE"},
    )
    raw = await repository.reply_run_detail(run_id)
    assert raw is not None
    trigger_message_id = str(raw["triggers"][0]["message_id"])
    manifest = ContextManifest(
        run_id=run_id,
        conversation_key=str(raw["conversation_key"]),
        sections=(
            ContextSection(
                section_id="identity",
                source_class=ContextSourceClass.CORE_IDENTITY,
                scope=ContextScope.GLOBAL,
                content="CONTEXT_SECRET_VALUE",
                policy_decision=ContextPolicyDecision.ALLOWED,
                record_ids=("identity-record",),
            ),
            ContextSection(
                section_id="history-denied",
                source_class=ContextSourceClass.HISTORY,
                scope=ContextScope.USER,
                subject_qq=USER_QQ,
                content="",
                policy_decision=ContextPolicyDecision.DENIED,
                policy_reason="POLICY_REASON_SECRET_VALUE",
            ),
        ),
    )
    assert await repository.store_reply_context_manifest(
        manifest_id="manifest-ops",
        manifest=manifest,
        revision=1,
        rendered_content="RENDERED_CONTEXT_SECRET_VALUE",
        budget_chars=500,
        lease_token=lease_token,
    )
    assert await repository.transition_reply_run(
        run_id=run_id,
        expected_stage=ReplyRunStage.ASSEMBLING_CONTEXT,
        target_stage=ReplyRunStage.CALLING_MODEL,
        lease_token=lease_token,
        now=now,
    )
    await repository.start_inference_run(
        run_id=f"reply:{run_id}",
        source_message_id=trigger_message_id,
        conversation_key=str(raw["conversation_key"]),
        actor_qq=USER_QQ,
        mode="reply_fake",
        prompt_hash="PROMPT_HASH_SECRET_VALUE",
        model_route="fake-chat",
        bot_qq=BOT_QQ,
    )
    await repository.finish_inference_success(
        run_id=f"reply:{run_id}",
        candidate_id="candidate-ops",
        source_message_id=trigger_message_id,
        conversation_kind="private",
        target_id=USER_QQ,
        content="MODEL_CANDIDATE_SECRET_VALUE",
        provider_request_id="fake-request-1",
        input_tokens=100,
        output_tokens=10,
        safety_flags=("reply_pipeline_fake", "not_for_delivery"),
    )
    assert await repository.transition_reply_run(
        run_id=run_id,
        expected_stage=ReplyRunStage.CALLING_MODEL,
        target_stage=ReplyRunStage.PLANNING_REPLY,
        lease_token=lease_token,
        now=now,
    )
    plan = ReplyPlan(
        run_id=run_id,
        bubbles=(
            ReplyBubble(
                sequence=1,
                idempotency_key=f"{run_id}:bubble:1",
                segments=(MessageSegment("text", {"text": "REPLY_PLAN_SECRET_VALUE"}),),
            ),
        ),
    )
    assert await repository.transition_reply_run(
        run_id=run_id,
        expected_stage=ReplyRunStage.PLANNING_REPLY,
        target_stage=ReplyRunStage.CREATING_OUTBOX,
        reply_plan=plan,
        lease_token=lease_token,
        now=now,
    )
    await repository.handoff_reply_plan_to_outbox(
        run_id=run_id,
        lease_token=lease_token,
        now=now,
    )
    service = ReplyOperationsService(repository, flags=flags())

    awaiting = await service.detail(run_id)
    assert awaiting is not None
    assert awaiting["lease"]["present"] is True
    assert "lease_token" not in awaiting["lease"]
    assert awaiting["policy"]["blockers"] == [
        {"code": "context_source_denied", "source_class": "history"}
    ]
    assert "content" not in awaiting["context_manifests"][0]["sections"][0]
    assert awaiting["model"]["candidate"]["content_chars"] == len("MODEL_CANDIDATE_SECRET_VALUE")
    assert awaiting["reply_plan"]["bubbles"][0]["text_chars"] == len("REPLY_PLAN_SECRET_VALUE")

    claimed = await repository.claim_outbound()
    assert claimed is not None
    assert await repository.begin_reply_delivery(claimed.id) is not None
    await repository.finalize_reply_delivery(
        outbox_id=claimed.id,
        outcome="delivery_unknown",
        safe_detail="sanitized delivery detail",
    )
    failed = await service.detail(run_id)
    assert failed is not None
    assert failed["failure"]["category"] == "delivery_unknown"
    assert failed["delivery"][-1]["outcome"] == "delivery_unknown"
    assert failed["triggers"][0]["text_preview"] == "hello user"

    encoded = json.dumps(failed, ensure_ascii=False)
    for secret in (
        "POLICY_SECRET_VALUE",
        "CONTEXT_SECRET_VALUE",
        "POLICY_REASON_SECRET_VALUE",
        "RENDERED_CONTEXT_SECRET_VALUE",
        "PROMPT_HASH_SECRET_VALUE",
        "MODEL_CANDIDATE_SECRET_VALUE",
        "REPLY_PLAN_SECRET_VALUE",
        lease_token,
    ):
        assert secret not in encoded

    listed = await service.list_runs(stage="failed", limit=10)
    assert listed["total"] == 1
    assert listed["items"][0]["latest_delivery_outcome"] == "delivery_unknown"


@pytest.mark.asyncio
async def test_reply_readiness_reports_real_activation_blockers(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reply-readiness.sqlite3")
    await repository.initialize()
    service = ReplyOperationsService(repository, flags=flags())

    readiness = await service.readiness(
        onebot_connected=False,
        outbound_worker={"active": False},
        chat_protection={"state": "closed"},
    )

    assert readiness["safe_observation_ready"] is True
    assert readiness["production_activation_ready"] is False
    assert readiness["mode"] == "observe_only"
    codes = {item["code"] for item in readiness["blockers"]}
    assert "reply_worker_wired" in codes
    assert "model_network" in codes
    assert "outbound_gate" in codes
    assert readiness["safeguards"]["automatic_ambiguous_retry"] is False
    assert readiness["summary"]["total_runs"] == 0


def test_reply_operations_api_requires_admin_and_returns_sanitized_views(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "reply-api.sqlite3"
    settings = Settings(
        project_root=tmp_path,
        database_path=database_path,
        admin_access_token="dashboard-token",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/reply-runs").status_code == 401
        assert client.get("/api/v1/reply-pipeline/readiness").status_code == 401
        run_id, _ = client.portal.call(seed_api_run, app.state.repository)
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        listed = client.get("/api/v1/reply-runs", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["items"][0]["id"] == run_id
        detail = client.get(f"/api/v1/reply-runs/{run_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["triggers"][0]["text_preview"] == "hello user"
        readiness = client.get("/api/v1/reply-pipeline/readiness", headers=headers)
        assert readiness.status_code == 200
        assert readiness.json()["production_activation_ready"] is False
        assert (
            client.get("/api/v1/reply-runs?stage=not-a-stage", headers=headers).status_code == 422
        )
        assert client.get("/api/v1/reply-runs/missing-run", headers=headers).status_code == 404
