from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application import OperationalApiError
from ych_bot.config import Settings
from ych_bot.domain.managed_artifacts import (
    ArtifactOwnerScope,
    ArtifactReferenceState,
    ArtifactRetentionState,
    ArtifactVerificationState,
    ManagedArtifact,
    ManagedArtifactType,
)
from ych_bot.domain.readiness import (
    CapabilityScope,
    EvidenceFreshness,
    ProbeStatus,
    ReadinessDecision,
    ReadinessDecisionStatus,
    ReadinessProbeResult,
    ReadinessProfile,
)

BOT_QQ = "2000000002"
OWNER_QQ = "2000000001"
USER_A = "10000001"
USER_B = "10000002"
PROCESS_ID = "operational-api-process"
NOW = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        database_path=tmp_path / "storage" / "runtime" / "ych.sqlite3",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        admin_access_token="dashboard-token",
    )


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/session",
        headers={"Authorization": "Bearer dashboard-token"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _write_backup(root: Path, *, index: int, created_at: datetime) -> None:
    root.mkdir(parents=True, exist_ok=True)
    stem = f"ych-before-v32-202608{31 - index:02d}T100000000000Z"
    backup = root / f"{stem}.sqlite3"
    with sqlite3.connect(backup) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence VALUES('operational-api')")
    content = backup.read_bytes()
    backup.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "format": "ych-migration-backup-v1",
                "backup_file": backup.name,
                "sha256": _digest(content),
                "byte_count": len(content),
                "source_schema_version": 31,
                "target_schema_version": 32,
                "created_at": created_at.isoformat(),
                "integrity_result": "ok",
            }
        ),
        encoding="utf-8",
    )


def _seed_backups(tmp_path: Path) -> None:
    root = tmp_path / "storage" / "backups" / "migrations"
    for index, created_at in enumerate(
        (NOW, NOW - timedelta(days=1), NOW - timedelta(days=2), NOW - timedelta(days=100))
    ):
        _write_backup(root, index=index, created_at=created_at)


def _stale_decision(process_instance_id: str, *, revision: int = 1) -> ReadinessDecision:
    observed = NOW - timedelta(minutes=10)
    probe = ReadinessProbeResult(
        probe_code="offline.database",
        status=ProbeStatus.PASS,
        capability_scope=CapabilityScope.OFFLINE_INFERENCE,
        freshness=EvidenceFreshness(
            observed_at=observed,
            expires_at=observed + timedelta(seconds=10),
        ),
        source="synthetic-api-test",
        source_revision="safe-revision",
        safe_detail="expired synthetic evidence",
        remediation_code="refresh_probe",
        evidence=(("private_body", "must-not-be-projected"),),
    )
    return ReadinessDecision(
        decision_id=f"stale-{process_instance_id}-{revision}",
        bot_qq=BOT_QQ,
        process_instance_id=process_instance_id,
        profile=ReadinessProfile.OFFLINE_SHADOW,
        capability_scope=CapabilityScope.OFFLINE_INFERENCE,
        scope_hash=f"scope-{process_instance_id}",
        revision=revision,
        status=ReadinessDecisionStatus.PASSED,
        evaluated_at=observed,
        probes=(probe,),
        correlation_id=f"correlation-{process_instance_id}",
    )


def _user_artifact(user_qq: str, suffix: str) -> ManagedArtifact:
    return ManagedArtifact(
        artifact_id=f"artifact-{suffix}",
        artifact_type=ManagedArtifactType.PRIVACY_EXPORT,
        owner_scope=ArtifactOwnerScope.USER,
        owner_qq=user_qq,
        relative_path=f"storage/exports/{user_qq}/export-{suffix}.json",
        bundle_key=f"private-bundle-{suffix}",
        size_bytes=123,
        digest_sha256=_digest(f"private-{suffix}".encode()),
        manifest_type="private-manifest-type",
        created_at=NOW,
        verification_state=ArtifactVerificationState.VERIFIED,
        reference_state=ArtifactReferenceState.UNREFERENCED,
        retention_state=ArtifactRetentionState.RETAIN,
    )


