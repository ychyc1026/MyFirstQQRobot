import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationCaseState,
    QualificationCeilings,
    QualificationDecisionStatus,
    QualificationExecutionMode,
    QualificationPreview,
    QualificationPreviewState,
    QualificationRouteRevision,
    QualificationRun,
    QualificationRunState,
    QualificationSuite,
)
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.database.sqlite import LATEST_SCHEMA_VERSION

NOW = datetime(2026, 9, 5, 6, 0, tzinfo=UTC)
HANDLE_HASH = "a" * 64
QUALIFICATION_TABLES = (
    "qualification_route_revisions",
    "qualification_previews",
    "qualification_runs",
    "qualification_cases",
    "qualification_check_results",
    "qualification_cost_evidence",
    "qualification_artifacts",
    "qualification_decisions",
)


def _suite() -> QualificationSuite:
    return QualificationSuite(
        suite_id="chat-shadow-v1",
        capability=QualificationCapability.CHAT,
        version="2026.09.05",
        fixture_ids=("chat-identity-001", "chat-style-001"),
        blocking_check_codes=("creator_identity", "isolation"),
        advisory_checks=(("usefulness", Decimal("1.0")),),
        minimum_advisory_score=Decimal("0.70"),
        max_timeout_rate=Decimal("0.10"),
        max_error_rate=Decimal("0.10"),
        required_evidence_fields=("latency_ms", "response_hash"),
    )


def _revision(**overrides: object) -> QualificationRouteRevision:
    values: dict[str, object] = {
        "capability": QualificationCapability.CHAT,
        "provider_protocol": "openai_compatible",
        "sanitized_base_host": "api.siliconflow.cn",
        "model_identifier": "deepseek-ai/DeepSeek-V3.2",
        "generation_settings": (("temperature", "0.2"),),
        "suite_version": "2026.09.05",
        "price_catalog_revision": "siliconflow-2026-09-05",
        "protection_policy_revision": "chat-protection-v1",
    }
    values.update(overrides)
    return QualificationRouteRevision(**values)  # type: ignore[arg-type]


def _preview(**overrides: object) -> QualificationPreview:
    values: dict[str, object] = {
        "preview_id": "preview-1",
        "confirmation_handle_hash": HANDLE_HASH,
        "actor_id": "admin-session-1",
        "process_instance_id": "proc-1",
        "capability": QualificationCapability.CHAT,
        "route_revision": _revision(),
        "suite": _suite(),
        "policy_revision": "chat-protection-v1",
        "fixture_ids": ("chat-identity-001", "chat-style-001"),
        "ceilings": QualificationCeilings(
            max_requests=2,
            max_input_tokens=1000,
            max_output_tokens=500,
            max_images=None,
            conservative_max_cost=Decimal("0.40"),
        ),
        "execution_mode": QualificationExecutionMode.FAKE,
        "expires_at": NOW + timedelta(minutes=10),
        "state": QualificationPreviewState.READY,
    }
    values.update(overrides)
    return QualificationPreview(**values)  # type: ignore[arg-type]


def _run(**overrides: object) -> QualificationRun:
    values: dict[str, object] = {
        "run_id": "run-1",
        "preview_id": "preview-1",
        "actor_id": "admin-session-1",
        "process_instance_id": "proc-1",
        "capability": QualificationCapability.CHAT,
        "route_revision": _revision(),
        "suite": _suite(),
        "execution_mode": QualificationExecutionMode.FAKE,
        "state": QualificationRunState.PREPARED,
        "idempotency_key": "admin-session-1:chat:2026.09.05",
        "created_at": NOW,
    }
    values.update(overrides)
    return QualificationRun(**values)  # type: ignore[arg-type]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _prepared_repository(tmp_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "qualify.sqlite3")
    await repository.initialize()
    await repository.save_qualification_route_revision(_revision(), now=NOW)
    await repository.create_qualification_preview(_preview(), now=NOW)
    return repository


