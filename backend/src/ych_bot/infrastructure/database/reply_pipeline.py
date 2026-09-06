"""Durable reply-run persistence with compare-and-set transitions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ych_bot.domain.reply_pipeline import (
    ContextManifest,
    MessageEnvelope,
    ReplyFailure,
    ReplyPlan,
    ReplyRunStage,
    can_transition_reply_stage,
)
from ych_bot.domain.reply_planning import restore_reply_plan


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("reply pipeline timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _reply_run_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["policy_snapshot"] = json.loads(result.pop("policy_snapshot_json"))
    raw_plan = result.pop("reply_plan_json")
    result["reply_plan"] = json.loads(raw_plan) if raw_plan else None
    return result


class ReplyPipelineRepositoryMixin:
    def _connect(self) -> sqlite3.Connection:  # pragma: no cover - supplied by repository
        raise NotImplementedError

    async def list_reply_runs(
        self,
        *,
        stage: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._list_reply_runs_sync, stage, limit, offset)

    def _list_reply_runs_sync(
        self,
        stage: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        where = "WHERE reply_runs.stage = ?" if stage else ""
        parameters: list[Any] = [stage] if stage else []
        with self._connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM reply_runs {where}", parameters
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT reply_runs.*, conversations.peer_id,
                       COUNT(DISTINCT reply_run_triggers.message_id) AS trigger_count,
                       COUNT(DISTINCT reply_context_manifests.id) AS manifest_count,
                       (
                         SELECT evidence.outcome
                         FROM reply_delivery_evidence AS evidence
                         WHERE evidence.run_id = reply_runs.id
                         ORDER BY evidence.observed_at DESC, evidence.rowid DESC
                         LIMIT 1
                       ) AS latest_delivery_outcome
                FROM reply_runs
                JOIN conversations
                  ON conversations.conversation_key = reply_runs.conversation_key
                LEFT JOIN reply_run_triggers
                  ON reply_run_triggers.run_id = reply_runs.id
                LEFT JOIN reply_context_manifests
                  ON reply_context_manifests.run_id = reply_runs.id
                {where}
                GROUP BY reply_runs.id
                ORDER BY reply_runs.created_at DESC, reply_runs.id DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
        return {
            "items": [_reply_run_row(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def reply_pipeline_summary(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._reply_pipeline_summary_sync)

    def _reply_pipeline_summary_sync(self) -> dict[str, Any]:
        now_iso = _iso(_utc_now())
        with self._connect() as connection:
            stage_rows = connection.execute(
                "SELECT stage, COUNT(*) AS count FROM reply_runs GROUP BY stage"
            ).fetchall()
            outcome_rows = connection.execute(
                """
                SELECT outcome, COUNT(*) AS count
                FROM reply_delivery_evidence GROUP BY outcome
                """
            ).fetchall()
            lease_row = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN expires_at <= ? THEN 1 ELSE 0 END) AS expired
                FROM reply_run_leases
                """,
                (now_iso,),
            ).fetchone()
        stages = {str(row["stage"]): int(row["count"]) for row in stage_rows}
        return {
            "total_runs": sum(stages.values()),
            "stages": stages,
            "delivery_outcomes": {str(row["outcome"]): int(row["count"]) for row in outcome_rows},
            "active_runs": sum(
                count
                for stage, count in stages.items()
                if stage
                not in {"shadow_completed", "completed", "suppressed", "failed", "cancelled"}
            ),
            "leases": {
                "total": int(lease_row["total"] or 0),
                "expired": int(lease_row["expired"] or 0),
            },
        }

    async def create_or_append_reply_run(
        self,
        *,
        run_id: str,
        envelope: MessageEnvelope,
        settled_until: datetime,
        policy_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._create_or_append_reply_run_sync,
            run_id,
            envelope,
            settled_until,
            policy_snapshot,
        )

    def _create_or_append_reply_run_sync(
        self,
        run_id: str,
        envelope: MessageEnvelope,
        settled_until: datetime,
        policy_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        now = _iso(_utc_now())
        subject_user_qq = (
            envelope.peer_id if envelope.conversation_kind.value == "private" else None
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored_message = connection.execute(
                "SELECT event_key, bot_qq, conversation_key FROM messages WHERE id = ?",
                (envelope.message_id,),
            ).fetchone()
            if (
                stored_message is None
                or str(stored_message["event_key"]) != envelope.event_key
                or str(stored_message["bot_qq"]) != envelope.bot_qq
                or str(stored_message["conversation_key"]) != envelope.conversation_key
            ):
                raise ValueError("message envelope does not match durable message evidence")
            active = connection.execute(
                """
                SELECT * FROM reply_runs
                WHERE conversation_key = ?
                  AND stage NOT IN (
                    'shadow_completed', 'completed', 'suppressed', 'failed', 'cancelled'
                  )
                LIMIT 1
                """,
                (envelope.conversation_key,),
            ).fetchone()
            created = active is None
            if active is None:
                connection.execute(
                    """
                    INSERT INTO reply_runs(
                        id, bot_qq, conversation_key, conversation_kind,
                        subject_user_qq, stage, policy_snapshot_json,
                        settled_until, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        envelope.bot_qq,
                        envelope.conversation_key,
                        envelope.conversation_kind.value,
                        subject_user_qq,
                        _json(policy_snapshot),
                        _iso(settled_until),
                        now,
                        now,
                    ),
                )
                active_id = run_id
            else:
                active_id = str(active["id"])
            existing_trigger = connection.execute(
                "SELECT sequence FROM reply_run_triggers WHERE run_id = ? AND message_id = ?",
                (active_id, envelope.message_id),
            ).fetchone()
            accepts_trigger = created or str(active["stage"]) in {"pending", "settling"}
            deferred = existing_trigger is None and not accepts_trigger
            trigger_added = existing_trigger is None and accepts_trigger
            if deferred:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO reply_deferred_triggers(
                        bot_qq, conversation_key, message_id, settled_until,
                        policy_snapshot_json, deferred_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        envelope.bot_qq,
                        envelope.conversation_key,
                        envelope.message_id,
                        _iso(settled_until),
                        _json(policy_snapshot),
                        now,
                    ),
                )
                deferred = cursor.rowcount == 1
            if trigger_added:
                sequence = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(sequence), 0) + 1
                        FROM reply_run_triggers WHERE run_id = ?
                        """,
                        (active_id,),
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO reply_run_triggers(run_id, message_id, sequence, added_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (active_id, envelope.message_id, sequence, now),
                )
                if not created:
                    connection.execute(
                        """
                        UPDATE reply_runs SET settled_until = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (_iso(settled_until), now, active_id),
                    )
            row = connection.execute(
                "SELECT * FROM reply_runs WHERE id = ?", (active_id,)
            ).fetchone()
            connection.commit()
        return {
            "created": created,
            "trigger_added": trigger_added,
            "deferred": deferred,
            "run": _reply_run_row(row),
        }

    async def claim_ready_reply_run(
        self,
        *,
        lease_owner: str,
        lease_token: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        if ttl_seconds < 1:
            raise ValueError("lease ttl_seconds must be positive")
        return await asyncio.to_thread(
            self._claim_ready_reply_run_sync,
            lease_owner,
            lease_token,
            ttl_seconds,
            now or _utc_now(),
        )

    def _claim_ready_reply_run_sync(
        self,
        lease_owner: str,
        lease_token: str,
        ttl_seconds: int,
        now: datetime,
    ) -> dict[str, Any] | None:
        now_iso = _iso(now)
        expires_at = _iso(now + timedelta(seconds=ttl_seconds))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE reply_runs
                SET stage = 'settling', updated_at = ?
                WHERE stage = 'pending'
                """,
                (now_iso,),
            )
            row = connection.execute(
                """
                SELECT reply_runs.*
                FROM reply_runs
                LEFT JOIN reply_run_leases
                  ON reply_run_leases.run_id = reply_runs.id
                WHERE (
                    (
                      reply_runs.stage = 'settling'
                      AND reply_runs.settled_until IS NOT NULL
                      AND reply_runs.settled_until <= ?
                    )
                    OR reply_runs.stage = 'assembling_context'
                  )
                  AND (
                    reply_run_leases.run_id IS NULL
                    OR reply_run_leases.expires_at <= ?
                  )
                ORDER BY
                  CASE WHEN reply_runs.stage = 'assembling_context' THEN 0 ELSE 1 END,
                  reply_runs.updated_at,
                  reply_runs.created_at,
                  reply_runs.id
                LIMIT 1
                """,
                (now_iso, now_iso),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            run_id = str(row["id"])
            recovered = str(row["stage"]) == "assembling_context"
            cursor = connection.execute(
                """
                UPDATE reply_runs
                SET stage = 'assembling_context',
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE id = ?
                  AND (
                    (stage = 'settling' AND settled_until <= ?)
                    OR stage = 'assembling_context'
                  )
                """,
                (now_iso, now_iso, run_id, now_iso),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            connection.execute(
                """
                INSERT INTO reply_run_leases(
                    run_id, lease_owner, lease_token, acquired_at, heartbeat_at, expires_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    lease_owner = excluded.lease_owner,
                    lease_token = excluded.lease_token,
                    acquired_at = excluded.acquired_at,
                    heartbeat_at = excluded.heartbeat_at,
                    expires_at = excluded.expires_at
                """,
                (run_id, lease_owner, lease_token, now_iso, now_iso, expires_at),
            )
            claimed = connection.execute(
                "SELECT * FROM reply_runs WHERE id = ?", (run_id,)
            ).fetchone()
            connection.commit()
        result = _reply_run_row(claimed)
        result["lease"] = {
            "lease_owner": lease_owner,
            "lease_token": lease_token,
            "acquired_at": now_iso,
            "heartbeat_at": now_iso,
            "expires_at": expires_at,
        }
        result["recovered"] = recovered
        return result

    async def transition_reply_run(
        self,
        *,
        run_id: str,
        expected_stage: ReplyRunStage,
        target_stage: ReplyRunStage,
        reply_plan: ReplyPlan | None = None,
        failure: ReplyFailure | None = None,
        lease_token: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        if not can_transition_reply_stage(expected_stage, target_stage):
            raise ValueError(f"illegal reply stage transition: {expected_stage} -> {target_stage}")
        return await asyncio.to_thread(
            self._transition_reply_run_sync,
            run_id,
            expected_stage,
            target_stage,
            reply_plan,
            failure,
            lease_token,
            now or _utc_now(),
        )

    def _transition_reply_run_sync(
        self,
        run_id: str,
        expected_stage: ReplyRunStage,
        target_stage: ReplyRunStage,
        reply_plan: ReplyPlan | None,
        failure: ReplyFailure | None,
        lease_token: str | None,
        now: datetime,
    ) -> bool:
        now_iso = _iso(now)
        plan_json = _json(asdict(reply_plan)) if reply_plan else None
        completed_at = now_iso if target_stage.terminal else None
        started_at = now_iso if target_stage is ReplyRunStage.ASSEMBLING_CONTEXT else None
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE reply_runs SET
                    stage = ?,
                    reply_plan_json = COALESCE(?, reply_plan_json),
                    failure_code = ?,
                    failure_category = ?,
                    failure_detail = ?,
                    attempt_count = attempt_count + ?,
                    started_at = COALESCE(started_at, ?),
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ? AND stage = ?
                  AND (
                    ? IS NULL
                    OR EXISTS(
                      SELECT 1 FROM reply_run_leases
                      WHERE reply_run_leases.run_id = reply_runs.id
                        AND reply_run_leases.lease_token = ?
                        AND reply_run_leases.expires_at > ?
                    )
                  )
                """,
                (
                    target_stage.value,
                    plan_json,
                    failure.code if failure else None,
                    failure.category.value if failure else None,
                    failure.safe_detail if failure else None,
                    int(target_stage is ReplyRunStage.CALLING_MODEL),
                    started_at,
                    completed_at,
                    now_iso,
                    run_id,
                    expected_stage.value,
                    lease_token,
                    lease_token,
                    now_iso,
                ),
            )
            if cursor.rowcount == 1 and target_stage.terminal:
                connection.execute("DELETE FROM reply_run_leases WHERE run_id = ?", (run_id,))
                self._promote_deferred_successor(connection, run_id=run_id, now_iso=now_iso)
        return cursor.rowcount == 1

    @staticmethod
    def _promote_deferred_successor(
        connection: sqlite3.Connection, *, run_id: str, now_iso: str
    ) -> str | None:
        run = connection.execute(
            "SELECT bot_qq, conversation_key, conversation_kind FROM reply_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        deferred = connection.execute(
            """
            SELECT * FROM reply_deferred_triggers
            WHERE conversation_key = ? ORDER BY id
            """,
            (run["conversation_key"],),
        ).fetchall()
        if not deferred:
            return None
        conversation = connection.execute(
            "SELECT peer_id FROM conversations WHERE conversation_key = ?",
            (run["conversation_key"],),
        ).fetchone()
        successor_id = str(
            uuid5(
                NAMESPACE_URL,
                f"reply-successor:{run['conversation_key']}:{deferred[0]['message_id']}",
            )
        )
        settled_until = max(str(item["settled_until"]) for item in deferred)
        connection.execute(
            """
            INSERT INTO reply_runs(
                id, bot_qq, conversation_key, conversation_kind, subject_user_qq,
                stage, policy_snapshot_json, settled_until, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, 'settling', ?, ?, ?, ?)
            """,
            (
                successor_id,
                run["bot_qq"],
                run["conversation_key"],
                run["conversation_kind"],
                conversation["peer_id"] if str(run["conversation_kind"]) == "private" else None,
                deferred[0]["policy_snapshot_json"],
                settled_until,
                now_iso,
                now_iso,
            ),
        )
        for sequence, item in enumerate(deferred, start=1):
            connection.execute(
                """
                INSERT INTO reply_run_triggers(run_id, message_id, sequence, added_at)
                VALUES(?, ?, ?, ?)
                """,
                (successor_id, item["message_id"], sequence, item["deferred_at"]),
            )
        connection.execute(
            "DELETE FROM reply_deferred_triggers WHERE conversation_key = ?",
            (run["conversation_key"],),
        )
        return successor_id

    async def acquire_reply_run_lease(
        self,
        *,
        run_id: str,
        lease_owner: str,
        lease_token: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> bool:
        if ttl_seconds < 1:
            raise ValueError("lease ttl_seconds must be positive")
        return await asyncio.to_thread(
            self._acquire_reply_run_lease_sync,
            run_id,
            lease_owner,
            lease_token,
            ttl_seconds,
            now or _utc_now(),
        )

    def _acquire_reply_run_lease_sync(
        self,
        run_id: str,
        lease_owner: str,
        lease_token: str,
        ttl_seconds: int,
        now: datetime,
    ) -> bool:
        now_iso = _iso(now)
        expires_at = _iso(now + timedelta(seconds=ttl_seconds))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT stage FROM reply_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None or ReplyRunStage(str(run["stage"])).terminal:
                connection.rollback()
                return False
            lease = connection.execute(
                "SELECT expires_at FROM reply_run_leases WHERE run_id = ?", (run_id,)
            ).fetchone()
            if lease is not None and str(lease["expires_at"]) > now_iso:
                connection.rollback()
                return False
            connection.execute(
                """
                INSERT INTO reply_run_leases(
                    run_id, lease_owner, lease_token, acquired_at, heartbeat_at, expires_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    lease_owner = excluded.lease_owner,
                    lease_token = excluded.lease_token,
                    acquired_at = excluded.acquired_at,
                    heartbeat_at = excluded.heartbeat_at,
                    expires_at = excluded.expires_at
                """,
                (run_id, lease_owner, lease_token, now_iso, now_iso, expires_at),
            )
            connection.commit()
        return True

    async def heartbeat_reply_run_lease(
        self, *, run_id: str, lease_token: str, ttl_seconds: int
    ) -> bool:
        if ttl_seconds < 1:
            raise ValueError("lease ttl_seconds must be positive")
        now = _utc_now()
        return await asyncio.to_thread(
            self._heartbeat_reply_run_lease_sync,
            run_id,
            lease_token,
            _iso(now),
            _iso(now + timedelta(seconds=ttl_seconds)),
        )

    def _heartbeat_reply_run_lease_sync(
        self, run_id: str, lease_token: str, now: str, expires_at: str
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE reply_run_leases SET heartbeat_at = ?, expires_at = ?
                WHERE run_id = ? AND lease_token = ? AND expires_at > ?
                """,
                (now, expires_at, run_id, lease_token, now),
            )
        return cursor.rowcount == 1

    async def release_reply_run_lease(self, *, run_id: str, lease_token: str) -> bool:
        return await asyncio.to_thread(self._release_reply_run_lease_sync, run_id, lease_token)

    def _release_reply_run_lease_sync(self, run_id: str, lease_token: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM reply_run_leases WHERE run_id = ? AND lease_token = ?",
                (run_id, lease_token),
            )
        return cursor.rowcount == 1

    async def store_reply_context_manifest(
        self,
        *,
        manifest_id: str,
        manifest: ContextManifest,
        revision: int,
        rendered_content: str | None,
        budget_chars: int | None,
        lease_token: str | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._store_reply_context_manifest_sync,
            manifest_id,
            manifest,
            revision,
            rendered_content,
            budget_chars,
            lease_token,
        )

    def _store_reply_context_manifest_sync(
        self,
        manifest_id: str,
        manifest: ContextManifest,
        revision: int,
        rendered_content: str | None,
        budget_chars: int | None,
        lease_token: str | None,
    ) -> bool:
        rendered_hash = (
            hashlib.sha256(rendered_content.encode("utf-8")).hexdigest()
            if rendered_content is not None
            else None
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO reply_context_manifests(
                    id, run_id, revision, conversation_key, manifest_json,
                    rendered_sha256, budget_chars, used_chars, created_at
                )
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
                FROM reply_runs
                LEFT JOIN reply_run_leases ON reply_run_leases.run_id = reply_runs.id
                WHERE reply_runs.id = ? AND conversation_key = ?
                  AND (
                    ? IS NULL
                    OR (
                      reply_run_leases.lease_token = ?
                      AND reply_run_leases.expires_at > ?
                    )
                  )
                """,
                (
                    manifest_id,
                    manifest.run_id,
                    revision,
                    manifest.conversation_key,
                    _json(asdict(manifest)),
                    rendered_hash,
                    budget_chars,
                    len(rendered_content) if rendered_content is not None else None,
                    _iso(_utc_now()),
                    manifest.run_id,
                    manifest.conversation_key,
                    lease_token,
                    lease_token,
                    _iso(_utc_now()),
                ),
            )
        return cursor.rowcount == 1

    async def handoff_reply_plan_to_outbox(
        self,
        *,
        run_id: str,
        lease_token: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._handoff_reply_plan_to_outbox_sync,
            run_id,
            lease_token,
            now or _utc_now(),
        )

    def _handoff_reply_plan_to_outbox_sync(
        self,
        run_id: str,
        lease_token: str,
        now: datetime,
    ) -> dict[str, Any]:
        now_iso = _iso(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT reply_runs.*, conversations.peer_id,
                       reply_run_leases.lease_token, reply_run_leases.expires_at
                FROM reply_runs
                JOIN conversations
                  ON conversations.conversation_key = reply_runs.conversation_key
                LEFT JOIN reply_run_leases ON reply_run_leases.run_id = reply_runs.id
                WHERE reply_runs.id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ValueError("reply run not found")
            if row["stage"] not in {
                ReplyRunStage.CREATING_OUTBOX.value,
                ReplyRunStage.AWAITING_DELIVERY.value,
            }:
                connection.rollback()
                raise ValueError("reply run is not ready for outbox handoff")
            lease_expired = datetime.fromisoformat(str(row["expires_at"])).astimezone(
                UTC
            ) <= now.astimezone(UTC)
            if row["lease_token"] != lease_token or lease_expired:
                connection.rollback()
                raise ValueError("reply run lease does not authorize outbox handoff")
            if not row["reply_plan_json"]:
                connection.rollback()
                raise ValueError("reply run has no validated reply plan")
            try:
                plan = restore_reply_plan(json.loads(row["reply_plan_json"]))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                connection.rollback()
                raise ValueError("reply run has an invalid reply plan") from exc
            if plan.run_id != run_id:
                connection.rollback()
                raise ValueError("reply plan does not belong to the reply run")

            created_count = 0
            outbox_ids: list[str] = []
            for bubble in plan.bubbles:
                expected_key = f"{run_id}:bubble:{bubble.sequence}"
                if bubble.idempotency_key != expected_key:
                    connection.rollback()
                    raise ValueError("reply bubble idempotency key is not canonical")
                outbox_id = str(uuid5(NAMESPACE_URL, f"ych-reply-outbox:{bubble.idempotency_key}"))
                segments_json = _json([segment.as_onebot() for segment in bubble.segments])
                existing = connection.execute(
                    "SELECT * FROM outbox WHERE idempotency_key = ?",
                    (bubble.idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["id"] != outbox_id
                        or existing["conversation_kind"] != row["conversation_kind"]
                        or existing["target_id"] != row["peer_id"]
                        or existing["segments_json"] != segments_json
                    ):
                        connection.rollback()
                        raise ValueError("reply outbox idempotency payload conflict")
                else:
                    ordered_at = _iso(now + timedelta(microseconds=bubble.sequence - 1))
                    connection.execute(
                        """
                        INSERT INTO outbox(
                            id, idempotency_key, conversation_kind, target_id,
                            segments_json, status, available_at, created_at
                        ) VALUES(?, ?, ?, ?, ?, 'pending', ?, ?)
                        """,
                        (
                            outbox_id,
                            bubble.idempotency_key,
                            row["conversation_kind"],
                            row["peer_id"],
                            segments_json,
                            ordered_at,
                            ordered_at,
                        ),
                    )
                    created_count += 1
                evidence_id = str(uuid5(NAMESPACE_URL, f"ych-reply-delivery:{outbox_id}:1:queued"))
                connection.execute(
                    """
                    INSERT OR IGNORE INTO reply_delivery_evidence(
                        id, run_id, outbox_id, bubble_sequence, attempt,
                        idempotency_key, outcome, evidence_json, observed_at
                    ) VALUES(?, ?, ?, ?, 1, ?, 'queued', ?, ?)
                    """,
                    (
                        evidence_id,
                        run_id,
                        outbox_id,
                        bubble.sequence,
                        bubble.idempotency_key,
                        _json({"source": "reply_plan_handoff"}),
                        now_iso,
                    ),
                )
                queued = connection.execute(
                    """
                    SELECT id, run_id, bubble_sequence, idempotency_key
                    FROM reply_delivery_evidence
                    WHERE outbox_id = ? AND attempt = 1 AND outcome = 'queued'
                    """,
                    (outbox_id,),
                ).fetchone()
                if queued is None or (
                    queued["id"] != evidence_id
                    or queued["run_id"] != run_id
                    or int(queued["bubble_sequence"]) != bubble.sequence
                    or queued["idempotency_key"] != bubble.idempotency_key
                ):
                    connection.rollback()
                    raise ValueError("reply delivery evidence mapping conflict")
                outbox_ids.append(outbox_id)

            recovered = row["stage"] == ReplyRunStage.AWAITING_DELIVERY.value
            if not recovered:
                transitioned = connection.execute(
                    """
                    UPDATE reply_runs SET stage = 'awaiting_delivery', updated_at = ?
                    WHERE id = ? AND stage = 'creating_outbox'
                    """,
                    (now_iso, run_id),
                )
                if transitioned.rowcount != 1:
                    connection.rollback()
                    raise ValueError("reply run could not enter awaiting delivery")
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        action, subject_type, subject_id, details_json, created_at
                    ) VALUES('reply.outbox_handoff', 'reply_run', ?, ?, ?)
                    """,
                    (
                        run_id,
                        _json(
                            {
                                "outbox_ids": outbox_ids,
                                "bubble_count": len(outbox_ids),
                            }
                        ),
                        now_iso,
                    ),
                )
            connection.commit()
        return {
            "run_id": run_id,
            "outbox_ids": outbox_ids,
            "created_count": created_count,
            "recovered": recovered,
        }

    async def begin_reply_delivery(self, outbox_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._begin_reply_delivery_sync, outbox_id)

    def _begin_reply_delivery_sync(self, outbox_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT evidence.run_id, evidence.bubble_sequence,
                       evidence.idempotency_key, outbox.attempts
                FROM reply_delivery_evidence AS evidence
                JOIN outbox ON outbox.id = evidence.outbox_id
                JOIN reply_runs ON reply_runs.id = evidence.run_id
                WHERE evidence.outbox_id = ? AND evidence.outcome = 'queued'
                  AND outbox.status = 'sending'
                  AND reply_runs.stage = 'awaiting_delivery'
                """,
                (outbox_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            attempt = int(row["attempts"])
            evidence_id = str(
                uuid5(NAMESPACE_URL, f"ych-reply-delivery:{outbox_id}:{attempt}:sending")
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO reply_delivery_evidence(
                    id, run_id, outbox_id, bubble_sequence, attempt,
                    idempotency_key, outcome, evidence_json, observed_at
                ) VALUES(?, ?, ?, ?, ?, ?, 'sending', ?, ?)
                """,
                (
                    evidence_id,
                    row["run_id"],
                    outbox_id,
                    row["bubble_sequence"],
                    attempt,
                    row["idempotency_key"],
                    _json({"source": "outbox_dispatcher"}),
                    _iso(_utc_now()),
                ),
            )
            connection.commit()
        return {
            "run_id": str(row["run_id"]),
            "bubble_sequence": int(row["bubble_sequence"]),
            "attempt": attempt,
            "idempotency_key": str(row["idempotency_key"]),
        }

    async def finalize_reply_delivery(
        self,
        *,
        outbox_id: str,
        outcome: str,
        safe_detail: str,
        provider_message_id: str | None = None,
    ) -> dict[str, Any] | None:
        if outcome not in {"delivered", "rejected", "delivery_unknown"}:
            raise ValueError("unsupported reply delivery outcome")
        return await asyncio.to_thread(
            self._finalize_reply_delivery_sync,
            outbox_id,
            outcome,
            safe_detail,
            provider_message_id,
        )

    def _finalize_reply_delivery_sync(
        self,
        outbox_id: str,
        outcome: str,
        safe_detail: str,
        provider_message_id: str | None,
    ) -> dict[str, Any] | None:
        now_iso = _iso(_utc_now())
        terminal_status = {
            "delivered": "sent",
            "rejected": "rejected",
            "delivery_unknown": "delivery_unknown",
        }[outcome]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT evidence.run_id, evidence.bubble_sequence,
                       evidence.idempotency_key, outbox.attempts, outbox.status
                FROM reply_delivery_evidence AS evidence
                JOIN outbox ON outbox.id = evidence.outbox_id
                WHERE evidence.outbox_id = ? AND evidence.outcome = 'queued'
                """,
                (outbox_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            if row["status"] == terminal_status:
                connection.rollback()
                return {
                    "run_id": str(row["run_id"]),
                    "outcome": outcome,
                    "run_completed": outcome == "delivered",
                    "recovered": True,
                }
            if row["status"] != "sending":
                connection.rollback()
                raise ValueError("reply outbox item is not in sending state")

            attempt = int(row["attempts"])
            evidence_id = str(
                uuid5(NAMESPACE_URL, f"ych-reply-delivery:{outbox_id}:{attempt}:{outcome}")
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO reply_delivery_evidence(
                    id, run_id, outbox_id, bubble_sequence, attempt,
                    idempotency_key, outcome, provider_message_id,
                    evidence_json, observed_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    row["run_id"],
                    outbox_id,
                    row["bubble_sequence"],
                    attempt,
                    row["idempotency_key"],
                    outcome,
                    provider_message_id,
                    _json({"source": "outbox_dispatcher", "detail": safe_detail}),
                    now_iso,
                ),
            )
            connection.execute(
                """
                UPDATE outbox SET status = ?, sent_at = ?, last_error = ?
                WHERE id = ? AND status = 'sending'
                """,
                (
                    terminal_status,
                    now_iso if outcome == "delivered" else None,
                    None if outcome == "delivered" else safe_detail[:1000],
                    outbox_id,
                ),
            )

            run_completed = False
            if outcome == "delivered":
                remaining = connection.execute(
                    """
                    SELECT 1
                    FROM reply_delivery_evidence AS mapping
                    JOIN outbox ON outbox.id = mapping.outbox_id
                    WHERE mapping.run_id = ? AND mapping.outcome = 'queued'
                      AND outbox.status != 'sent'
                    LIMIT 1
                    """,
                    (row["run_id"],),
                ).fetchone()
                if remaining is None:
                    updated = connection.execute(
                        """
                        UPDATE reply_runs
                        SET stage = 'completed', completed_at = ?, updated_at = ?
                        WHERE id = ? AND stage = 'awaiting_delivery'
                        """,
                        (now_iso, now_iso, row["run_id"]),
                    )
                    run_completed = updated.rowcount == 1
            else:
                failure_code = (
                    "onebot_send_rejected" if outcome == "rejected" else "onebot_delivery_unknown"
                )
                failure_category = (
                    "delivery_rejected" if outcome == "rejected" else "delivery_unknown"
                )
                connection.execute(
                    """
                    UPDATE reply_runs SET
                        stage = 'failed', failure_code = ?, failure_category = ?,
                        failure_detail = ?, completed_at = ?, updated_at = ?
                    WHERE id = ? AND stage = 'awaiting_delivery'
                    """,
                    (
                        failure_code,
                        failure_category,
                        safe_detail,
                        now_iso,
                        now_iso,
                        row["run_id"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE outbox
                    SET status = 'cancelled', last_error = 'reply_run_terminal_failure'
                    WHERE id IN (
                        SELECT outbox_id FROM reply_delivery_evidence
                        WHERE run_id = ? AND outcome = 'queued'
                    ) AND status IN ('pending', 'failed')
                    """,
                    (row["run_id"],),
                )

            if run_completed or outcome != "delivered":
                connection.execute(
                    "DELETE FROM reply_run_leases WHERE run_id = ?", (row["run_id"],)
                )
            connection.execute(
                """
                INSERT INTO audit_log(
                    action, subject_type, subject_id, details_json, created_at
                ) VALUES('reply.delivery_outcome', 'reply_run', ?, ?, ?)
                """,
                (
                    row["run_id"],
                    _json(
                        {
                            "outbox_id": outbox_id,
                            "bubble_sequence": int(row["bubble_sequence"]),
                            "attempt": attempt,
                            "outcome": outcome,
                        }
                    ),
                    now_iso,
                ),
            )
            connection.commit()
        return {
            "run_id": str(row["run_id"]),
            "outcome": outcome,
            "run_completed": run_completed,
            "recovered": False,
        }

    async def quarantine_interrupted_reply_deliveries(self) -> int:
        outbox_ids = await asyncio.to_thread(self._sending_reply_outbox_ids_sync)
        quarantined = 0
        for outbox_id in outbox_ids:
            result = await self.finalize_reply_delivery(
                outbox_id=outbox_id,
                outcome="delivery_unknown",
                safe_detail="reply delivery was interrupted before confirmation",
            )
            quarantined += int(result is not None)
        return quarantined

    def _sending_reply_outbox_ids_sync(self) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT outbox.id
                FROM outbox
                JOIN reply_delivery_evidence AS mapping
                  ON mapping.outbox_id = outbox.id AND mapping.outcome = 'queued'
                WHERE outbox.status = 'sending'
                ORDER BY outbox.created_at
                """
            ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    async def record_reply_delivery_evidence(
        self,
        *,
        evidence_id: str,
        run_id: str,
        outbox_id: str,
        bubble_sequence: int,
        attempt: int,
        idempotency_key: str,
        outcome: str,
        evidence: dict[str, Any],
        provider_message_id: str | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._record_reply_delivery_evidence_sync,
            evidence_id,
            run_id,
            outbox_id,
            bubble_sequence,
            attempt,
            idempotency_key,
            outcome,
            evidence,
            provider_message_id,
        )

    def _record_reply_delivery_evidence_sync(
        self,
        evidence_id: str,
        run_id: str,
        outbox_id: str,
        bubble_sequence: int,
        attempt: int,
        idempotency_key: str,
        outcome: str,
        evidence: dict[str, Any],
        provider_message_id: str | None,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO reply_delivery_evidence(
                    id, run_id, outbox_id, bubble_sequence, attempt,
                    idempotency_key, outcome, provider_message_id,
                    evidence_json, observed_at
                )
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                FROM reply_runs
                JOIN outbox ON outbox.id = ?
                WHERE reply_runs.id = ? AND outbox.idempotency_key = ?
                """,
                (
                    evidence_id,
                    run_id,
                    outbox_id,
                    bubble_sequence,
                    attempt,
                    idempotency_key,
                    outcome,
                    provider_message_id,
                    _json(evidence),
                    _iso(_utc_now()),
                    outbox_id,
                    run_id,
                    idempotency_key,
                ),
            )
        return cursor.rowcount == 1

    async def reply_run_detail(self, run_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._reply_run_detail_sync, run_id)

    def _reply_run_detail_sync(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT reply_runs.*, conversations.peer_id
                FROM reply_runs
                JOIN conversations
                  ON conversations.conversation_key = reply_runs.conversation_key
                WHERE reply_runs.id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            triggers = connection.execute(
                """
                SELECT reply_run_triggers.*, messages.sender_id, messages.direction,
                       messages.occurred_at, messages.plain_text, messages.segments_json,
                       messages.reply_to_message_id
                FROM reply_run_triggers
                JOIN messages ON messages.id = reply_run_triggers.message_id
                WHERE reply_run_triggers.run_id = ?
                ORDER BY reply_run_triggers.sequence
                """,
                (run_id,),
            ).fetchall()
            manifests = connection.execute(
                """
                SELECT * FROM reply_context_manifests
                WHERE run_id = ? ORDER BY revision DESC
                """,
                (run_id,),
            ).fetchall()
            lease = connection.execute(
                "SELECT * FROM reply_run_leases WHERE run_id = ?", (run_id,)
            ).fetchone()
            deliveries = connection.execute(
                """
                SELECT * FROM reply_delivery_evidence
                WHERE run_id = ? ORDER BY bubble_sequence, attempt, observed_at
                """,
                (run_id,),
            ).fetchall()
        result = _reply_run_row(row)
        result["triggers"] = [
            {**dict(item), "segments": json.loads(item["segments_json"])} for item in triggers
        ]
        result["context_manifests"] = [
            {**dict(item), "manifest": json.loads(item["manifest_json"])} for item in manifests
        ]
        result["lease"] = dict(lease) if lease else None
        result["delivery_evidence"] = [
            {**dict(item), "evidence": json.loads(item["evidence_json"])} for item in deliveries
        ]
        return result