def test_readiness_apis_auth_scope_staleness_history_and_audit(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), process_instance_id=PROCESS_ID)
    with TestClient(app) as client:
        unauthenticated = client.get("/api/v1/operations/readiness/summary")
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["detail"]["code"] == "admin_session_invalid"
        headers = _login(client)

        client.portal.call(
            app.state.repository.create_readiness_decision,
            _stale_decision(PROCESS_ID),
        )
        client.portal.call(
            app.state.repository.create_readiness_decision,
            _stale_decision("historical-process"),
        )

        current = client.get(
            "/api/v1/operations/readiness/current",
            headers=headers,
            params={
                "profile": "offline_shadow",
                "capability_scope": "offline_inference",
                "bot_qq": BOT_QQ,
                "process_instance_id": PROCESS_ID,
            },
        )
        assert current.status_code == 200
        detail = current.json()
        assert detail["recorded_status"] == "passed"
        assert detail["status"] == "blocked"
        assert detail["stale_probe_count"] == 1
        assert detail["probes"][0]["status"] == "stale"
        assert "evidence" not in detail["probes"][0]
        assert "must-not-be-projected" not in current.text
        assert "scope_hash" not in current.text

        history = client.get(
            "/api/v1/operations/readiness/history",
            headers=headers,
            params={
                "profile": "offline_shadow",
                "capability_scope": "offline_inference",
                "include_historical_instances": True,
                "bot_qq": BOT_QQ,
            },
        )
        assert history.status_code == 200
        assert {item["current_instance"] for item in history.json()["items"]} == {True, False}
        assert all("probes" not in item for item in history.json()["items"])

        wrong_bot = client.get(
            "/api/v1/operations/readiness/summary",
            headers=headers,
            params={"bot_qq": "99999999"},
        )
        assert wrong_bot.status_code == 403
        assert wrong_bot.json()["detail"]["code"] == "bot_scope_mismatch"

        wrong_process = client.post(
            "/api/v1/operations/readiness/refresh",
            headers=headers,
            json={
                "bot_qq": BOT_QQ,
                "process_instance_id": "stale-dashboard-process",
                "profile": "controlled_real_effect",
                "capability_scope": "qq_reply",
            },
        )
        assert wrong_process.status_code == 409
        assert wrong_process.json()["detail"]["code"] == "process_instance_mismatch"

        refreshed = client.post(
            "/api/v1/operations/readiness/refresh",
            headers=headers,
            json={
                "bot_qq": BOT_QQ,
                "process_instance_id": PROCESS_ID,
                "profile": "controlled_real_effect",
                "capability_scope": "qq_reply",
            },
        )
        assert refreshed.status_code == 200
        refreshed_body = refreshed.json()
        assert refreshed_body["process_instance_id"] == PROCESS_ID
        assert refreshed_body["correlation_id"]

    with sqlite3.connect(_settings(tmp_path).database_path) as connection:
        row = connection.execute(
            "SELECT details_json FROM audit_log WHERE action = 'readiness.api_refreshed'"
        ).fetchone()
    assert row is not None
    audit = json.loads(row[0])
    assert audit["actor_id"] == OWNER_QQ
    assert audit["correlation_id"] == refreshed_body["correlation_id"]
    assert "token" not in json.dumps(audit).lower()


def test_artifact_api_isolates_users_and_sanitizes_dtos(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), process_instance_id=PROCESS_ID)
    with TestClient(app) as client:
        headers = _login(client)
        client.portal.call(
            partial(
                app.state.repository.upsert_managed_artifact,
                _user_artifact(USER_A, "a"),
                now=NOW,
            )
        )
        client.portal.call(
            partial(
                app.state.repository.upsert_managed_artifact,
                _user_artifact(USER_B, "b"),
                now=NOW,
            )
        )

        response = client.get(
            "/api/v1/operations/artifacts",
            headers=headers,
            params={"owner_scope": "user", "owner_qq": USER_A, "bot_qq": BOT_QQ},
        )
        assert response.status_code == 200
        body = response.json()
        assert [item["artifact_id"] for item in body["items"]] == ["artifact-a"]
        assert body["paths_exposed"] is False
        serialized = response.text
        for forbidden in (
            "relative_path",
            "digest_sha256",
            "bundle_key",
            "manifest_type",
            "private-bundle",
        ):
            assert forbidden not in serialized
        assert str(tmp_path) not in serialized

        missing_owner = client.get(
            "/api/v1/operations/artifacts",
            headers=headers,
            params={"owner_scope": "user"},
        )
        assert missing_owner.status_code == 422
        assert missing_owner.json()["detail"]["code"] == "user_owner_required"