@pytest.mark.asyncio
async def test_v32_database_gains_empty_qualification_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "qualify-v32.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?, 'v32')",
            ((version,) for version in range(1, 33)),
        )

    repository = SQLiteRepository(database_path)
    await repository.initialize()

    with sqlite3.connect(database_path) as connection:
        version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in QUALIFICATION_TABLES
        }
        ready_previews = connection.execute(
            "SELECT COUNT(*) FROM qualification_previews WHERE state = 'ready'"
        ).fetchone()[0]
        passed_decisions = connection.execute(
            "SELECT COUNT(*) FROM qualification_decisions WHERE status = 'passed'"
        ).fetchone()[0]
        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()

    assert LATEST_SCHEMA_VERSION == 34
    assert version == 34
    assert set(QUALIFICATION_TABLES) <= tables
    assert counts == dict.fromkeys(QUALIFICATION_TABLES, 0)
    assert ready_previews == 0
    assert passed_decisions == 0
    assert foreign_key_violations == []
    assert (
        await repository.qualification_decision(QualificationCapability.CHAT)
    ).status is QualificationDecisionStatus.UNQUALIFIED


@pytest.mark.asyncio
async def test_route_revision_snapshots_are_immutable_and_idempotent(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "qualify-revision.sqlite3")
    await repository.initialize()
    first = await repository.save_qualification_route_revision(_revision(), now=NOW)
    again = await repository.save_qualification_route_revision(
        _revision(), now=NOW + timedelta(minutes=1)
    )
    changed = await repository.save_qualification_route_revision(
        _revision(model_identifier="other-model"), now=NOW
    )

    assert first.fingerprint == again.fingerprint
    assert changed.fingerprint != first.fingerprint
    with sqlite3.connect(repository.path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM qualification_route_revisions").fetchone()[0]
            == 2
        )


@pytest.mark.asyncio
async def test_preview_confirmation_is_actor_process_and_revision_bound(tmp_path: Path) -> None:
    repository = await _prepared_repository(tmp_path)
    revision = _revision()
    assert (
        await repository.consume_qualification_preview(
            confirmation_handle_hash=HANDLE_HASH,
            actor_id="other-admin",
            process_instance_id="proc-1",
            route_fingerprint=revision.fingerprint,
            suite_id="chat-shadow-v1",
            suite_version="2026.09.05",
            policy_revision="chat-protection-v1",
            now=NOW,
        )
        is None
    )
    assert (
        await repository.consume_qualification_preview(
            confirmation_handle_hash=HANDLE_HASH,
            actor_id="admin-session-1",
            process_instance_id="other-proc",
            route_fingerprint=revision.fingerprint,
            suite_id="chat-shadow-v1",
            suite_version="2026.09.05",
            policy_revision="chat-protection-v1",
            now=NOW,
        )
        is None
    )
    assert (
        await repository.consume_qualification_preview(
            confirmation_handle_hash=HANDLE_HASH,
            actor_id="admin-session-1",
            process_instance_id="proc-1",
            route_fingerprint=revision.fingerprint,
            suite_id="chat-shadow-v1",
            suite_version="2026.09.05",
            policy_revision="other-policy",
            now=NOW,
        )
        is None
    )
    consumed = await repository.consume_qualification_preview(
        confirmation_handle_hash=HANDLE_HASH,
        actor_id="admin-session-1",
        process_instance_id="proc-1",
        route_fingerprint=revision.fingerprint,
        suite_id="chat-shadow-v1",
        suite_version="2026.09.05",
        policy_revision="chat-protection-v1",
        now=NOW,
    )
    reused = await repository.consume_qualification_preview(
        confirmation_handle_hash=HANDLE_HASH,
        actor_id="admin-session-1",
        process_instance_id="proc-1",
        route_fingerprint=revision.fingerprint,
        suite_id="chat-shadow-v1",
        suite_version="2026.09.05",
        policy_revision="chat-protection-v1",
        now=NOW,
    )

    assert consumed is not None
    assert consumed.state is QualificationPreviewState.CONSUMED
    assert reused is None


@pytest.mark.asyncio
async def test_expired_preview_cannot_be_confirmed(tmp_path: Path) -> None:
    repository = await _prepared_repository(tmp_path)
    revision = _revision()
    assert (
        await repository.consume_qualification_preview(
            confirmation_handle_hash=HANDLE_HASH,
            actor_id="admin-session-1",
            process_instance_id="proc-1",
            route_fingerprint=revision.fingerprint,
            suite_id="chat-shadow-v1",
            suite_version="2026.09.05",
            policy_revision="chat-protection-v1",
            now=NOW + timedelta(hours=1),
        )
        is None
    )
    stored = await repository.qualification_preview("preview-1")
    assert stored is not None
    assert stored.state is QualificationPreviewState.EXPIRED


