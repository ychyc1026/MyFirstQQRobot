import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from ych_bot.domain.control import ControlSource, OwnerCommand, OwnerCommandKind
from ych_bot.domain.models import ConversationKind, MessageSegment, OutboundMessage
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.parser import parse_message_event


def event() -> dict:
    return {
        "time": 1_700_000_000,
        "self_id": 2000000002,
        "post_type": "message",
        "message_type": "private",
        "message_id": 202,
        "user_id": 123456789,
        "message": [{"type": "text", "data": {"text": "hello"}}],
    }


@pytest.mark.asyncio
async def test_inbound_message_is_atomic_and_deduplicated(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "test.sqlite3")
    await repository.initialize()
    message = parse_message_event(event(), expected_bot_qq="2000000002")

    assert await repository.store_inbound(message) is True
    assert await repository.store_inbound(message) is False
    assert await repository.counts() == {
        "users": 1,
        "conversations": 1,
        "messages": 1,
        "outbox": 0,
        "control_commands": 0,
        "qzone_posts": 0,
        "qzone_schedule_policy": 0,
        "qzone_post_runtime": 0,
        "qzone_post_events": 0,
        "approval_requests": 0,
        "owner_reports": 0,
        "friend_baselines": 0,
        "user_relationships": 0,
        "identity_evidence": 0,
        "history_access_policies": 0,
        "privacy_scope_policies": 0,
        "privacy_requests": 0,
        "data_access_log": 0,
        "approval_payloads": 0,
        "friend_baseline_candidates": 0,
        "admin_sessions": 0,
        "privacy_job_artifacts": 0,
        "privacy_delete_previews": 0,
        "knowledge_documents": 0,
        "user_understanding_profiles": 0,
        "persona_profiles": 0,
        "persona_profile_evidence": 0,
        "memory_records": 0,
        "memory_evidence": 0,
        "memory_conflicts": 0,
        "document_blobs": 0,
        "document_chunks": 0,
        "knowledge_processing_jobs": 0,
        "knowledge_job_checkpoints": 0,
        "knowledge_chunk_analyses": 0,
        "inference_runs": 0,
        "reply_candidates": 0,
        "user_reply_style_policies": 0,
        "model_call_events": 0,
        "image_generation_tasks": 0,
        "image_artifacts": 0,
        "image_artifact_reviews": 0,
        "image_orphan_scans": 0,
        "proactive_user_policies": 0,
        "proactive_message_tasks": 0,
        "proactive_delivery_events": 0,
        "owner_report_delivery_policy": 0,
        "owner_report_runtime": 0,
        "owner_report_events": 0,
        "qzone_profile_access_policies": 0,
        "qzone_profile_snapshots": 0,
        "worker_runtime_state": 0,
        "instance_owner": 0,
        "bots": 0,
        "chat_quota_overrides": 0,
        "chat_quota_notices": 0,
        "daily_summaries": 0,
        "diary_entries": 0,
        "proactive_materials": 0,
        "privacy_subject_links": 0,
        "reply_runs": 0,
        "reply_run_triggers": 0,
        "reply_context_manifests": 0,
        "reply_run_leases": 0,
        "reply_delivery_evidence": 0,
    }


@pytest.mark.asyncio
async def test_outbox_uses_idempotency_key(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "test.sqlite3")
    await repository.initialize()
    outbound = OutboundMessage(
        id=str(uuid4()),
        idempotency_key="reply:202",
        conversation_kind=ConversationKind.PRIVATE,
        target_id="123456789",
        segments=(MessageSegment(type="text", data={"text": "world"}),),
    )

    assert await repository.enqueue_outbound(outbound) is True
    assert await repository.enqueue_outbound(outbound) is False
    claimed = await repository.claim_outbound()
    assert claimed is not None
    assert claimed.target_id == "123456789"
    assert claimed.segments[0].data["text"] == "world"


