"""SQLite operations for the QQ Zone scheduling state machine."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ych_bot.domain.control import QzonePostStatus, QzoneVisibility
from ych_bot.domain.proactive import MissedTaskPolicy
from ych_bot.domain.qzone import QzoneSchedulePolicy
from ych_bot.infrastructure.database.reports import record_owner_report_on_connection


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class QzoneRepositoryMixin:
    """Mixed into ``SQLiteRepository`` to isolate the space workflow."""

    def _connect(self) -> sqlite3.Connection:  # pragma: no cover - supplied by repository
        raise NotImplementedError

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        *,
        post_id: str,
        event_type: str,
        reason: str | None,
        details: dict[str, Any],
        occurred_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO qzone_post_events(
                id, post_id, event_type, reason, details_json, occurred_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (str(uuid4()), post_id, event_type, reason, _json(details), occurred_at),
        )

    @staticmethod
    def _report(
        connection: sqlite3.Connection,
        *,
        post_id: str,
        severity: str,
        title: str,
        body: str,
        now_iso: str,
    ) -> None:
        record_owner_report_on_connection(
            connection,
            report_id=str(uuid4()),
            severity=severity,
            category="qzone",
            title=title,
            body=body,
            related_type="qzone_post",
            related_id=post_id,
            now=datetime.fromisoformat(now_iso),
        )

    @staticmethod
    def _approval(
        connection: sqlite3.Connection,
        *,
        post_id: str,
        approval_id: str,
        approval_code: str,
        requested_to: str,
        request_type: str,
        now: datetime,
        target_uins: tuple[str, ...] = (),
    ) -> None:
        now_iso = now.astimezone(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO approval_requests(
                id, request_type, subject_id, approval_code, status,
                requested_to, expires_at, created_at
            ) VALUES(?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                approval_id,
                request_type,
                post_id,
                approval_code,
                requested_to,
                (now + timedelta(minutes=30)).isoformat(),
                now_iso,
            ),
        )
        connection.execute(
            "INSERT INTO approval_payloads(approval_id, payload_json) VALUES(?, ?)",
            (approval_id, _json({"post_id": post_id, "target_uins": list(target_uins)})),
        )

    @staticmethod
    def _targeted_post_ids(connection: sqlite3.Connection, user_qq: str) -> list[str]:
        return [
            str(row[0])
            for row in connection.execute(
                """
                SELECT id FROM qzone_posts
                WHERE EXISTS (
                    SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                    WHERE CAST(json_each.value AS TEXT) = ?
                )
                """,
                (user_qq,),
            ).fetchall()
        ]

    @staticmethod
    def _ensure_policy(
        connection: sqlite3.Connection,
        policy: QzoneSchedulePolicy,
        *,
        updated_by: str,
        now_iso: str,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO qzone_schedule_policy(
                singleton_id, timezone, quiet_hours_enabled, quiet_start,
                quiet_end, quiet_behavior, daily_limit,
                minimum_interval_seconds, updated_by, updated_at
            ) VALUES(1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy.timezone,
                int(policy.quiet_hours_enabled),
                policy.quiet_start,
                policy.quiet_end,
                policy.quiet_behavior.value,
                policy.daily_limit,
                policy.minimum_interval_seconds,
                updated_by,
                now_iso,
            ),
        )

    async def create_qzone_draft(
        self,
        *,
        post_id: str,
        content: str,
        visibility: QzoneVisibility,
        target_uins: tuple[str, ...] = (),
        source: str,
        created_by: str,
        timezone_name: str,
        now: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._create_qzone_draft_sync,
            post_id,
            content,
            visibility,
            target_uins,
            source,
            created_by,
            timezone_name,
            now,
        )

    def _create_qzone_draft_sync(
        self,
        post_id: str,
        content: str,
        visibility: QzoneVisibility,
        target_uins: tuple[str, ...],
        source: str,
        created_by: str,
        timezone_name: str,
        now: datetime,
    ) -> None:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO qzone_posts(
                    id, content, images_json, visibility, target_uins_json,
                    source, status, created_by, created_at, updated_at
                ) VALUES(?, ?, '[]', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post_id,
                    content,
                    int(visibility),
                    _json(list(target_uins)),
                    source,
                    QzonePostStatus.DRAFT.value,
                    created_by,
                    now_iso,
                    now_iso,
                ),
            )
            connection.execute(
                """
                INSERT INTO qzone_post_runtime(
                    post_id, original_scheduled_for, timezone, missed_policy,
                    missed_grace_seconds, next_eligible_at, updated_at
                ) VALUES(?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    post_id,
                    now_iso,
                    timezone_name,
                    MissedTaskPolicy.SKIP.value,
                    now_iso,
                    now_iso,
                ),
            )
            self._event(
                connection,
                post_id=post_id,
                event_type="draft_created",
                reason=source,
                details={},
                occurred_at=now_iso,
            )
            connection.commit()

    async def create_qzone_publish_request(
        self,
        *,
        post_id: str,
        content: str,
        visibility: QzoneVisibility,
        target_uins: tuple[str, ...] = (),
        scheduled_for: datetime,
        timezone_name: str,
        missed_policy: MissedTaskPolicy,
        missed_grace_seconds: int,
        source: str,
        created_by: str,
        approval_id: str,
        approval_code: str,
        requested_to: str,
        default_policy: QzoneSchedulePolicy,
        now: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._create_qzone_publish_request_sync,
            post_id,
            content,
            visibility,
            target_uins,
            scheduled_for,
            timezone_name,
            missed_policy,
            missed_grace_seconds,
            source,
            created_by,
            approval_id,
            approval_code,
            requested_to,
            default_policy,
            now,
        )

    def _create_qzone_publish_request_sync(
        self,
        post_id: str,
        content: str,
        visibility: QzoneVisibility,
        target_uins: tuple[str, ...],
        scheduled_for: datetime,
        timezone_name: str,
        missed_policy: MissedTaskPolicy,
        missed_grace_seconds: int,
        source: str,
        created_by: str,
        approval_id: str,
        approval_code: str,
        requested_to: str,
        default_policy: QzoneSchedulePolicy,
        now: datetime,
    ) -> None:
        now_iso = now.astimezone(UTC).isoformat()
        scheduled_iso = scheduled_for.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_policy(
                connection,
                default_policy,
                updated_by=created_by,
                now_iso=now_iso,
            )
            self._approval(
                connection,
                post_id=post_id,
                approval_id=approval_id,
                approval_code=approval_code,
                requested_to=requested_to,
                request_type="qzone.publish",
                now=now,
                target_uins=target_uins,
            )
            connection.execute(
                """
                INSERT INTO qzone_posts(
                    id, content, images_json, visibility, target_uins_json,
                    source, status, scheduled_for, created_by, created_at, updated_at
                ) VALUES(?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post_id,
                    content,
                    int(visibility),
                    _json(list(target_uins)),
                    source,
                    QzonePostStatus.PENDING_APPROVAL.value,
                    scheduled_iso,
                    created_by,
                    now_iso,
                    now_iso,
                ),
            )
            connection.execute(
                """
                INSERT INTO qzone_post_runtime(
                    post_id, original_scheduled_for, timezone, missed_policy,
                    missed_grace_seconds, approval_id, next_eligible_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post_id,
                    scheduled_iso,
                    timezone_name,
                    missed_policy.value,
                    missed_grace_seconds,
                    approval_id,
                    scheduled_iso,
                    now_iso,
                ),
            )
            self._report(
                connection,
                post_id=post_id,
                severity="action_required",
                title="QQ 空间发布任务待审批",
                body=(
                    f"空间任务 {post_id} 计划于 {scheduled_iso} 发布；"
                    f"可见范围 {int(visibility)}。确认码 {approval_code}。"
                ),
                now_iso=now_iso,
            )
            self._event(
                connection,
                post_id=post_id,
                event_type="publish_requested",
                reason="awaiting_owner_approval",
                details={"approval_id": approval_id, "source": source},
                occurred_at=now_iso,
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('qzone.publish_requested', 'qzone_post', ?, ?, ?)
                """,
                (
                    post_id,
                    _json(
                        {
                            "scheduled_for": scheduled_iso,
                            "visibility": int(visibility),
                            "missed_policy": missed_policy.value,
                            "target_uins": list(target_uins),
                        }
                    ),
                    now_iso,
                ),
            )
            connection.commit()

    async def qzone_post(self, post_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._qzone_post_sync, post_id)

    def _qzone_post_sync(self, post_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                self._qzone_select() + " WHERE qzone_posts.id = ?",
                (post_id,),
            ).fetchone()
            if row is None:
                return None
            events = connection.execute(
                """
                SELECT id, event_type, reason, details_json, occurred_at
                FROM qzone_post_events WHERE post_id = ? ORDER BY occurred_at ASC
                """,
                (post_id,),
            ).fetchall()
        result = self._decode_row(row)
        result["events"] = [
            {**dict(event), "details": json.loads(event["details_json"])} for event in events
        ]
        return result

    async def qzone_posts(
        self,
        *,
        status: QzonePostStatus | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._qzone_posts_sync,
            status,
            date_from,
            date_to,
            limit,
        )

    def _qzone_posts_sync(
        self,
        status: QzonePostStatus | None,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if status is not None:
            clauses.append("qzone_posts.status = ?")
            parameters.append(status.value)
        if date_from is not None:
            clauses.append("qzone_post_runtime.original_scheduled_for >= ?")
            parameters.append(date_from.astimezone(UTC).isoformat())
        if date_to is not None:
            clauses.append("qzone_post_runtime.original_scheduled_for < ?")
            parameters.append(date_to.astimezone(UTC).isoformat())
        query = self._qzone_select()
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY qzone_post_runtime.original_scheduled_for ASC LIMIT ?"
        parameters.append(max(1, min(limit, 500)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return [self._decode_row(row) for row in rows]

    @staticmethod
    def _qzone_select() -> str:
        return """
            SELECT qzone_posts.*, qzone_post_runtime.original_scheduled_for,
                   qzone_post_runtime.timezone, qzone_post_runtime.missed_policy,
                   qzone_post_runtime.missed_grace_seconds,
                   qzone_post_runtime.approval_id, qzone_post_runtime.next_eligible_at,
                   qzone_post_runtime.hold_reason, qzone_post_runtime.first_evaluated_at,
                   qzone_post_runtime.last_evaluated_at,
                   qzone_post_runtime.lease_expires_at,
                   qzone_post_runtime.evaluation_attempts,
                   qzone_post_runtime.owner_exception
            FROM qzone_posts
            JOIN qzone_post_runtime ON qzone_post_runtime.post_id = qzone_posts.id
        """

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["images"] = json.loads(result.pop("images_json"))
        result["target_uins"] = json.loads(result.pop("target_uins_json"))
        result["owner_exception"] = bool(result["owner_exception"])
        if result.get("last_error"):
            result["last_error"] = str(result["last_error"])[:500]
        return result

    async def approve_qzone_post(
        self,
        post_id: str,
        *,
        actor_qq: str,
        approved_at: datetime,
        late: bool,
    ) -> bool:
        return await asyncio.to_thread(
            self._approve_qzone_post_sync,
            post_id,
            actor_qq,
            approved_at,
            late,
        )

    def _approve_qzone_post_sync(
        self,
        post_id: str,
        actor_qq: str,
        approved_at: datetime,
        late: bool,
    ) -> bool:
        now_iso = approved_at.astimezone(UTC).isoformat()
        expected = (
            QzonePostStatus.REAPPROVAL_REQUIRED.value
            if late
            else QzonePostStatus.PENDING_APPROVAL.value
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE qzone_posts
                SET status = ?, approved_by = ?, approved_at = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    QzonePostStatus.SCHEDULED.value,
                    actor_qq,
                    now_iso,
                    now_iso,
                    post_id,
                    expected,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE qzone_post_runtime
                    SET next_eligible_at = CASE
                            WHEN original_scheduled_for > ? THEN original_scheduled_for ELSE ? END,
                        hold_reason = NULL, owner_exception = ?, updated_at = ?
                    WHERE post_id = ?
                    """,
                    (now_iso, now_iso, int(late), now_iso, post_id),
                )
                connection.execute(
                    """
                    UPDATE owner_reports SET status = 'acknowledged', acknowledged_at = ?
                    WHERE related_type = 'qzone_post' AND related_id = ? AND status = 'pending'
                    """,
                    (now_iso, post_id),
                )
                self._event(
                    connection,
                    post_id=post_id,
                    event_type="approved",
                    reason="late_reapproval" if late else "initial_approval",
                    details={"actor_qq": actor_qq},
                    occurred_at=now_iso,
                )
            connection.commit()
        return updated.rowcount == 1

    async def decide_qzone_post(
        self,
        post_id: str,
        *,
        status: QzonePostStatus,
        actor_qq: str | None,
        decided_at: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._decide_qzone_post_sync,
            post_id,
            status,
            actor_qq,
            decided_at,
        )

    def _decide_qzone_post_sync(
        self,
        post_id: str,
        status: QzonePostStatus,
        actor_qq: str | None,
        decided_at: datetime,
    ) -> bool:
        allowed = {QzonePostStatus.REJECTED, QzonePostStatus.APPROVAL_EXPIRED}
        if status not in allowed:
            raise ValueError("unsupported qzone approval decision")
        now_iso = decided_at.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, updated_at = ?
                WHERE id = ? AND status IN (?, ?)
                """,
                (
                    status.value,
                    now_iso,
                    post_id,
                    QzonePostStatus.PENDING_APPROVAL.value,
                    QzonePostStatus.REAPPROVAL_REQUIRED.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE owner_reports SET status = 'acknowledged', acknowledged_at = ?
                    WHERE related_type = 'qzone_post' AND related_id = ? AND status = 'pending'
                    """,
                    (now_iso, post_id),
                )
                self._event(
                    connection,
                    post_id=post_id,
                    event_type=status.value,
                    reason=status.value,
                    details={"actor_qq": actor_qq},
                    occurred_at=now_iso,
                )
            connection.commit()
        return updated.rowcount == 1

    async def cancel_qzone_post(self, post_id: str, *, actor_qq: str) -> bool:
        return await asyncio.to_thread(self._cancel_qzone_post_sync, post_id, actor_qq)

    def _cancel_qzone_post_sync(self, post_id: str, actor_qq: str) -> bool:
        if self._revert_qzone_delete_request_sync(
            post_id,
            actor_qq,
            "owner_cancelled",
            datetime.now(UTC),
        ):
            return True
        now_iso = _now_iso()
        cancellable = (
            QzonePostStatus.DRAFT.value,
            QzonePostStatus.PENDING_APPROVAL.value,
            QzonePostStatus.SCHEDULED.value,
            QzonePostStatus.EVALUATING.value,
            QzonePostStatus.WAITING_QUIET_HOURS.value,
            QzonePostStatus.WAITING_RATE_LIMIT.value,
            QzonePostStatus.REAPPROVAL_REQUIRED.value,
        )
        placeholders = ",".join("?" for _ in cancellable)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT status FROM qzone_posts WHERE id = ? AND status IN ({placeholders})",
                (post_id, *cancellable),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            connection.execute(
                "UPDATE qzone_posts SET status = ?, updated_at = ? WHERE id = ?",
                (QzonePostStatus.CANCELLED.value, now_iso, post_id),
            )
            connection.execute(
                """
                UPDATE qzone_post_runtime
                SET lease_expires_at = NULL, hold_reason = 'owner_cancelled', updated_at = ?
                WHERE post_id = ?
                """,
                (now_iso, post_id),
            )
            connection.execute(
                """
                UPDATE approval_requests SET status = 'expired', decided_at = ?
                WHERE subject_id = ? AND request_type LIKE 'qzone.%' AND status = 'pending'
                """,
                (now_iso, post_id),
            )
            connection.execute(
                """
                UPDATE owner_reports SET status = 'acknowledged', acknowledged_at = ?
                WHERE related_type = 'qzone_post' AND related_id = ? AND status = 'pending'
                """,
                (now_iso, post_id),
            )
            self._event(
                connection,
                post_id=post_id,
                event_type="cancelled",
                reason="owner_cancelled",
                details={"actor_qq": actor_qq},
                occurred_at=now_iso,
            )
            connection.commit()
        return True

    async def qzone_policy(self) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._qzone_policy_sync)

    def _qzone_policy_sync(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM qzone_schedule_policy WHERE singleton_id = 1"
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["quiet_hours_enabled"] = bool(result["quiet_hours_enabled"])
        return result

    async def upsert_qzone_policy(
        self,
        policy: QzoneSchedulePolicy,
        *,
        updated_by: str,
        now: datetime,
    ) -> None:
        await asyncio.to_thread(self._upsert_qzone_policy_sync, policy, updated_by, now)

    def _upsert_qzone_policy_sync(
        self,
        policy: QzoneSchedulePolicy,
        updated_by: str,
        now: datetime,
    ) -> None:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO qzone_schedule_policy(
                    singleton_id, timezone, quiet_hours_enabled, quiet_start,
                    quiet_end, quiet_behavior, daily_limit,
                    minimum_interval_seconds, updated_by, updated_at
                ) VALUES(1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    timezone = excluded.timezone,
                    quiet_hours_enabled = excluded.quiet_hours_enabled,
                    quiet_start = excluded.quiet_start,
                    quiet_end = excluded.quiet_end,
                    quiet_behavior = excluded.quiet_behavior,
                    daily_limit = excluded.daily_limit,
                    minimum_interval_seconds = excluded.minimum_interval_seconds,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    policy.timezone,
                    int(policy.quiet_hours_enabled),
                    policy.quiet_start,
                    policy.quiet_end,
                    policy.quiet_behavior.value,
                    policy.daily_limit,
                    policy.minimum_interval_seconds,
                    updated_by,
                    now_iso,
                ),
            )

    async def claim_due_qzone_post(
        self,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._claim_due_qzone_post_sync, now, lease_seconds)

    def _claim_due_qzone_post_sync(
        self,
        now: datetime,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        now_iso = now.astimezone(UTC).isoformat()
        lease_iso = (now + timedelta(seconds=lease_seconds)).astimezone(UTC).isoformat()
        ready = (
            QzonePostStatus.SCHEDULED.value,
            QzonePostStatus.WAITING_QUIET_HOURS.value,
            QzonePostStatus.WAITING_RATE_LIMIT.value,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE qzone_posts SET status = ?, updated_at = ?
                WHERE status = ? AND id IN (
                    SELECT post_id FROM qzone_post_runtime WHERE lease_expires_at <= ?
                )
                """,
                (
                    QzonePostStatus.SCHEDULED.value,
                    now_iso,
                    QzonePostStatus.EVALUATING.value,
                    now_iso,
                ),
            )
            connection.execute(
                """
                UPDATE qzone_post_runtime SET lease_expires_at = NULL,
                    hold_reason = 'recovered_expired_evaluation', updated_at = ?
                WHERE lease_expires_at <= ? AND post_id IN (
                    SELECT id FROM qzone_posts WHERE status = ?
                )
                """,
                (now_iso, now_iso, QzonePostStatus.SCHEDULED.value),
            )
            uncertain = connection.execute(
                """
                SELECT id FROM qzone_posts WHERE status = ? AND id IN (
                    SELECT post_id FROM qzone_post_runtime WHERE lease_expires_at <= ?
                )
                """,
                (QzonePostStatus.PUBLISHING.value, now_iso),
            ).fetchall()
            for item in uncertain:
                post_id = item["id"]
                connection.execute(
                    "UPDATE qzone_posts SET status = ?, updated_at = ? WHERE id = ?",
                    (QzonePostStatus.DELIVERY_UNCERTAIN.value, now_iso, post_id),
                )
                connection.execute(
                    """
                    UPDATE qzone_post_runtime SET lease_expires_at = NULL,
                        hold_reason = 'publish_interrupted', updated_at = ? WHERE post_id = ?
                    """,
                    (now_iso, post_id),
                )
                self._report(
                    connection,
                    post_id=post_id,
                    severity="critical",
                    title="QQ 空间发布结果不确定",
                    body=f"任务 {post_id} 在发布中断后无法确认是否成功，禁止自动重试。",
                    now_iso=now_iso,
                )
                self._event(
                    connection,
                    post_id=post_id,
                    event_type="delivery_uncertain",
                    reason="publish_interrupted",
                    details={},
                    occurred_at=now_iso,
                )
            row = connection.execute(
                """
                SELECT qzone_posts.*, qzone_post_runtime.original_scheduled_for,
                       qzone_post_runtime.timezone, qzone_post_runtime.missed_policy,
                       qzone_post_runtime.missed_grace_seconds,
                       qzone_post_runtime.next_eligible_at,
                       qzone_post_runtime.hold_reason,
                       qzone_post_runtime.first_evaluated_at,
                       qzone_post_runtime.owner_exception,
                       qzone_schedule_policy.timezone AS policy_timezone,
                       qzone_schedule_policy.quiet_hours_enabled,
                       qzone_schedule_policy.quiet_start,
                       qzone_schedule_policy.quiet_end,
                       qzone_schedule_policy.quiet_behavior,
                       qzone_schedule_policy.daily_limit,
                       qzone_schedule_policy.minimum_interval_seconds
                FROM qzone_posts
                JOIN qzone_post_runtime ON qzone_post_runtime.post_id = qzone_posts.id
                JOIN qzone_schedule_policy ON qzone_schedule_policy.singleton_id = 1
                WHERE qzone_posts.status IN (?, ?, ?)
                  AND qzone_post_runtime.next_eligible_at <= ?
                ORDER BY qzone_post_runtime.next_eligible_at ASC LIMIT 1
                """,
                (*ready, now_iso),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (QzonePostStatus.EVALUATING.value, now_iso, row["id"], row["status"]),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            connection.execute(
                """
                UPDATE qzone_post_runtime SET lease_expires_at = ?,
                    first_evaluated_at = COALESCE(first_evaluated_at, ?),
                    last_evaluated_at = ?, evaluation_attempts = evaluation_attempts + 1,
                    updated_at = ? WHERE post_id = ?
                """,
                (lease_iso, now_iso, now_iso, now_iso, row["id"]),
            )
            connection.commit()
        result = dict(row)
        result["images"] = json.loads(result.pop("images_json"))
        result["target_uins"] = json.loads(result.pop("target_uins_json"))
        result["quiet_hours_enabled"] = bool(result["quiet_hours_enabled"])
        result["owner_exception"] = bool(result["owner_exception"])
        result["was_previously_evaluated"] = row["first_evaluated_at"] is not None
        return result

    async def hold_qzone_post(
        self,
        post_id: str,
        *,
        status: QzonePostStatus,
        reason: str,
        next_eligible_at: datetime,
        now: datetime,
    ) -> bool:
        allowed = {
            QzonePostStatus.WAITING_QUIET_HOURS,
            QzonePostStatus.WAITING_RATE_LIMIT,
        }
        if status not in allowed:
            raise ValueError("unsupported qzone hold status")
        return await asyncio.to_thread(
            self._hold_qzone_post_sync,
            post_id,
            status,
            reason,
            next_eligible_at,
            now,
        )

    def _hold_qzone_post_sync(
        self,
        post_id: str,
        status: QzonePostStatus,
        reason: str,
        next_eligible_at: datetime,
        now: datetime,
    ) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT hold_reason FROM qzone_post_runtime WHERE post_id = ?",
                (post_id,),
            ).fetchone()
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    now_iso,
                    post_id,
                    QzonePostStatus.EVALUATING.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE qzone_post_runtime SET hold_reason = ?, next_eligible_at = ?,
                        lease_expires_at = NULL, updated_at = ? WHERE post_id = ?
                    """,
                    (
                        reason,
                        next_eligible_at.astimezone(UTC).isoformat(),
                        now_iso,
                        post_id,
                    ),
                )
                if current is None or current["hold_reason"] != reason:
                    self._event(
                        connection,
                        post_id=post_id,
                        event_type="held",
                        reason=reason,
                        details={"next_eligible_at": next_eligible_at.isoformat()},
                        occurred_at=now_iso,
                    )
            connection.commit()
        return updated.rowcount == 1

    async def qzone_delivery_window(
        self,
        *,
        day_start: datetime,
        day_end: datetime,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._qzone_delivery_window_sync, day_start, day_end)

    def _qzone_delivery_window_sync(
        self,
        day_start: datetime,
        day_end: datetime,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS published_count, MAX(published_at) AS last_published_at
                FROM qzone_posts WHERE status = ? AND published_at >= ? AND published_at < ?
                """,
                (
                    QzonePostStatus.PUBLISHED.value,
                    day_start.astimezone(UTC).isoformat(),
                    day_end.astimezone(UTC).isoformat(),
                ),
            ).fetchone()
        return dict(row)

    async def mark_qzone_missed(
        self,
        post_id: str,
        *,
        reason: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(self._mark_qzone_missed_sync, post_id, reason, now)

    def _mark_qzone_missed_sync(self, post_id: str, reason: str, now: datetime) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    QzonePostStatus.MISSED.value,
                    now_iso,
                    post_id,
                    QzonePostStatus.EVALUATING.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE qzone_post_runtime SET hold_reason = ?, lease_expires_at = NULL,
                        updated_at = ? WHERE post_id = ?
                    """,
                    (reason, now_iso, post_id),
                )
                self._report(
                    connection,
                    post_id=post_id,
                    severity="warning",
                    title="QQ 空间计划已错过",
                    body=f"空间任务 {post_id} 未自动补发，原因：{reason}。",
                    now_iso=now_iso,
                )
                self._event(
                    connection,
                    post_id=post_id,
                    event_type="missed",
                    reason=reason,
                    details={},
                    occurred_at=now_iso,
                )
            connection.commit()
        return updated.rowcount == 1

    async def require_qzone_reapproval(
        self,
        post_id: str,
        *,
        reason: str,
        approval_id: str,
        approval_code: str,
        requested_to: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._require_qzone_reapproval_sync,
            post_id,
            reason,
            approval_id,
            approval_code,
            requested_to,
            now,
        )

    def _require_qzone_reapproval_sync(
        self,
        post_id: str,
        reason: str,
        approval_id: str,
        approval_code: str,
        requested_to: str,
        now: datetime,
    ) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id, target_uins_json FROM qzone_posts
                WHERE id = ? AND status = ?
                """,
                (post_id, QzonePostStatus.EVALUATING.value),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            self._approval(
                connection,
                post_id=post_id,
                approval_id=approval_id,
                approval_code=approval_code,
                requested_to=requested_to,
                request_type="qzone.publish.reapprove",
                now=now,
                target_uins=tuple(json.loads(row["target_uins_json"] or "[]")),
            )
            connection.execute(
                "UPDATE qzone_posts SET status = ?, updated_at = ? WHERE id = ?",
                (QzonePostStatus.REAPPROVAL_REQUIRED.value, now_iso, post_id),
            )
            connection.execute(
                """
                UPDATE qzone_post_runtime SET approval_id = ?, hold_reason = ?,
                    lease_expires_at = NULL, updated_at = ? WHERE post_id = ?
                """,
                (approval_id, reason, now_iso, post_id),
            )
            self._report(
                connection,
                post_id=post_id,
                severity="action_required",
                title="QQ 空间任务需要重新审批",
                body=f"空间任务 {post_id} 因 {reason} 未发布；确认码 {approval_code}。",
                now_iso=now_iso,
            )
            self._event(
                connection,
                post_id=post_id,
                event_type="reapproval_required",
                reason=reason,
                details={"approval_id": approval_id},
                occurred_at=now_iso,
            )
            connection.commit()
        return True

    async def begin_qzone_publish(self, post_id: str, *, now: datetime) -> bool:
        return await asyncio.to_thread(self._begin_qzone_publish_sync, post_id, now)

    def _begin_qzone_publish_sync(self, post_id: str, now: datetime) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    QzonePostStatus.PUBLISHING.value,
                    now_iso,
                    post_id,
                    QzonePostStatus.EVALUATING.value,
                ),
            )
            if updated.rowcount == 1:
                self._event(
                    connection,
                    post_id=post_id,
                    event_type="publishing",
                    reason="network_call_started",
                    details={},
                    occurred_at=now_iso,
                )
            connection.commit()
        return updated.rowcount == 1

    async def complete_qzone_publish(
        self,
        post_id: str,
        *,
        qzone_tid: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(self._complete_qzone_publish_sync, post_id, qzone_tid, now)

    def _complete_qzone_publish_sync(
        self,
        post_id: str,
        qzone_tid: str,
        now: datetime,
    ) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, qzone_tid = ?, published_at = ?,
                    last_error = NULL, updated_at = ? WHERE id = ? AND status = ?
                """,
                (
                    QzonePostStatus.PUBLISHED.value,
                    qzone_tid,
                    now_iso,
                    now_iso,
                    post_id,
                    QzonePostStatus.PUBLISHING.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE qzone_post_runtime SET lease_expires_at = NULL,
                        owner_exception = 0, updated_at = ? WHERE post_id = ?
                    """,
                    (now_iso, post_id),
                )
                self._report(
                    connection,
                    post_id=post_id,
                    severity="info",
                    title="QQ 空间发布成功",
                    body=f"空间任务 {post_id} 已发布并保存平台 tid。",
                    now_iso=now_iso,
                )
                self._event(
                    connection,
                    post_id=post_id,
                    event_type="published",
                    reason="napcat_confirmed",
                    details={"tid_present": True},
                    occurred_at=now_iso,
                )
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        action, subject_type, subject_id, details_json, created_at
                    )
                    VALUES('qzone.published', 'qzone_post', ?, ?, ?)
                    """,
                    (post_id, _json({"tid_present": True}), now_iso),
                )
            connection.commit()
        return updated.rowcount == 1

    async def mark_qzone_publish_uncertain(
        self,
        post_id: str,
        *,
        error_type: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._mark_qzone_publish_uncertain_sync,
            post_id,
            error_type,
            now,
        )

    async def mark_qzone_evaluation_failed(
        self,
        post_id: str,
        *,
        error_type: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._mark_qzone_evaluation_failed_sync,
            post_id,
            error_type,
            now,
        )

    def _mark_qzone_evaluation_failed_sync(
        self,
        post_id: str,
        error_type: str,
        now: datetime,
    ) -> bool:
        """Fail safely before a network publish attempt has begun."""
        now_iso = now.astimezone(UTC).isoformat()
        safe_error = error_type[:200]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, last_error = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    QzonePostStatus.FAILED.value,
                    safe_error,
                    now_iso,
                    post_id,
                    QzonePostStatus.EVALUATING.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE qzone_post_runtime SET lease_expires_at = NULL,
                        hold_reason = 'evaluation_failed', updated_at = ?
                    WHERE post_id = ?
                    """,
                    (now_iso, post_id),
                )
                self._report(
                    connection,
                    post_id=post_id,
                    severity="warning",
                    title="QQ Zone task evaluation failed",
                    body=(
                        f"Qzone task {post_id} failed before publishing ({safe_error}); "
                        "no network publish was attempted."
                    ),
                    now_iso=now_iso,
                )
                self._event(
                    connection,
                    post_id=post_id,
                    event_type="evaluation_failed",
                    reason=safe_error,
                    details={"network_publish_attempted": False},
                    occurred_at=now_iso,
                )
            connection.commit()
        return updated.rowcount == 1

    async def mark_qzone_readiness_blocked(
        self,
        post_id: str,
        *,
        expected_status: QzonePostStatus,
        reason: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._mark_qzone_readiness_blocked_sync,
            post_id,
            expected_status,
            reason,
            now,
        )

    def _mark_qzone_readiness_blocked_sync(
        self,
        post_id: str,
        expected_status: QzonePostStatus,
        reason: str,
        now: datetime,
    ) -> bool:
        if expected_status not in {
            QzonePostStatus.EVALUATING,
            QzonePostStatus.DELETE_APPROVED,
        }:
            raise ValueError("readiness can only block a pre-network Qzone state")
        now_iso = now.astimezone(UTC).isoformat()
        safe_reason = reason[:200]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, last_error = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    QzonePostStatus.FAILED.value,
                    safe_reason,
                    now_iso,
                    post_id,
                    expected_status.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE qzone_post_runtime SET lease_expires_at = NULL,
                        hold_reason = 'readiness_blocked', updated_at = ?
                    WHERE post_id = ?
                    """,
                    (now_iso, post_id),
                )
                self._event(
                    connection,
                    post_id=post_id,
                    event_type="readiness_blocked",
                    reason=safe_reason,
                    details={"network_attempted": False, "automatic_retry": False},
                    occurred_at=now_iso,
                )
            connection.commit()
        return updated.rowcount == 1

    def _mark_qzone_publish_uncertain_sync(
        self,
        post_id: str,
        error_type: str,
        now: datetime,
    ) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        safe_error = error_type[:200]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, last_error = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    QzonePostStatus.DELIVERY_UNCERTAIN.value,
                    safe_error,
                    now_iso,
                    post_id,
                    QzonePostStatus.PUBLISHING.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE qzone_post_runtime SET lease_expires_at = NULL,
                        hold_reason = 'publish_result_uncertain', updated_at = ?
                    WHERE post_id = ?
                    """,
                    (now_iso, post_id),
                )
                self._report(
                    connection,
                    post_id=post_id,
                    severity="critical",
                    title="QQ 空间发布结果不确定",
                    body=(f"空间任务 {post_id} 返回 {safe_error}；禁止自动重试，请人工核查空间。"),
                    now_iso=now_iso,
                )
                self._event(
                    connection,
                    post_id=post_id,
                    event_type="delivery_uncertain",
                    reason=safe_error,
                    details={},
                    occurred_at=now_iso,
                )
            connection.commit()
        return updated.rowcount == 1

    async def request_qzone_delete(
        self,
        post_id: str,
        *,
        actor_qq: str,
        source: str,
        approval_id: str,
        approval_code: str,
        requested_to: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._request_qzone_delete_sync,
            post_id,
            actor_qq,
            source,
            approval_id,
            approval_code,
            requested_to,
            now,
        )

    def _request_qzone_delete_sync(
        self,
        post_id: str,
        actor_qq: str,
        source: str,
        approval_id: str,
        approval_code: str,
        requested_to: str,
        now: datetime,
    ) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT qzone_posts.status, qzone_posts.qzone_tid, qzone_posts.target_uins_json
                FROM qzone_posts WHERE id = ?
                """,
                (post_id,),
            ).fetchone()
            tid = str((row["qzone_tid"] if row else "") or "").strip()
            if row is None or row["status"] != QzonePostStatus.PUBLISHED.value or not tid:
                connection.rollback()
                return False
            target_uins = tuple(json.loads(row["target_uins_json"] or "[]"))
            updated = connection.execute(
                "UPDATE qzone_posts SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (
                    QzonePostStatus.PENDING_DELETE_APPROVAL.value,
                    now_iso,
                    post_id,
                    QzonePostStatus.PUBLISHED.value,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return False
            self._approval(
                connection,
                post_id=post_id,
                approval_id=approval_id,
                approval_code=approval_code,
                requested_to=requested_to,
                request_type="qzone.delete",
                now=now,
                target_uins=target_uins,
            )
            connection.execute(
                """
                UPDATE qzone_post_runtime
                SET approval_id = ?, hold_reason = 'pending_delete_approval', updated_at = ?
                WHERE post_id = ?
                """,
                (approval_id, now_iso, post_id),
            )
            self._report(
                connection,
                post_id=post_id,
                severity="action_required",
                title="QQ 空间撤销需要确认",
                body=f"空间任务 {post_id} 将删除平台 tid；确认码 {approval_code}。",
                now_iso=now_iso,
            )
            self._event(
                connection,
                post_id=post_id,
                event_type="delete_requested",
                reason=source,
                details={"actor_qq": actor_qq, "approval_id": approval_id, "tid_present": True},
                occurred_at=now_iso,
            )
            connection.execute(
                """
                INSERT INTO audit_log(
                    action, subject_type, subject_id, details_json, created_at
                )
                VALUES('qzone.delete_requested', 'qzone_post', ?, ?, ?)
                """,
                (post_id, _json({"actor_qq": actor_qq, "source": source}), now_iso),
            )
            connection.commit()
        return True

    async def approve_qzone_delete(
        self,
        post_id: str,
        *,
        actor_qq: str,
        approved_at: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._approve_qzone_delete_sync,
            post_id,
            actor_qq,
            approved_at,
        )

    def _approve_qzone_delete_sync(
        self,
        post_id: str,
        actor_qq: str,
        approved_at: datetime,
    ) -> bool:
        now_iso = approved_at.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    QzonePostStatus.DELETE_APPROVED.value,
                    now_iso,
                    post_id,
                    QzonePostStatus.PENDING_DELETE_APPROVAL.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE qzone_post_runtime
                    SET next_eligible_at = ?, hold_reason = NULL, updated_at = ?
                    WHERE post_id = ?
                    """,
                    (now_iso, now_iso, post_id),
                )
                connection.execute(
                    """
                    UPDATE owner_reports SET status = 'acknowledged', acknowledged_at = ?
                    WHERE related_type = 'qzone_post' AND related_id = ? AND status = 'pending'
                    """,
                    (now_iso, post_id),
                )
                self._event(
                    connection,
                    post_id=post_id,
                    event_type="delete_approved",
                    reason="owner_confirmed",
                    details={"actor_qq": actor_qq},
                    occurred_at=now_iso,
                )
            connection.commit()
        return updated.rowcount == 1

    async def revert_qzone_delete_request(
        self,
        post_id: str,
        *,
        actor_qq: str | None,
        reason: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._revert_qzone_delete_request_sync,
            post_id,
            actor_qq,
            reason,
            now,
        )

    def _revert_qzone_delete_request_sync(
        self,
        post_id: str,
        actor_qq: str | None,
        reason: str,
        now: datetime,
    ) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    QzonePostStatus.PUBLISHED.value,
                    now_iso,
                    post_id,
                    QzonePostStatus.PENDING_DELETE_APPROVAL.value,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return False
            connection.execute(
                """
                UPDATE qzone_post_runtime
                SET hold_reason = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE post_id = ?
                """,
                (now_iso, post_id),
            )
            connection.execute(
                """
                UPDATE approval_requests SET status = 'expired', decided_at = ?
                WHERE subject_id = ? AND request_type = 'qzone.delete' AND status = 'pending'
                """,
                (now_iso, post_id),
            )
            connection.execute(
                """
                UPDATE owner_reports SET status = 'acknowledged', acknowledged_at = ?
                WHERE related_type = 'qzone_post' AND related_id = ? AND status = 'pending'
                """,
                (now_iso, post_id),
            )
            self._event(
                connection,
                post_id=post_id,
                event_type="delete_aborted",
                reason=reason,
                details={"actor_qq": actor_qq},
                occurred_at=now_iso,
            )
            connection.commit()
        return True

    async def claim_due_qzone_delete(
        self,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._claim_due_qzone_delete_sync, now, lease_seconds)

    def _claim_due_qzone_delete_sync(
        self,
        now: datetime,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        now_iso = now.astimezone(UTC).isoformat()
        lease_iso = (now + timedelta(seconds=lease_seconds)).astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            interrupted = connection.execute(
                """
                SELECT id FROM qzone_posts WHERE status = ? AND id IN (
                    SELECT post_id FROM qzone_post_runtime WHERE lease_expires_at <= ?
                )
                """,
                (QzonePostStatus.DELETING.value, now_iso),
            ).fetchall()
            for item in interrupted:
                post_id = item["id"]
                connection.execute(
                    """
                    UPDATE qzone_posts
                    SET status = ?, last_error = ?, updated_at = ? WHERE id = ?
                    """,
                    (
                        QzonePostStatus.DELETE_UNCERTAIN.value,
                        "delete_interrupted",
                        now_iso,
                        post_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE qzone_post_runtime SET lease_expires_at = NULL,
                        hold_reason = 'delete_interrupted', updated_at = ? WHERE post_id = ?
                    """,
                    (now_iso, post_id),
                )
                self._report(
                    connection,
                    post_id=post_id,
                    severity="critical",
                    title="QQ 空间删除结果不确定",
                    body=f"任务 {post_id} 在删除中断后无法确认是否成功，禁止自动重试。",
                    now_iso=now_iso,
                )
                self._event(
                    connection,
                    post_id=post_id,
                    event_type="delete_uncertain",
                    reason="delete_interrupted",
                    details={},
                    occurred_at=now_iso,
                )
            row = connection.execute(
                """
                SELECT qzone_posts.*, qzone_post_runtime.next_eligible_at,
                       qzone_post_runtime.hold_reason,
                       qzone_post_runtime.lease_expires_at
                FROM qzone_posts
                JOIN qzone_post_runtime ON qzone_post_runtime.post_id = qzone_posts.id
                WHERE qzone_posts.status = ?
                  AND qzone_post_runtime.next_eligible_at <= ?
                  AND (
                      qzone_post_runtime.lease_expires_at IS NULL
                      OR qzone_post_runtime.lease_expires_at <= ?
                  )
                ORDER BY qzone_post_runtime.next_eligible_at ASC LIMIT 1
                """,
                (QzonePostStatus.DELETE_APPROVED.value, now_iso, now_iso),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE qzone_post_runtime SET lease_expires_at = ?,
                    last_evaluated_at = ?, evaluation_attempts = evaluation_attempts + 1,
                    updated_at = ? WHERE post_id = ?
                """,
                (lease_iso, now_iso, now_iso, row["id"]),
            )
            connection.commit()
        result = dict(row)
        result["images"] = json.loads(result.pop("images_json"))
        result["target_uins"] = json.loads(result.pop("target_uins_json"))
        return result

    async def begin_qzone_delete(self, post_id: str, *, now: datetime) -> bool:
        return await asyncio.to_thread(self._begin_qzone_delete_sync, post_id, now)

    def _begin_qzone_delete_sync(self, post_id: str, now: datetime) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    QzonePostStatus.DELETING.value,
                    now_iso,
                    post_id,
                    QzonePostStatus.DELETE_APPROVED.value,
                ),
            )
            if updated.rowcount == 1:
                self._event(
                    connection,
                    post_id=post_id,
                    event_type="deleting",
                    reason="network_call_started",
                    details={},
                    occurred_at=now_iso,
                )
            connection.commit()
        return updated.rowcount == 1

    async def complete_qzone_delete(self, post_id: str, *, now: datetime) -> bool:
        return await asyncio.to_thread(self._complete_qzone_delete_sync, post_id, now)

    def _complete_qzone_delete_sync(self, post_id: str, now: datetime) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, last_error = NULL, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    QzonePostStatus.DELETED.value,
                    now_iso,
                    post_id,
                    QzonePostStatus.DELETING.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE qzone_post_runtime SET lease_expires_at = NULL,
                        hold_reason = NULL, updated_at = ? WHERE post_id = ?
                    """,
                    (now_iso, post_id),
                )
                self._report(
                    connection,
                    post_id=post_id,
                    severity="info",
                    title="QQ 空间已撤销",
                    body=f"空间任务 {post_id} 已按 tid 删除平台说说。",
                    now_iso=now_iso,
                )
                self._event(
                    connection,
                    post_id=post_id,
                    event_type="deleted",
                    reason="napcat_confirmed",
                    details={"tid_present": True},
                    occurred_at=now_iso,
                )
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        action, subject_type, subject_id, details_json, created_at
                    )
                    VALUES('qzone.deleted', 'qzone_post', ?, ?, ?)
                    """,
                    (post_id, _json({"tid_present": True}), now_iso),
                )
            connection.commit()
        return updated.rowcount == 1

    async def mark_qzone_delete_uncertain(
        self,
        post_id: str,
        *,
        error_type: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._mark_qzone_delete_uncertain_sync,
            post_id,
            error_type,
            now,
        )

    def _mark_qzone_delete_uncertain_sync(
        self,
        post_id: str,
        error_type: str,
        now: datetime,
    ) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        safe_error = error_type[:200]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE qzone_posts SET status = ?, last_error = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    QzonePostStatus.DELETE_UNCERTAIN.value,
                    safe_error,
                    now_iso,
                    post_id,
                    QzonePostStatus.DELETING.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE qzone_post_runtime SET lease_expires_at = NULL,
                        hold_reason = 'delete_result_uncertain', updated_at = ?
                    WHERE post_id = ?
                    """,
                    (now_iso, post_id),
                )
                self._report(
                    connection,
                    post_id=post_id,
                    severity="critical",
                    title="QQ 空间删除结果不确定",
                    body=(f"空间任务 {post_id} 返回 {safe_error}；禁止自动重试，请人工核查空间。"),
                    now_iso=now_iso,
                )
                self._event(
                    connection,
                    post_id=post_id,
                    event_type="delete_uncertain",
                    reason=safe_error,
                    details={},
                    occurred_at=now_iso,
                )
            connection.commit()
        return updated.rowcount == 1

    async def qzone_summary(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._qzone_summary_sync)

    def _qzone_summary_sync(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM qzone_posts GROUP BY status"
            ).fetchall()
            next_row = connection.execute(
                """
                SELECT original_scheduled_for FROM qzone_post_runtime
                JOIN qzone_posts ON qzone_posts.id = qzone_post_runtime.post_id
                WHERE qzone_posts.status IN (?, ?, ?)
                ORDER BY original_scheduled_for ASC LIMIT 1
                """,
                (
                    QzonePostStatus.SCHEDULED.value,
                    QzonePostStatus.WAITING_QUIET_HOURS.value,
                    QzonePostStatus.WAITING_RATE_LIMIT.value,
                ),
            ).fetchone()
        return {
            "by_status": {row["status"]: row["count"] for row in rows},
            "next_scheduled_for": next_row[0] if next_row else None,
        }