@pytest.mark.asyncio
async def test_run_creation_is_idempotent_and_seeds_pending_cases(tmp_path: Path) -> None:
    repository = await _prepared_repository(tmp_path)
    revision = _revision()
    consumed = await repository.consume_qualification_preview(
        confirmation_handle_hash=HANDLE_HASH,
        actor_id="admin-session-1",
        process_instance_id="proc-1",
        route_fingerprint=revision.fingerprint,
        suite_id="chat-shadow-v1",
        suite_version="2026.09.05",
        policy_revision="chat-protection-v1",
        now=NOW,
    )
    assert consumed is not None
    created = await repository.create_qualification_run(_run(), now=NOW)
    again = await repository.create_qualification_run(_run(), now=NOW)
    cases = await repository.qualification_cases("run-1")

    assert created.run_id == again.run_id
    assert created.state is QualificationRunState.PREPARED
    assert {item.fixture_id for item in cases} == {"chat-identity-001", "chat-style-001"}
    assert all(item.state is QualificationCaseState.PENDING for item in cases)


@pytest.mark.asyncio
async def test_invalid_run_transition_is_rejected(tmp_path: Path) -> None:
    repository = await _prepared_repository(tmp_path)
    revision = _revision()
    await repository.consume_qualification_preview(
        confirmation_handle_hash=HANDLE_HASH,
        actor_id="admin-session-1",
        process_instance_id="proc-1",
        route_fingerprint=revision.fingerprint,
        suite_id="chat-shadow-v1",
        suite_version="2026.09.05",
        policy_revision="chat-protection-v1",
        now=NOW,
    )
    await repository.create_qualification_run(_run(), now=NOW)
    assert (
        await repository.transition_qualification_run(
            run_id="run-1",
            from_state=QualificationRunState.PREPARED,
            to_state=QualificationRunState.PASSED,
            now=NOW,
        )
        is None
    )
    running = await repository.transition_qualification_run(
        run_id="run-1",
        from_state=QualificationRunState.PREPARED,
        to_state=QualificationRunState.RUNNING,
        now=NOW,
    )
    assert running is not None
    assert running.state is QualificationRunState.RUNNING


@pytest.mark.asyncio
async def test_expired_case_lease_can_be_reclaimed_but_old_token_cannot_finish(
    tmp_path: Path,
) -> None:
    repository = await _prepared_repository(tmp_path)
    revision = _revision()
    await repository.consume_qualification_preview(
        confirmation_handle_hash=HANDLE_HASH,
        actor_id="admin-session-1",
        process_instance_id="proc-1",
        route_fingerprint=revision.fingerprint,
        suite_id="chat-shadow-v1",
        suite_version="2026.09.05",
        policy_revision="chat-protection-v1",
        now=NOW,
    )
    await repository.create_qualification_run(_run(), now=NOW)
    await repository.transition_qualification_run(
        run_id="run-1",
        from_state=QualificationRunState.PREPARED,
        to_state=QualificationRunState.RUNNING,
        now=NOW,
    )
    first = await repository.claim_qualification_case(
        run_id="run-1",
        fixture_id="chat-identity-001",
        lease_token="lease-old",
        ttl_seconds=30,
        now=NOW,
    )
    assert first is not None
    reclaimed = await repository.claim_qualification_case(
        run_id="run-1",
        fixture_id="chat-identity-001",
        lease_token="lease-new",
        ttl_seconds=30,
        now=NOW + timedelta(minutes=1),
    )
    old_finish = await repository.transition_qualification_case(
        case_id=first.case_id,
        from_state=QualificationCaseState.RUNNING,
        to_state=QualificationCaseState.PASSED,
        lease_token="lease-old",
        now=NOW + timedelta(minutes=1),
    )

    assert reclaimed is not None
    assert reclaimed.lease_token == "lease-new"
    assert old_finish is None