@pytest.mark.asyncio
async def test_outbound_sent_is_mirrored_into_messages(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "outbound-mirror.sqlite3")
    await repository.initialize()
    outbound = OutboundMessage(
        id=str(uuid4()),
        idempotency_key="mirror:1",
        conversation_kind=ConversationKind.PRIVATE,
        target_id="10001",
        segments=(MessageSegment(type="text", data={"text": "你好"}),),
    )
    assert await repository.enqueue_outbound(outbound) is True
    claimed = await repository.claim_outbound()
    assert claimed is not None
    await repository.mark_outbound_sent(claimed.id, bot_qq="2000000002")
    await repository.mark_outbound_sent(claimed.id, bot_qq="2000000002")
    counts = await repository.counts()
    assert counts["messages"] == 1
    start = "2020-01-01T00:00:00+00:00"
    end = "2100-01-01T00:00:00+00:00"
    stats = await repository.stats_message_counts(start_iso=start, end_iso=end)
    assert stats["bot_messages"] == 1
    log = await repository.chatlog_messages(
        kind="private",
        peer_id="10001",
        start_iso=start,
        end_iso=end,
    )
    assert log[0]["direction"] == "outbound"
    assert log[0]["plain_text"] == "你好"


@pytest.mark.asyncio
async def test_outbox_restart_recovery_still_respects_attempt_limit(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "outbox-recovery.sqlite3")
    await repository.initialize()
    outbound = OutboundMessage(
        id=str(uuid4()),
        idempotency_key="recovery:202",
        conversation_kind=ConversationKind.PRIVATE,
        target_id="123456789",
        segments=(MessageSegment(type="text", data={"text": "recover"}),),
    )
    assert await repository.enqueue_outbound(outbound) is True

    assert await repository.claim_outbound(max_attempts=2) is not None
    assert await repository.recover_sending_outbound() == 1
    assert await repository.claim_outbound(max_attempts=2) is not None
    assert await repository.recover_sending_outbound() == 1

    assert await repository.claim_outbound(max_attempts=2) is None


@pytest.mark.asyncio
async def test_schema_version_four_upgrades_to_thirty_three_idempotently(tmp_path: Path) -> None:
    database_path = tmp_path / "upgrade.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?, 'old')",
            ((version,) for version in range(1, 5)),
        )

    repository = SQLiteRepository(database_path)
    await repository.initialize()
    await repository.initialize()

    with sqlite3.connect(database_path) as connection:
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()

    assert versions == list(range(1, 35))
    assert {
        "persona_profiles",
        "memory_records",
        "inference_runs",
        "reply_candidates",
        "user_reply_style_policies",
        "model_call_events",
        "image_generation_tasks",
        "image_artifacts",
        "image_artifact_reviews",
        "image_orphan_scans",
        "knowledge_job_checkpoints",
        "knowledge_chunk_analyses",
        "persona_profile_evidence",
        "proactive_user_policies",
        "proactive_message_tasks",
        "proactive_delivery_events",
        "qzone_schedule_policy",
        "qzone_post_runtime",
        "qzone_post_events",
        "owner_report_delivery_policy",
        "owner_report_runtime",
        "owner_report_events",
        "qzone_profile_access_policies",
        "qzone_profile_snapshots",
        "operator_labels",
        "worker_runtime_state",
        "instance_owner",
        "bots",
        "chat_quota_overrides",
        "chat_quota_notices",
        "daily_summaries",
        "diary_entries",
        "proactive_materials",
        "privacy_subject_links",
        "reply_runs",
        "reply_run_triggers",
        "reply_context_manifests",
        "reply_run_leases",
        "reply_delivery_evidence",
        "readiness_decisions",
        "readiness_probe_evidence",
        "managed_artifacts",
        "managed_artifact_references",
        "retention_previews",
        "quarantine_batches",
        "quarantine_batch_items",
        "qualification_route_revisions",
        "qualification_previews",
        "qualification_runs",
        "qualification_cases",
        "qualification_check_results",
        "qualification_cost_evidence",
        "qualification_artifacts",
        "qualification_decisions",
    } <= tables
    assert foreign_key_violations == []


