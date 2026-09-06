import asyncio
import socket
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ych_bot.application.launcher_preflight import (
    LauncherPreflightService,
    safe_configuration_fingerprint,
)
from ych_bot.application.readiness import (
    CapabilityGateProbe,
    DurablePauseProbe,
    LauncherEvidenceProbe,
    OneBotIdentityProbe,
    ProbeContext,
    ReadinessService,
    SharedLauncherEvidence,
    WorkerCeilingProbe,
)
from ych_bot.config import Settings
from ych_bot.domain.readiness import (
    CapabilityScope,
    EvidenceFreshness,
    ProbeStatus,
    ReadinessDecisionStatus,
    ReadinessProbeResult,
    ReadinessProfile,
    ReadinessReasonCode,
)
from ych_bot.infrastructure.database import SQLiteRepository

BOT_QQ = "2000000002"
NOW = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "project_root": tmp_path,
        "database_path": tmp_path / "storage" / "runtime" / "ych.sqlite3",
        "admin_port": free_port(),
        "admin_access_token": "local-dashboard-token",
    }
    values.update(overrides)
    return Settings(**values)


def create_legacy_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?, 'legacy')",
            ((version,) for version in range(1, 5)),
        )


def seed_bot(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO bots(qq, label, quota_user_reply, created_at, updated_at)
            VALUES(?, 'test', 'quota exceeded', ?, ?)
            """,
            (BOT_QQ, NOW.isoformat(), NOW.isoformat()),
        )


class StubProbe:
    def __init__(
        self,
        code: str,
        *,
        status: ProbeStatus = ProbeStatus.PASS,
        observed_at: datetime | None = None,
        expires_at: datetime | None = None,
        delay: float = 0,
    ) -> None:
        self.code = code
        self.status = status
        self.observed_at = observed_at
        self.expires_at = expires_at
        self.delay = delay

    async def collect(self, context: ProbeContext) -> ReadinessProbeResult:
        if self.delay:
            await asyncio.sleep(self.delay)
        observed = self.observed_at or context.now
        expires = self.expires_at or observed + timedelta(minutes=1)
        return ReadinessProbeResult(
            probe_code=self.code,
            status=self.status,
            capability_scope=context.capability_scope,
            freshness=EvidenceFreshness(observed_at=observed, expires_at=expires),
            source="test",
            source_revision=f"{self.code}-v1",
            safe_detail=f"{self.code} test evidence",
        )


def pass_probes(
    profile: ReadinessProfile,
    scope: CapabilityScope,
    *,
    observed_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict[str, StubProbe]:
    return {
        code: StubProbe(code, observed_at=observed_at, expires_at=expires_at)
        for code in ReadinessService.required_probe_codes(profile, scope)
    }


@pytest.mark.asyncio
async def test_launcher_preflight_is_no_write_and_accepts_available_local_runtime(
    tmp_path: Path,
) -> None:
    active_settings = settings(tmp_path)
    before = {item.relative_to(tmp_path) for item in tmp_path.rglob("*")}

    result = await LauncherPreflightService(active_settings, clock=lambda: NOW).inspect(
        process_instance_id="process-a"
    )

    after = {item.relative_to(tmp_path) for item in tmp_path.rglob("*")}
    assert result.passed is True
    assert before == after
    assert not active_settings.database_path.exists()
    assert {probe.probe_code for probe in result.probes} == {
        "launcher.configuration",
        "launcher.database",
        "launcher.managed_paths",
        "launcher.admin_auth",
        "launcher.port",
    }


@pytest.mark.asyncio
async def test_launcher_blocks_port_conflict_invalid_auth_and_unbacked_migration(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "storage" / "runtime" / "ych.sqlite3"
    create_legacy_database(database_path)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        active_settings = settings(
            tmp_path,
            database_path=database_path,
            admin_port=int(listener.getsockname()[1]),
            admin_access_token="",
            migration_backup_enabled=False,
        )
        result = await LauncherPreflightService(active_settings, clock=lambda: NOW).inspect(
            process_instance_id="process-a"
        )

    statuses = {probe.probe_code: probe.status for probe in result.probes}
    assert statuses["launcher.port"] is ProbeStatus.BLOCKED
    assert statuses["launcher.admin_auth"] is ProbeStatus.BLOCKED
    assert statuses["launcher.database"] is ProbeStatus.BLOCKED
    assert not (tmp_path / "storage" / "backups").exists()


@pytest.mark.asyncio
async def test_matching_launcher_evidence_recognizes_current_listener_and_mismatch_blocks(
    tmp_path: Path,
) -> None:
    active_settings = settings(tmp_path)
    launcher = await LauncherPreflightService(active_settings, clock=lambda: NOW).inspect(
        process_instance_id="process-a"
    )
    context = ProbeContext(
        bot_qq=BOT_QQ,
        process_instance_id="process-a",
        profile=ReadinessProfile.LOCAL_START,
        capability_scope=CapabilityScope.LOCAL_RUNTIME,
        now=NOW + timedelta(seconds=5),
    )
    port_probe = LauncherEvidenceProbe(
        "launcher.port",
        launcher_result=launcher,
        expected_process_instance_id="process-a",
        expected_configuration_fingerprint=safe_configuration_fingerprint(active_settings),
    )

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((active_settings.admin_host, active_settings.admin_port))
        listener.listen()
        assert (await port_probe.collect(context)).status is ProbeStatus.PASS

    mismatched = LauncherEvidenceProbe(
        "launcher.port",
        launcher_result=launcher,
        expected_process_instance_id="process-b",
        expected_configuration_fingerprint=safe_configuration_fingerprint(active_settings),
    )
    result = await mismatched.collect(context)
    assert result.status is ProbeStatus.BLOCKED
    assert result.source_revision == "process-mismatch"


@pytest.mark.asyncio
async def test_stale_launcher_evidence_refreshes_when_current_process_owns_port(
    tmp_path: Path,
) -> None:
    active_settings = settings(tmp_path)
    clock = {"now": NOW}
    service = LauncherPreflightService(active_settings, clock=lambda: clock["now"])
    startup = await service.inspect(process_instance_id="process-a")
    evidence = SharedLauncherEvidence(result=startup)

    async def refresh():
        clock["now"] = NOW + timedelta(minutes=3)
        return await service.inspect(process_instance_id="process-a", accept_current_listener=True)

    evidence.refresher = refresh
    stale_context = ProbeContext(
        bot_qq=BOT_QQ,
        process_instance_id="process-a",
        profile=ReadinessProfile.CONTROLLED_REAL_EFFECT,
        capability_scope=CapabilityScope.CHAT_MODEL,
        now=NOW + timedelta(minutes=3),
    )
    frozen = LauncherEvidenceProbe(
        "launcher.port",
        evidence=evidence,
        expected_process_instance_id="process-a",
        expected_configuration_fingerprint=safe_configuration_fingerprint(active_settings),
    )
    without_refresh = LauncherEvidenceProbe(
        "launcher.port",
        launcher_result=startup,
        expected_process_instance_id="process-a",
        expected_configuration_fingerprint=safe_configuration_fingerprint(active_settings),
    )
    assert (await without_refresh.collect(stale_context)).status is ProbeStatus.STALE

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((active_settings.admin_host, active_settings.admin_port))
        listener.listen()
        refreshed = await frozen.collect(stale_context)
    assert refreshed.status is ProbeStatus.PASS
    assert "current process" in refreshed.safe_detail


@pytest.mark.asyncio
async def test_profiles_fail_closed_independently_and_report_configured_vs_active(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "readiness.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()
    seed_bot(database_path)
    scope = CapabilityScope.QQ_REPLY
    probes: dict[str, object] = pass_probes(ReadinessProfile.CONTROLLED_REAL_EFFECT, scope)
    probes["onebot.identity"] = OneBotIdentityProbe(
        state=lambda: {
            "connected": True,
            "token_configured": True,
            "authenticated_bot_qq": "99999999",
            "connection_revision": 7,
        }
    )
    probes["capability.gate"] = CapabilityGateProbe(
        state=lambda _scope: {
            "configured": True,
            "active": False,
            "circuit_available": True,
            "revision": "gate-4",
        }
    )
    service = ReadinessService(
        repository,
        bot_qq=BOT_QQ,
        process_instance_id="process-a",
        probes=probes,
        clock=lambda: NOW,
    )

    decision = await service.evaluate(
        ReadinessProfile.CONTROLLED_REAL_EFFECT,
        capability_scope=scope,
    )

    assert decision.status is ReadinessDecisionStatus.BLOCKED
    blocker_codes = {item.code for item in decision.blockers}
    assert ReadinessReasonCode.BOT_IDENTITY_MISMATCH in blocker_codes
    assert ReadinessReasonCode.NETWORK_GATE_CLOSED in blocker_codes
    capability = next(item for item in decision.probes if item.probe_code == "capability.gate")
    assert dict(capability.evidence) == {
        "active": "false",
        "circuit_available": "true",
        "configured": "true",
    }


@pytest.mark.asyncio
async def test_offline_shadow_needs_no_live_provider_and_durable_pause_is_independent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "profiles.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()
    seed_bot(database_path)
    offline_scope = CapabilityScope.OFFLINE_INFERENCE
    offline_probes = pass_probes(ReadinessProfile.OFFLINE_SHADOW, offline_scope)
    assert "onebot.identity" not in offline_probes
    assert "capability.gate" not in offline_probes
    offline = ReadinessService(
        repository,
        bot_qq=BOT_QQ,
        process_instance_id="offline-process",
        probes=offline_probes,
        clock=lambda: NOW,
    )
    decision = await offline.evaluate(
        ReadinessProfile.OFFLINE_SHADOW,
        capability_scope=offline_scope,
    )
    assert decision.status is ReadinessDecisionStatus.PASSED

    reply_scope = CapabilityScope.QQ_REPLY
    controlled_probes: dict[str, object] = pass_probes(
        ReadinessProfile.CONTROLLED_REAL_EFFECT, reply_scope
    )
    controlled_probes["runtime.durable_pause"] = DurablePauseProbe(
        state=lambda _scope: {"paused": True, "revision": "pause-3"}
    )
    paused = ReadinessService(
        repository,
        bot_qq=BOT_QQ,
        process_instance_id="controlled-process",
        probes=controlled_probes,
        clock=lambda: NOW,
    )
    paused_decision = await paused.evaluate(
        ReadinessProfile.CONTROLLED_REAL_EFFECT,
        capability_scope=reply_scope,
    )
    assert ReadinessReasonCode.DURABLE_PAUSE_ACTIVE in {
        item.code for item in paused_decision.blockers
    }

    ceilings = WorkerCeilingProbe(configured_modes={"reply": None})
    ceiling_result = await ceilings.collect(
        ProbeContext(
            bot_qq=BOT_QQ,
            process_instance_id="controlled-process",
            profile=ReadinessProfile.LOCAL_START,
            capability_scope=CapabilityScope.LOCAL_RUNTIME,
            now=NOW,
        )
    )
    assert ceiling_result.status is ProbeStatus.BLOCKED


@pytest.mark.asyncio
async def test_probe_timeout_is_unknown_without_erasing_other_results(tmp_path: Path) -> None:
    database_path = tmp_path / "timeout.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()
    seed_bot(database_path)
    scope = CapabilityScope.LOCAL_RUNTIME
    probes = pass_probes(ReadinessProfile.LOCAL_START, scope)
    probes["runtime.database"] = StubProbe("runtime.database", delay=0.1)
    service = ReadinessService(
        repository,
        bot_qq=BOT_QQ,
        process_instance_id="process-a",
        probes=probes,
        clock=lambda: NOW,
        per_probe_timeout_seconds=0.01,
        max_concurrency=2,
    )

    decision = await service.evaluate(
        ReadinessProfile.LOCAL_START,
        capability_scope=scope,
    )

    statuses = {item.probe_code: item.status for item in decision.probes}
    assert statuses["runtime.database"] is ProbeStatus.UNKNOWN
    assert all(
        status is ProbeStatus.PASS
        for code, status in statuses.items()
        if code != "runtime.database"
    )


@pytest.mark.asyncio
async def test_concurrent_refreshes_receive_distinct_immutable_revisions(tmp_path: Path) -> None:
    database_path = tmp_path / "concurrent.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()
    seed_bot(database_path)
    scope = CapabilityScope.LOCAL_RUNTIME
    service = ReadinessService(
        repository,
        bot_qq=BOT_QQ,
        process_instance_id="process-a",
        probes=pass_probes(ReadinessProfile.LOCAL_START, scope),
        clock=lambda: NOW,
    )

    first, second = await asyncio.gather(
        service.evaluate(ReadinessProfile.LOCAL_START, capability_scope=scope),
        service.evaluate(ReadinessProfile.LOCAL_START, capability_scope=scope),
    )

    assert {first.revision, second.revision} == {1, 2}
    assert first.decision_id != second.decision_id


@pytest.mark.asyncio
async def test_sleep_expiry_and_restart_never_reuse_prior_process_pass(tmp_path: Path) -> None:
    database_path = tmp_path / "restart.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()
    seed_bot(database_path)
    scope = CapabilityScope.LOCAL_RUNTIME
    clock = [NOW]
    fixed = pass_probes(
        ReadinessProfile.LOCAL_START,
        scope,
        observed_at=NOW,
        expires_at=NOW + timedelta(seconds=10),
    )
    first = ReadinessService(
        repository,
        bot_qq=BOT_QQ,
        process_instance_id="process-a",
        probes=fixed,
        clock=lambda: clock[0],
    )
    passed = await first.evaluate(ReadinessProfile.LOCAL_START, capability_scope=scope)
    assert passed.status is ReadinessDecisionStatus.PASSED

    clock[0] = NOW + timedelta(hours=8)
    stale = await first.evaluate(ReadinessProfile.LOCAL_START, capability_scope=scope)
    assert stale.status is ReadinessDecisionStatus.BLOCKED
    assert {item.code for item in stale.blockers} == {ReadinessReasonCode.EVIDENCE_STALE}

    second = ReadinessService(
        repository,
        bot_qq=BOT_QQ,
        process_instance_id="process-b",
        probes={},
        clock=lambda: clock[0],
    )
    restarted = await second.evaluate(ReadinessProfile.LOCAL_START, capability_scope=scope)
    assert restarted.status is ReadinessDecisionStatus.BLOCKED
    assert restarted.revision == 1
    assert {item.code for item in restarted.blockers} == {
        ReadinessReasonCode.REQUIRED_PROBE_UNKNOWN
    }