@pytest.mark.asyncio
async def test_ambiguous_expired_lease_is_inconclusive_and_not_retried(tmp_path: Path) -> None:
    repository = await _prepared_repository(tmp_path)
    revision = _revision()
    await repository.consume_qualification_preview(
        confirmation_handle_hash=HANDLE_HASH,
        actor_id="admin-session-1",
        process_instance_id="proc-1",
        route_fingerprint=revision.fingerprint,
        suite_id="chat-shadow-v1",
        suite_version="2026.09.05",
        policy_revision="chat-protection-v1",
        now=NOW,
    )
    await repository.create_qualification_run(_run(), now=NOW)
    await repository.transition_qualification_run(
        run_id="run-1",
        from_state=QualificationRunState.PREPARED,
        to_state=QualificationRunState.RUNNING,
        now=NOW,
    )
    claimed = await repository.claim_qualification_case(
        run_id="run-1",
        fixture_id="chat-identity-001",
        lease_token="lease-live",
        ttl_seconds=30,
        now=NOW,
    )
    assert claimed is not None
    await repository.mark_qualification_case_provider_attempt(
        case_id=claimed.case_id,
        lease_token="lease-live",
        provider_request_id="sf-req-1",
        now=NOW,
    )
    reconciled = await repository.reconcile_qualification_leases(now=NOW + timedelta(minutes=2))
    retry = await repository.claim_qualification_case(
        run_id="run-1",
        fixture_id="chat-identity-001",
        lease_token="lease-retry",
        ttl_seconds=30,
        now=NOW + timedelta(minutes=2),
    )
    cases = {item.fixture_id: item for item in await repository.qualification_cases("run-1")}

    assert claimed.case_id in reconciled
    assert retry is None
    assert cases["chat-identity-001"].state is QualificationCaseState.INCONCLUSIVE


@pytest.mark.asyncio
async def test_cancellation_blocks_future_claims_without_erasing_evidence(tmp_path: Path) -> None:
    repository = await _prepared_repository(tmp_path)
    revision = _revision()
    await repository.consume_qualification_preview(
        confirmation_handle_hash=HANDLE_HASH,
        actor_id="admin-session-1",
        process_instance_id="proc-1",
        route_fingerprint=revision.fingerprint,
        suite_id="chat-shadow-v1",
        suite_version="2026.09.05",
        policy_revision="chat-protection-v1",
        now=NOW,
    )
    await repository.create_qualification_run(_run(), now=NOW)
    await repository.transition_qualification_run(
        run_id="run-1",
        from_state=QualificationRunState.PREPARED,
        to_state=QualificationRunState.RUNNING,
        now=NOW,
    )
    claimed = await repository.claim_qualification_case(
        run_id="run-1",
        fixture_id="chat-identity-001",
        lease_token="lease-1",
        ttl_seconds=60,
        now=NOW,
    )
    assert claimed is not None
    cancelled = await repository.cancel_qualification_run("run-1", now=NOW)
    later = await repository.claim_qualification_case(
        run_id="run-1",
        fixture_id="chat-style-001",
        lease_token="lease-2",
        ttl_seconds=60,
        now=NOW,
    )
    cases = await repository.qualification_cases("run-1")

    assert cancelled is not None
    assert cancelled.state is QualificationRunState.CANCELLED
    assert later is None
    assert any(item.fixture_id == "chat-identity-001" for item in cases)


@pytest.mark.asyncio
async def test_stale_route_revision_does_not_keep_a_passing_decision(tmp_path: Path) -> None:
    repository = await _prepared_repository(tmp_path)
    revision = _revision()
    await repository.consume_qualification_preview(
        confirmation_handle_hash=HANDLE_HASH,
        actor_id="admin-session-1",
        process_instance_id="proc-1",
        route_fingerprint=revision.fingerprint,
        suite_id="chat-shadow-v1",
        suite_version="2026.09.05",
        policy_revision="chat-protection-v1",
        now=NOW,
    )
    await repository.create_qualification_run(_run(), now=NOW)
    await repository.record_qualification_decision(
        capability=QualificationCapability.CHAT,
        route_revision=revision,
        suite_version="2026.09.05",
        status=QualificationDecisionStatus.PASSED,
        run_id="run-1",
        evaluated_at=NOW,
        advisory_score=Decimal("0.90"),
    )
    current = await repository.qualification_decision(QualificationCapability.CHAT)
    stale = await repository.refresh_stale_qualification_decisions(
        current_revision=_revision(model_identifier="other-model"),
        current_suite_version="2026.09.05",
        now=NOW,
    )

    assert current is not None
    assert current.status is QualificationDecisionStatus.PASSED
    assert stale.status is QualificationDecisionStatus.STALE
    assert stale.qualifies is False
    assert (await repository.qualification_decision(QualificationCapability.CHAT)).status is (
        QualificationDecisionStatus.STALE
    )