@pytest.mark.asyncio
async def test_interrupted_schema_upgrade_preserves_legacy_data_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ych_bot.infrastructure.database.sqlite as sqlite_module

    database_path = tmp_path / "interrupted-upgrade.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?, 'old')",
            ((version,) for version in range(1, 5)),
        )
        connection.execute("CREATE TABLE legacy_data(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_data(value) VALUES('must survive')")
    original_schema = sqlite_module.SCHEMA
    monkeypatch.setattr(sqlite_module, "SCHEMA", original_schema + "\nINVALID MIGRATION SQL;")
    repository = SQLiteRepository(database_path)

    with pytest.raises(sqlite3.OperationalError):
        await repository.initialize()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT value FROM legacy_data").fetchone()[0] == "must survive"
        versions_after_failure = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        reply_runs_after_failure = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reply_runs'"
        ).fetchone()
    assert versions_after_failure == [(1,), (2,), (3,), (4,)]
    assert reply_runs_after_failure is None

    monkeypatch.setattr(sqlite_module, "SCHEMA", original_schema)
    await repository.initialize()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT value FROM legacy_data").fetchone()[0] == "must survive"
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 34
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.asyncio
async def test_v30_managed_artifact_owner_survives_user_row_deletion(tmp_path: Path) -> None:
    database_path = tmp_path / "managed-artifact-v30.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'managed_artifacts'"
        ).fetchone()
        assert row is not None
        legacy_sql = str(row["sql"]).replace(
            "owner_qq TEXT",
            "owner_qq TEXT REFERENCES users(qq_id) ON DELETE RESTRICT",
            1,
        )
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA legacy_alter_table = ON")
        connection.executescript(
            f"""
            DROP INDEX idx_managed_artifacts_scope;
            DROP INDEX idx_managed_artifacts_system_path;
            DROP INDEX idx_managed_artifacts_user_path;
            ALTER TABLE managed_artifacts RENAME TO managed_artifacts_v31;
            {legacy_sql};
            DROP TABLE managed_artifacts_v31;
            """
        )
        connection.execute(
            "INSERT INTO users(qq_id, first_seen_at, last_seen_at) VALUES(?, ?, ?)",
            ("123456789", now, now),
        )
        connection.execute(
            """
            INSERT INTO managed_artifacts(
                id, artifact_type, owner_scope, owner_qq, relative_path, bundle_key,
                size_bytes, digest_sha256, manifest_type, created_at,
                verification_state, verification_revision, reference_state,
                reference_revision, retention_state, revision, updated_at
            ) VALUES(
                'privacy-backup', 'privacy_deletion_backup', 'user', '123456789',
                'storage/privacy-backups/request.json', 'request', 10, 'digest',
                'ych-privacy-bundle-v1', ?, 'verified', 1, 'unreferenced', 1,
                'retain', 1, ?
            )
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO managed_artifact_references(
                id, artifact_id, reference_type, reference_key, state, revision, observed_at
            ) VALUES(
                'privacy-reference', 'privacy-backup', 'privacy_job', 'request',
                'protected', 1, ?
            )
            """,
            (now,),
        )

    await repository.initialize()

    with sqlite3.connect(database_path) as connection:
        owner_foreign_keys = [
            row
            for row in connection.execute("PRAGMA foreign_key_list(managed_artifacts)")
            if row[2] == "users"
        ]
        assert owner_foreign_keys == []
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("DELETE FROM users WHERE qq_id = '123456789'")
        assert (
            connection.execute(
                "SELECT owner_qq FROM managed_artifacts WHERE id = 'privacy-backup'"
            ).fetchone()[0]
            == "123456789"
        )
        assert (
            connection.execute(
                "SELECT artifact_id FROM managed_artifact_references WHERE id = 'privacy-reference'"
            ).fetchone()[0]
            == "privacy-backup"
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.asyncio
async def test_v31_quarantine_owner_survives_user_row_deletion(tmp_path: Path) -> None:
    database_path = tmp_path / "quarantine-v31.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO users(qq_id, first_seen_at, last_seen_at) VALUES(?, ?, ?)",
            ("123456789", now, now),
        )
        connection.execute(
            """
            INSERT INTO managed_artifacts(
                id, artifact_type, owner_scope, owner_qq, relative_path, bundle_key,
                size_bytes, digest_sha256, manifest_type, created_at,
                verification_state, verification_revision, reference_state,
                reference_revision, retention_state, revision, updated_at
            ) VALUES(
                'privacy-export', 'privacy_export', 'user', '123456789',
                'storage/exports/request.json', 'request', 10, 'digest',
                'ych-privacy-bundle-v1', ?, 'verified', 1, 'unreferenced', 1,
                'candidate', 1, ?
            )
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO retention_previews(
                id, token_hash, actor_id, actor_source, process_instance_id,
                policy_revision, candidate_ids_json, evidence_revisions_json,
                total_bytes, target_batch_type, state, revision, correlation_id,
                created_at, expires_at, consumed_at
            ) VALUES(
                'preview-v31', 'token-hash', 'actor', 'dashboard', 'process', 1,
                '["privacy-export"]', '[["privacy-export","evidence"]]', 10,
                'privacy_exports', 'consumed', 2, 'correlation', ?, ?, ?
            )
            """,
            (now, now, now),
        )
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'quarantine_batches'"
        ).fetchone()
        assert row is not None
        legacy_sql = str(row["sql"]).replace(
            "owner_qq TEXT",
            "owner_qq TEXT REFERENCES users(qq_id) ON DELETE RESTRICT",
            1,
        )
        connection.execute("PRAGMA legacy_alter_table = ON")
        connection.executescript(
            f"""
            DROP INDEX idx_quarantine_batches_scope;
            ALTER TABLE quarantine_batches RENAME TO quarantine_batches_v32;
            {legacy_sql};
            DROP TABLE quarantine_batches_v32;
            """
        )
        connection.execute(
            """
            INSERT INTO quarantine_batches(
                id, preview_id, batch_type, owner_scope, owner_qq, actor_id,
                process_instance_id, state, revision, correlation_id, created_at, updated_at
            ) VALUES(
                'batch-v31', 'preview-v31', 'privacy_exports', 'user', '123456789',
                'actor', 'process', 'moving', 2, 'correlation', ?, ?
            )
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO quarantine_batch_items(
                batch_id, artifact_id, sequence, source_relative_path,
                quarantine_relative_path, expected_digest_sha256, state, updated_at
            ) VALUES(
                'batch-v31', 'privacy-export', 1, 'storage/exports/request.json',
                'storage/trash/batch/request.json', 'digest', 'pending', ?
            )
            """,
            (now,),
        )

    await repository.initialize()

    with sqlite3.connect(database_path) as connection:
        assert [
            row
            for row in connection.execute("PRAGMA foreign_key_list(quarantine_batches)")
            if row[2] == "users"
        ] == []
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("DELETE FROM users WHERE qq_id = '123456789'")
        assert (
            connection.execute(
                "SELECT owner_qq FROM quarantine_batches WHERE id = 'batch-v31'"
            ).fetchone()[0]
            == "123456789"
        )
        assert (
            connection.execute(
                "SELECT artifact_id FROM quarantine_batch_items WHERE batch_id = 'batch-v31'"
            ).fetchone()[0]
            == "privacy-export"
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.asyncio
async def test_reply_pipeline_schema_enforces_active_run_and_cascades(tmp_path: Path) -> None:
    database_path = tmp_path / "reply-schema.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.initialize()
    message = parse_message_event(event(), expected_bot_qq="2000000002")
    assert await repository.store_inbound(message) is True
    outbound = OutboundMessage(
        id="outbox-reply-1",
        idempotency_key="run-1:1",
        conversation_kind=ConversationKind.PRIVATE,
        target_id="123456789",
        segments=(MessageSegment(type="text", data={"text": "reply"}),),
    )
    assert await repository.enqueue_outbound(outbound) is True
    now = "2026-08-28T00:00:00+00:00"

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO reply_runs(
                id, bot_qq, conversation_key, conversation_kind, subject_user_qq,
                stage, created_at, updated_at
            ) VALUES('run-1', '2000000002', ?, 'private', '123456789',
                     'pending', ?, ?)
            """,
            (message.conversation_key, now, now),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO reply_runs(
                    id, bot_qq, conversation_key, conversation_kind, subject_user_qq,
                    stage, created_at, updated_at
                ) VALUES('run-active-duplicate', '2000000002', ?, 'private',
                         '123456789', 'settling', ?, ?)
                """,
                (message.conversation_key, now, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO reply_runs(
                    id, bot_qq, conversation_key, conversation_kind, subject_user_qq,
                    stage, created_at, updated_at
                ) VALUES('run-group-leak', '2000000002', ?, 'group', '123456789',
                         'completed', ?, ?)
                """,
                (message.conversation_key, now, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO reply_runs(
                    id, bot_qq, conversation_key, conversation_kind, subject_user_qq,
                    stage, created_at, updated_at
                ) VALUES('run-invalid', '2000000002', ?, 'private', '123456789',
                         'unknown', ?, ?)
                """,
                (message.conversation_key, now, now),
            )
        connection.execute(
            """
            INSERT INTO reply_runs(
                id, bot_qq, conversation_key, conversation_kind, subject_user_qq,
                stage, created_at, updated_at, completed_at
            ) VALUES('run-terminal', '2000000002', ?, 'private', '123456789',
                     'completed', ?, ?, ?)
            """,
            (message.conversation_key, now, now, now),
        )
        connection.execute(
            "INSERT INTO reply_run_triggers VALUES('run-1', ?, 1, ?)",
            (message.id, now),
        )
        connection.execute(
            """
            INSERT INTO reply_context_manifests(
                id, run_id, revision, conversation_key, manifest_json, created_at
            ) VALUES('manifest-1', 'run-1', 1, ?, '{}', ?)
            """,
            (message.conversation_key, now),
        )
        connection.execute(
            """
            INSERT INTO reply_run_leases(
                run_id, lease_owner, lease_token, acquired_at, heartbeat_at, expires_at
            ) VALUES('run-1', 'worker-1', 'lease-1', ?, ?, ?)
            """,
            (now, now, "2026-08-28T00:01:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO reply_delivery_evidence(
                id, run_id, outbox_id, bubble_sequence, attempt, idempotency_key,
                outcome, observed_at
            ) VALUES('evidence-1', 'run-1', 'outbox-reply-1', 1, 1,
                     'run-1:1', 'queued', ?)
            """,
            (now,),
        )
        connection.execute("DELETE FROM reply_runs WHERE id = 'run-1'")
        remaining = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "reply_run_triggers",
                "reply_context_manifests",
                "reply_run_leases",
                "reply_delivery_evidence",
            )
        }
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()

    assert remaining == {
        "reply_run_triggers": 0,
        "reply_context_manifests": 0,
        "reply_run_leases": 0,
        "reply_delivery_evidence": 0,
    }
    assert violations == []


@pytest.mark.asyncio
async def test_privacy_links_match_structured_user_fields_only(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "privacy-links.sqlite3")
    await repository.initialize()
    target_qq = "123456789"
    linked = OwnerCommand(
        id="linked-command",
        kind=OwnerCommandKind.USER_STATUS,
        actor_qq="2000000001",
        source_message_id="1",
        arguments={"user_qq": target_qq},
        source=ControlSource.DASHBOARD,
    )
    unrelated = OwnerCommand(
        id="unrelated-command",
        kind=OwnerCommandKind.STATUS,
        actor_qq="2000000001",
        source_message_id="2",
        arguments={"note": target_qq},
        source=ControlSource.DASHBOARD,
    )
    assert await repository.store_control_command(linked) is True
    assert await repository.store_control_command(unrelated) is True

    impact = await repository.privacy_delete_impact(target_qq)
    exported = await repository.privacy_export_snapshot(target_qq)

    assert impact["control_commands"] == 1
    assert [item["id"] for item in exported["linked_control_commands"]] == ["linked-command"]

    await repository.delete_user_data(target_qq, request_id="privacy-delete-test")
    commands = await repository.control_commands(limit=10)
    assert [item["id"] for item in commands] == ["unrelated-command"]


@pytest.mark.asyncio
async def test_worker_pause_state_is_isolated_per_worker(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "worker-pause.sqlite3")
    await repository.initialize()

    repository.set_worker_paused_sync("knowledge", True, updated_by="owner")
    repository.set_worker_paused_sync("outbox", False, updated_by="owner")

    assert repository.worker_paused_sync("knowledge") is True
    assert repository.worker_paused_sync("outbox") is False
    assert repository.worker_paused_sync("qzone") is False
    assert (await repository.counts())["worker_runtime_state"] == 2
