from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.application import safe_configuration_fingerprint
from ych_bot.config import Settings
from ych_bot.domain.readiness import (
    CapabilityScope,
    EvidenceFreshness,
    LauncherPreflightResult,
    ProbeStatus,
    ReadinessProbeResult,
)

BOT_QQ = "2000000002"
OWNER_QQ = "2000000001"
PROCESS_ID = "production-acceptance-process"
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
LAUNCHER_CODES = (
    "launcher.configuration",
    "launcher.database",
    "launcher.managed_paths",
    "launcher.admin_auth",
    "launcher.port",
)


class FixedProbe:
    def __init__(
        self,
        code: str,
        *,
        status: ProbeStatus,
        evidence: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.code = code
        self._status = status
        self._evidence = evidence

    async def collect(self, context: Any) -> ReadinessProbeResult:
        return ReadinessProbeResult(
            probe_code=self.code,
            status=self._status,
            capability_scope=context.capability_scope,
            freshness=EvidenceFreshness(
                observed_at=context.now,
                expires_at=context.now + timedelta(minutes=2),
            ),
            source="production-acceptance-fake",
            source_revision=f"{self.code}-fixed-v1",
            safe_detail="controlled acceptance evidence",
            evidence=self._evidence,
        )


def _settings(tmp_path: Path, *, database_path: Path | None = None) -> Settings:
    return Settings(
        project_root=tmp_path,
        database_path=database_path or tmp_path / "storage" / "runtime" / "ych.sqlite3",
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        admin_access_token="qa-token",
        onebot_access_token="configured-but-disabled",
        chat_api_protocol="openai_compatible",
        chat_api_base="https://model.invalid/v1",
        chat_model="acceptance-chat",
        chat_model_enabled=True,
        vision_api_protocol="openai_compatible",
        vision_api_base="https://model.invalid/v1",
        vision_model="acceptance-vision",
        vision_model_enabled=True,
        image_api_protocol="openai_compatible",
        image_api_base="https://model.invalid/v1",
        image_model="acceptance-image",
        image_model_enabled=True,
        stats_api_protocol="openai_compatible",
        stats_api_base="https://model.invalid/v1",
        stats_model="acceptance-stats",
        stats_model_enabled=True,
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
            source_revision=f"{code}-acceptance-v1",
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


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/session",
        headers={"Authorization": "Bearer qa-token"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _write_migration_pair(root: Path, *, day: int, created_at: datetime) -> None:
    root.mkdir(parents=True, exist_ok=True)
    stem = f"ych-before-v32-202608{day:02d}T120000000000Z"
    backup = root / f"{stem}.sqlite3"
    with sqlite3.connect(backup) as connection:
        connection.execute("CREATE TABLE acceptance(value TEXT NOT NULL)")
        connection.execute("INSERT INTO acceptance VALUES('no-network')")
    content = backup.read_bytes()
    backup.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "format": "ych-migration-backup-v1",
                "backup_file": backup.name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "byte_count": len(content),
                "source_schema_version": 31,
                "target_schema_version": 32,
                "created_at": created_at.isoformat(),
                "integrity_result": "ok",
            }
        ),
        encoding="utf-8",
    )


def _seed_retention_candidates(tmp_path: Path) -> None:
    root = tmp_path / "storage" / "backups" / "migrations"
    for day, created_at in (
        (31, NOW),
        (30, NOW - timedelta(days=1)),
        (29, NOW - timedelta(days=2)),
        (28, NOW - timedelta(days=100)),
    ):
        _write_migration_pair(root, day=day, created_at=created_at)


def _forbidden_constructor(name: str, calls: list[str]):
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        calls.append(name)
        raise AssertionError(f"real external adapter was constructed: {name}")

    return forbidden


