"""Durable reply-runtime policy, eligibility, approval, and audit persistence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ych_bot.domain.reply_pipeline import ReplyActivationMode, ReplyRuntimeLifecycle


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("reply runtime timestamps must be timezone-aware")
    return current.astimezone(UTC)


def _state(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["emergency_paused"] = bool(value["emergency_paused"])
    return value


class ReplyRuntimeRepositoryMixin:
    def _connect(self) -> sqlite3.Connection:  # pragma: no cover
        raise NotImplementedError

    async def ensure_reply_runtime_state(self, bot_qq: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._ensure_reply_runtime_state_sync, bot_qq)

    def _ensure_reply_runtime_state_sync(self, bot_qq: str) -> dict[str, Any]:
        if not bot_qq.isdigit():
            raise ValueError("bot_qq must contain digits only")
        now = _now().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO reply_runtime_state(
                    bot_qq, requested_mode, emergency_paused, revision, lifecycle,
                    updated_by, updated_source, created_at, updated_at
                ) VALUES(?, 'observe_only', 1, 1, 'disabled', 'system', 'system', ?, ?)
                """,
                (bot_qq, now, now),
            )
            row = connection.execute(
                "SELECT * FROM reply_runtime_state WHERE bot_qq = ?", (bot_qq,)
            ).fetchone()
        return _state(row)

    async def reply_runtime_state(self, bot_qq: str) -> dict[str, Any]:
        return await self.ensure_reply_runtime_state(bot_qq)

    async def compare_and_set_reply_runtime(
        self,
        *,
        bot_qq: str,
        expected_revision: int,
        requested_mode: ReplyActivationMode | None = None,
        emergency_paused: bool | None = None,
        lifecycle: ReplyRuntimeLifecycle | None = None,
        actor: str,
        source: str,
        event_type: str,
        details: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._compare_and_set_reply_runtime_sync,
            bot_qq,
            expected_revision,
            requested_mode,
            emergency_paused,
            lifecycle,
            actor,
            source,
            event_type,
            details or {},
            _now(now),
        )

    def _compare_and_set_reply_runtime_sync(
        self,
        bot_qq: str,
        expected_revision: int,
        requested_mode: ReplyActivationMode | None,
        emergency_paused: bool | None,
        lifecycle: ReplyRuntimeLifecycle | None,
        actor: str,
        source: str,
        event_type: str,
        details: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any] | None:
        now_iso = now.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM reply_runtime_state WHERE bot_qq = ?", (bot_qq,)
            ).fetchone()
            if row is None or int(row["revision"]) != expected_revision:
                connection.rollback()
                return None
            next_revision = expected_revision + 1
            cursor = connection.execute(
                """
                UPDATE reply_runtime_state SET
                    requested_mode = COALESCE(?, requested_mode),
                    emergency_paused = COALESCE(?, emergency_paused),
                    lifecycle = COALESCE(?, lifecycle),
                    revision = ?, updated_by = ?, updated_source = ?, updated_at = ?
                WHERE bot_qq = ? AND revision = ?
                """,
                (
                    requested_mode.value if requested_mode else None,
                    int(emergency_paused) if emergency_paused is not None else None,
                    lifecycle.value if lifecycle else None,
                    next_revision,
                    actor,
                    source,
                    now_iso,
                    bot_qq,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            self._insert_runtime_event(
                connection,
                bot_qq=bot_qq,
                event_type=event_type,
                actor=actor,
                source=source,
                prior_revision=expected_revision,
                resulting_revision=next_revision,
                details=details,
                now_iso=now_iso,
            )
            updated = connection.execute(
                "SELECT * FROM reply_runtime_state WHERE bot_qq = ?", (bot_qq,)
            ).fetchone()
            connection.commit()
        return _state(updated)

    async def update_reply_worker_health(
        self,
        *,
        bot_qq: str,
        lifecycle: ReplyRuntimeLifecycle,
        failure_code: str | None = None,
        claimed: bool = False,
        progressed: bool = False,
        recovered: bool = False,
        now: datetime | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._update_reply_worker_health_sync,
            bot_qq,
            lifecycle,
            failure_code,
            claimed,
            progressed,
            recovered,
            _now(now),
        )

    def _update_reply_worker_health_sync(
        self,
        bot_qq: str,
        lifecycle: ReplyRuntimeLifecycle,
        failure_code: str | None,
        claimed: bool,
        progressed: bool,
        recovered: bool,
        now: datetime,
    ) -> None:
        now_iso = now.isoformat()
        self._ensure_reply_runtime_state_sync(bot_qq)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE reply_runtime_state SET lifecycle = ?, last_iteration_at = ?,
                    last_claim_at = CASE WHEN ? THEN ? ELSE last_claim_at END,
                    last_progress_at = CASE WHEN ? THEN ? ELSE last_progress_at END,
                    last_failure_code = ?,
                    recovered_count = recovered_count + ?, updated_at = ?
                WHERE bot_qq = ?
                """,
                (
                    lifecycle.value,
                    now_iso,
                    int(claimed),
                    now_iso,
                    int(progressed),
                    now_iso,
                    failure_code,
                    int(recovered),
                    now_iso,
                    bot_qq,
                ),
            )

    async def set_reply_eligibility(
        self,
        *,
        bot_qq: str,
        conversation_key: str,
        enabled: bool,
        expected_revision: int | None,
        actor: str,
        source: str,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._set_reply_eligibility_sync,
            bot_qq,
            conversation_key,
            enabled,
            expected_revision,
            actor,
            source,
            _now(now),
        )

    def _set_reply_eligibility_sync(
        self,
        bot_qq: str,
        conversation_key: str,
        enabled: bool,
        expected_revision: int | None,
        actor: str,
        source: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        if not conversation_key.startswith(f"{bot_qq}:"):
            raise ValueError("eligibility conversation does not belong to bot")
        now_iso = now.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM reply_runtime_eligibility WHERE bot_qq = ? AND conversation_key = ?",
                (bot_qq, conversation_key),
            ).fetchone()
            current_revision = int(current["revision"]) if current else 0
            if expected_revision is not None and current_revision != expected_revision:
                connection.rollback()
                return None
            next_revision = current_revision + 1
            connection.execute(
                """
                INSERT INTO reply_runtime_eligibility(
                    bot_qq, conversation_key, enabled, revision, updated_by,
                    updated_source, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_qq, conversation_key) DO UPDATE SET
                    enabled = excluded.enabled, revision = excluded.revision,
                    updated_by = excluded.updated_by, updated_source = excluded.updated_source,
                    updated_at = excluded.updated_at
                """,
                (
                    bot_qq,
                    conversation_key,
                    int(enabled),
                    next_revision,
                    actor,
                    source,
                    now_iso,
                    now_iso,
                ),
            )
            self._insert_runtime_event(
                connection,
                bot_qq=bot_qq,
                event_type="eligibility_changed",
                actor=actor,
                source=source,
                prior_revision=current_revision,
                resulting_revision=next_revision,
                details={"conversation_key": conversation_key, "enabled": enabled},
                now_iso=now_iso,
            )
            row = connection.execute(
                "SELECT * FROM reply_runtime_eligibility WHERE bot_qq = ? AND conversation_key = ?",
                (bot_qq, conversation_key),
            ).fetchone()
            connection.commit()
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        return result

    async def reply_eligibility(self, bot_qq: str) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM reply_runtime_eligibility
                    WHERE bot_qq = ? ORDER BY conversation_key
                    """,
                    (bot_qq,),
                ).fetchall()
            return [{**dict(row), "enabled": bool(row["enabled"])} for row in rows]

        return await asyncio.to_thread(read)

    async def conversation_reply_eligible(self, bot_qq: str, conversation_key: str) -> bool:
        items = await self.reply_eligibility(bot_qq)
        return any(
            item["conversation_key"] == conversation_key and item["enabled"] for item in items
        )

    async def create_reply_runtime_approval(
        self,
        *,
        run_id: str,
        plan_hash: str,
        runtime_revision: int,
        requested_by: str,
        ttl_seconds: int = 1800,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _now(now)
        return await asyncio.to_thread(
            self._create_reply_runtime_approval_sync,
            run_id,
            plan_hash,
            runtime_revision,
            requested_by,
            ttl_seconds,
            current,
        )

    def _create_reply_runtime_approval_sync(
        self,
        run_id: str,
        plan_hash: str,
        runtime_revision: int,
        requested_by: str,
        ttl_seconds: int,
        now: datetime,
    ) -> dict[str, Any]:
        if not 60 <= ttl_seconds <= 3600:
            raise ValueError("reply approval ttl must be between 60 and 3600 seconds")
        approval_id = str(uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reply_runtime_approvals(
                    id, run_id, plan_hash, runtime_revision, status, requested_by,
                    expires_at, created_at
                ) VALUES(?, ?, ?, ?, 'pending', ?, ?, ?)
                ON CONFLICT(run_id, plan_hash, runtime_revision) DO NOTHING
                """,
                (
                    approval_id,
                    run_id,
                    plan_hash,
                    runtime_revision,
                    requested_by,
                    (now + timedelta(seconds=ttl_seconds)).isoformat(),
                    now.isoformat(),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM reply_runtime_approvals
                WHERE run_id = ? AND plan_hash = ? AND runtime_revision = ?
                """,
                (run_id, plan_hash, runtime_revision),
            ).fetchone()
        return dict(row)

    async def decide_reply_runtime_approval(
        self,
        *,
        approval_id: str,
        approve: bool,
        actor: str,
        cancel: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._decide_reply_runtime_approval_sync,
            approval_id,
            approve,
            actor,
            cancel,
            _now(now),
        )

    async def reply_runtime_approval(self, approval_id: str) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM reply_runtime_approvals WHERE id = ?", (approval_id,)
                ).fetchone()
            return dict(row) if row else None

        return await asyncio.to_thread(read)

    def _decide_reply_runtime_approval_sync(
        self,
        approval_id: str,
        approve: bool,
        actor: str,
        cancel: bool,
        now: datetime,
    ) -> dict[str, Any] | None:
        now_iso = now.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE reply_runtime_approvals SET status = 'expired', decided_at = ?
                WHERE id = ? AND status = 'pending' AND expires_at <= ?
                """,
                (now_iso, approval_id, now_iso),
            )
            cursor = connection.execute(
                """
                UPDATE reply_runtime_approvals
                SET status = ?, decided_by = ?, decided_at = ?
                WHERE id = ? AND status = 'pending' AND expires_at > ?
                """,
                (
                    "cancelled" if cancel else ("approved" if approve else "rejected"),
                    actor,
                    now_iso,
                    approval_id,
                    now_iso,
                ),
            )
            row = connection.execute(
                "SELECT * FROM reply_runtime_approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            connection.commit()
        return dict(row) if cursor.rowcount == 1 and row else None

    async def reply_runtime_approvals(
        self, bot_qq: str, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            clause = "AND approvals.status = ?" if status else ""
            params: list[Any] = [bot_qq]
            if status:
                params.append(status)
            with self._connect() as connection:
                rows = connection.execute(
                    f"""
                    SELECT approvals.* FROM reply_runtime_approvals AS approvals
                    JOIN reply_runs ON reply_runs.id = approvals.run_id
                    WHERE reply_runs.bot_qq = ? {clause}
                    ORDER BY approvals.created_at DESC
                    """,
                    params,
                ).fetchall()
            return [dict(row) for row in rows]

        return await asyncio.to_thread(read)

    async def reply_runtime_events(self, bot_qq: str, limit: int = 100) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM reply_runtime_events WHERE bot_qq = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (bot_qq, max(1, min(limit, 200))),
                ).fetchall()
            return [{**dict(row), "details": json.loads(row["details_json"])} for row in rows]

        return await asyncio.to_thread(read)

    async def record_reply_runtime_event(
        self,
        *,
        bot_qq: str,
        event_type: str,
        actor: str,
        source: str,
        details: dict[str, Any],
        now: datetime | None = None,
    ) -> None:
        current = _now(now)

        def write() -> None:
            with self._connect() as connection:
                state = connection.execute(
                    "SELECT revision FROM reply_runtime_state WHERE bot_qq = ?", (bot_qq,)
                ).fetchone()
                revision = int(state["revision"]) if state else None
                self._insert_runtime_event(
                    connection,
                    bot_qq=bot_qq,
                    event_type=event_type,
                    actor=actor,
                    source=source,
                    prior_revision=revision,
                    resulting_revision=revision,
                    details=details,
                    now_iso=current.isoformat(),
                )

        await asyncio.to_thread(write)

    @staticmethod
    def plan_hash(plan_json: dict[str, Any]) -> str:
        payload = json.dumps(plan_json, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _insert_runtime_event(
        connection: sqlite3.Connection,
        *,
        bot_qq: str,
        event_type: str,
        actor: str,
        source: str,
        prior_revision: int | None,
        resulting_revision: int | None,
        details: dict[str, Any],
        now_iso: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO reply_runtime_events(
                id, bot_qq, event_type, actor, source, prior_revision,
                resulting_revision, details_json, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                bot_qq,
                event_type,
                actor,
                source,
                prior_revision,
                resulting_revision,
                json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                now_iso,
            ),
        )