def test_retention_api_preview_confirm_history_is_single_use_and_recoverable(
    tmp_path: Path,
) -> None:
    _seed_backups(tmp_path)
    app = create_app(_settings(tmp_path), process_instance_id=PROCESS_ID)
    with TestClient(app) as client:
        headers = _login(client)
        refresh = client.post(
            "/api/v1/operations/artifacts/refresh",
            headers=headers,
            json={
                "bot_qq": BOT_QQ,
                "process_instance_id": PROCESS_ID,
                "owner_scope": "system",
            },
        )
        assert refresh.status_code == 200
        refresh_body = refresh.json()
        assert refresh_body["filesystem_mutated"] is False
        assert refresh_body["correlation_id"]
        assert len(refresh_body["items"]) == 4

        preview = client.post(
            "/api/v1/operations/artifacts/retention/preview",
            headers=headers,
            json={
                "bot_qq": BOT_QQ,
                "process_instance_id": PROCESS_ID,
                "target_batch_type": "migration_backups",
                "owner_scope": "system",
            },
        )
        assert preview.status_code == 200
        preview_body = preview.json()
        assert preview_body["candidate_count"] == 1
        assert preview_body["recoverable_quarantine"] is True
        assert preview_body["permanent_deletion"] is False
        assert preview_body["correlation_id"]
        assert "token_hash" not in preview.text
        assert "relative_path" not in preview.text
        assert str(tmp_path) not in preview.text

        invalid_session = client.post(
            "/api/v1/operations/artifacts/retention/confirm",
            json={
                "bot_qq": BOT_QQ,
                "process_instance_id": PROCESS_ID,
                "confirmation_token": preview_body["confirmation_token"],
                "expected_revision": preview_body["revision"],
            },
        )
        assert invalid_session.status_code == 401
        assert invalid_session.json()["detail"]["code"] == "admin_session_invalid"

        confirmed = client.post(
            "/api/v1/operations/artifacts/retention/confirm",
            headers=headers,
            json={
                "bot_qq": BOT_QQ,
                "process_instance_id": PROCESS_ID,
                "confirmation_token": preview_body["confirmation_token"],
                "expected_revision": preview_body["revision"],
            },
        )
        assert confirmed.status_code == 200
        confirmed_body = confirmed.json()
        assert confirmed_body["status"] == "quarantined"
        assert confirmed_body["recoverable"] is True
        assert confirmed_body["permanent_deletion"] is False
        assert confirmed_body["correlation_id"] == preview_body["correlation_id"]

        reused = client.post(
            "/api/v1/operations/artifacts/retention/confirm",
            headers=headers,
            json={
                "bot_qq": BOT_QQ,
                "process_instance_id": PROCESS_ID,
                "confirmation_token": preview_body["confirmation_token"],
                "expected_revision": preview_body["revision"],
            },
        )
        assert reused.status_code == 409
        assert reused.json()["detail"]["code"] == "preview_used"

        history = client.get(
            "/api/v1/operations/artifacts/quarantine",
            headers=headers,
            params={"owner_scope": "system", "state": "quarantined"},
        )
        assert history.status_code == 200
        history_body = history.json()
        assert history_body["count"] == 1
        assert history_body["paths_exposed"] is False
        assert history_body["items"][0]["correlation_id"] == preview_body["correlation_id"]
        for forbidden in (
            "source_relative_path",
            "quarantine_relative_path",
            "expected_digest_sha256",
            "confirmation_token",
            "token_hash",
        ):
            assert forbidden not in history.text

    with sqlite3.connect(_settings(tmp_path).database_path) as connection:
        refresh_audit = connection.execute(
            "SELECT details_json FROM audit_log WHERE action = 'artifacts.api_refreshed'"
        ).fetchone()
    assert refresh_audit is not None
    assert json.loads(refresh_audit[0])["correlation_id"] == refresh_body["correlation_id"]


def test_dashboard_and_owner_context_share_current_owner_policy(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), process_instance_id=PROCESS_ID)
    with TestClient(app) as client:
        headers = _login(client)
        dashboard = client.portal.call(
            partial(
                app.state.operational_api.admin_context,
                headers["Authorization"],
                bot_qq=BOT_QQ,
                process_instance_id=PROCESS_ID,
                require_current_instance=True,
            )
        )
        owner = client.portal.call(
            partial(
                app.state.operational_api.owner_context,
                OWNER_QQ,
                bot_qq=BOT_QQ,
                process_instance_id=PROCESS_ID,
            )
        )
        assert dashboard.actor_id == owner.actor_id == OWNER_QQ
        assert dashboard.actor_source == "dashboard"
        assert owner.actor_source == "owner_qq"

        with pytest.raises(OperationalApiError) as denied:
            client.portal.call(
                partial(
                    app.state.operational_api.owner_context,
                    USER_A,
                    bot_qq=BOT_QQ,
                    process_instance_id=PROCESS_ID,
                )
            )
        assert denied.value.code == "owner_authorization_required"


def test_retention_confirmation_rechecks_current_owner_binding(tmp_path: Path) -> None:
    _seed_backups(tmp_path)
    app = create_app(_settings(tmp_path), process_instance_id=PROCESS_ID)
    oldest = (
        tmp_path
        / "storage"
        / "backups"
        / "migrations"
        / ("ych-before-v32-20260828T100000000000Z.sqlite3")
    )
    with TestClient(app) as client:
        headers = _login(client)
        preview = client.post(
            "/api/v1/operations/artifacts/retention/preview",
            headers=headers,
            json={
                "bot_qq": BOT_QQ,
                "process_instance_id": PROCESS_ID,
                "target_batch_type": "migration_backups",
                "owner_scope": "system",
            },
        ).json()
        client.portal.call(
            partial(
                app.state.repository.set_owner_qq,
                USER_A,
                updated_by="test-owner-change",
            )
        )

        rejected = client.post(
            "/api/v1/operations/artifacts/retention/confirm",
            headers=headers,
            json={
                "bot_qq": BOT_QQ,
                "process_instance_id": PROCESS_ID,
                "confirmation_token": preview["confirmation_token"],
                "expected_revision": preview["revision"],
            },
        )
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "unauthorized_actor"
        assert oldest.is_file()