def test_production_composition_is_no_network_through_retention_and_dashboard_reporting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import ych_bot.api.app as app_module

    _seed_retention_candidates(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        app_module,
        "OpenAICompatibleChatGateway",
        _forbidden_constructor("chat-model", calls),
    )
    monkeypatch.setattr(
        app_module,
        "OpenAICompatibleImageGateway",
        _forbidden_constructor("image-model", calls),
    )
    monkeypatch.setattr(
        app_module,
        "WebSearchClient",
        _forbidden_constructor("web-search", calls),
    )
    napcat_factory = _forbidden_constructor("napcat", calls)
    settings = _settings(tmp_path)
    app = create_app(
        settings,
        process_instance_id=PROCESS_ID,
        launcher_result=_launcher(settings),
        clock=lambda: NOW,
        napcat_factory=napcat_factory,
        readiness_probe_overrides={
            "onebot.identity": FixedProbe(
                "onebot.identity",
                status=ProbeStatus.BLOCKED,
                evidence=(
                    ("connected", "true"),
                    ("token_configured", "true"),
                    ("identity_matches", "false"),
                ),
            )
        },
    )

    with TestClient(app) as client:
        assert app.state.napcat_client.constructed is False
        assert app.state.web_search.enabled is False
        headers = _login(client)

        local = client.get("/api/v1/operations/readiness/summary", headers=headers)
        assert local.status_code == 200
        local_profile = next(
            item for item in local.json()["profiles"] if item["profile"] == "local_start"
        )
        assert local_profile["status"] == "passed"

        controlled = client.post(
            "/api/v1/operations/readiness/refresh",
            headers=headers,
            json={
                "bot_qq": BOT_QQ,
                "process_instance_id": PROCESS_ID,
                "profile": "controlled_real_effect",
                "capability_scope": "qq_reply",
            },
        )
        assert controlled.status_code == 200
        assert controlled.json()["status"] == "blocked"

        refreshed = client.post(
            "/api/v1/operations/artifacts/refresh",
            headers=headers,
            json={
                "bot_qq": BOT_QQ,
                "process_instance_id": PROCESS_ID,
                "owner_scope": "system",
            },
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["filesystem_mutated"] is False
        assert len(refreshed.json()["items"]) == 4

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
        assert confirmed.json()["status"] == "quarantined"
        assert client.portal.call(app.state.retention.reconcile_interrupted_batches) == {
            "quarantined": 0,
            "rolled_back": 0,
            "blocked": 0,
        }
        assert app.state.napcat_client.constructed is False

    assert calls == []
    with sqlite3.connect(settings.database_path) as connection:
        report_runtime = connection.execute(
            """
            SELECT owner_report_runtime.delivery_policy,
                   owner_report_runtime.delivery_status
            FROM owner_report_runtime
            JOIN owner_reports ON owner_reports.id = owner_report_runtime.report_id
            WHERE owner_reports.category = 'managed_artifact_retention'
            """
        ).fetchall()
    assert report_runtime
    assert all(row == ("dashboard_only", "suppressed") for row in report_runtime)


def test_migration_required_startup_backs_up_legacy_data_without_external_clients(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "storage" / "runtime" / "ych.sqlite3"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?, 'legacy')",
            ((version,) for version in range(1, 5)),
        )
        connection.execute("CREATE TABLE legacy_data(value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_data VALUES('rollback-evidence')")
    settings = _settings(tmp_path, database_path=database_path)
    calls: list[str] = []
    app = create_app(
        settings,
        process_instance_id=PROCESS_ID,
        launcher_result=_launcher(settings),
        clock=lambda: NOW,
        napcat_factory=_forbidden_constructor("napcat", calls),
    )

    with TestClient(app):
        assert app.state.napcat_client.constructed is False

    assert calls == []
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 34
        assert connection.execute("SELECT value FROM legacy_data").fetchone()[0] == (
            "rollback-evidence"
        )
    backups = list((tmp_path / "storage" / "backups" / "migrations").glob("*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM legacy_data").fetchone()[0] == (
            "rollback-evidence"
        )
