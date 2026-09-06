"""SQLite operations for owner-report dedupe and delivery."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ych_bot.domain.control import ReportSeverity
from ych_bot.domain.models import ConversationKind, MessageSegment, OutboundMessage, OutboxStatus
from ych_bot.domain.reports import (
    DEFAULT_OWNER_REPORT_POLICY,
    OwnerReportSchedulePolicy,
    ReportDeliveryPolicy,
    ReportDeliveryStatus,
    default_dedupe_key,
    next_digest_at,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _load_policy(connection: sqlite3.Connection) -> OwnerReportSchedulePolicy:
    row = connection.execute(
        "SELECT * FROM owner_report_delivery_policy WHERE singleton_id = 1"
    ).fetchone()
    if row is None:
        return DEFAULT_OWNER_REPORT_POLICY
    return OwnerReportSchedulePolicy(
        timezone=row["timezone"],
        digest_local_time=row["digest_local_time"],
        dedupe_window_seconds=int(row["dedupe_window_seconds"]),
        action_required_policy=ReportDeliveryPolicy(row["action_required_policy"]),
        critical_policy=ReportDeliveryPolicy(row["critical_policy"]),
        warning_policy=ReportDeliveryPolicy(row["warning_policy"]),
        info_policy=ReportDeliveryPolicy(row["info_policy"]),
    )


def _ensure_policy(
    connection: sqlite3.Connection,
    policy: OwnerReportSchedulePolicy,
    *,
    updated_by: str,
    now_iso: str,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO owner_report_delivery_policy(
            singleton_id, timezone, digest_local_time, dedupe_window_seconds,
            action_required_policy, critical_policy, warning_policy, info_policy,
            updated_by, updated_at
        ) VALUES(1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            policy.timezone,
            policy.digest_local_time,
            policy.dedupe_window_seconds,
            policy.action_required_policy.value,
            policy.critical_policy.value,
            policy.warning_policy.value,
            policy.info_policy.value,
            updated_by,
            now_iso,
        ),
    )


def _event(
    connection: sqlite3.Connection,
    *,
    report_id: str,
    event_type: str,
    reason: str | None,
    details: dict[str, Any],
    occurred_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO owner_report_events(
            id, report_id, event_type, reason, details_json, occurred_at
        ) VALUES(?, ?, ?, ?, ?, ?)
        """,
        (str(uuid4()), report_id, event_type, reason, _json(details), occurred_at),
    )


def record_owner_report_on_connection(
    connection: sqlite3.Connection,
    *,
    report_id: str,
    severity: str,
    category: str,
    title: str,
    body: str,
    related_type: str | None,
    related_id: str | None,
    now: datetime,
    dedupe_key: str | None = None,
    updated_by: str = "system",
) -> dict[str, Any]:
    now_utc = _as_utc(now)
    now_iso = now_utc.isoformat()
    _ensure_policy(
        connection,
        DEFAULT_OWNER_REPORT_POLICY,
        updated_by=updated_by,
        now_iso=now_iso,
    )
    policy = _load_policy(connection)
    key = dedupe_key or default_dedupe_key(
        category=category,
        related_type=related_type,
        related_id=related_id,
        severity=severity,
    )
    window_start = (now_utc - timedelta(seconds=policy.dedupe_window_seconds)).isoformat()
    existing = connection.execute(
        """
        SELECT owner_reports.id, owner_report_runtime.occurrence_count,
               owner_report_runtime.last_occurred_at
        FROM owner_report_runtime
        JOIN owner_reports ON owner_reports.id = owner_report_runtime.report_id
        WHERE owner_report_runtime.dedupe_key = ?
          AND owner_reports.status != 'acknowledged'
          AND owner_report_runtime.last_occurred_at >= ?
        ORDER BY owner_report_runtime.last_occurred_at DESC
        LIMIT 1
        """,
        (key, window_start),
    ).fetchone()
    if existing is not None:
        occurrence = int(existing["occurrence_count"]) + 1
        connection.execute(
            """
            UPDATE owner_report_runtime
            SET occurrence_count = ?, last_occurred_at = ?, updated_at = ?
            WHERE report_id = ?
            """,
            (occurrence, now_iso, now_iso, existing["id"]),
        )
        connection.execute(
            "UPDATE owner_reports SET body = ?, title = ? WHERE id = ?",
            (body, title, existing["id"]),
        )
        _event(
            connection,
            report_id=existing["id"],
            event_type="merged",
            reason="duplicate_within_window",
            details={"occurrence_count": occurrence},
            occurred_at=now_iso,
        )
        return {
            "id": existing["id"],
            "merged": True,
            "occurrence_count": occurrence,
        }

    delivery_policy = policy.policy_for(severity)
    if delivery_policy is ReportDeliveryPolicy.DIGEST:
        delivery_status = ReportDeliveryStatus.WAITING_DIGEST
        next_eligible = next_digest_at(
            now_utc,
            timezone_name=policy.timezone,
            digest_local_time=policy.digest_local_time,
        )
    else:
        delivery_status = ReportDeliveryStatus.PENDING
        next_eligible = now_utc
    connection.execute(
        """
        INSERT INTO owner_reports(
            id, severity, category, title, body,
            related_type, related_id, status, created_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            report_id,
            severity,
            category,
            title,
            body,
            related_type,
            related_id,
            now_iso,
        ),
    )
    connection.execute(
        """
        INSERT INTO owner_report_runtime(
            report_id, dedupe_key, delivery_policy, delivery_status,
            occurrence_count, first_occurred_at, last_occurred_at,
            next_eligible_at, updated_at
        ) VALUES(?, ?, ?, ?, 1, ?, ?, ?, ?)
        """,
        (
            report_id,
            key,
            delivery_policy.value,
            delivery_status.value,
            now_iso,
            now_iso,
            next_eligible.isoformat(),
            now_iso,
        ),
    )
    _event(
        connection,
        report_id=report_id,
        event_type="recorded",
        reason=delivery_policy.value,
        details={"severity": severity, "category": category},
        occurred_at=now_iso,
    )
    return {
        "id": report_id,
        "merged": False,
        "occurrence_count": 1,
        "delivery_policy": delivery_policy.value,
        "delivery_status": delivery_status.value,
        "next_eligible_at": next_eligible.isoformat(),
    }


class OwnerReportRepositoryMixin:
    def _connect(self) -> sqlite3.Connection:  # pragma: no cover - supplied by repository
        raise NotImplementedError

    async def record_owner_report(
        self,
        *,
        severity: ReportSeverity | str,
        category: str,
        title: str,
        body: str,
        related_type: str | None = None,
        related_id: str | None = None,
        dedupe_key: str | None = None,
        report_id: str | None = None,
        now: datetime | None = None,
        updated_by: str = "system",
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._record_owner_report_sync,
            severity,
            category,
            title,
            body,
            related_type,
            related_id,
            dedupe_key,
            report_id,
            now or datetime.now(UTC),
            updated_by,
        )

    async def record_dashboard_only_owner_report(
        self,
        *,
        severity: ReportSeverity | str,
        category: str,
        title: str,
        body: str,
        related_type: str,
        related_id: str,
        dedupe_key: str,
        now: datetime,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._record_dashboard_only_owner_report_sync,
            severity,
            category,
            title,
            body,
            related_type,
            related_id,
            dedupe_key,
            now,
        )

    def _record_dashboard_only_owner_report_sync(
        self,
        severity: ReportSeverity | str,
        category: str,
        title: str,
        body: str,
        related_type: str,
        related_id: str,
        dedupe_key: str,
        now: datetime,
    ) -> dict[str, Any]:
        severity_value = severity.value if isinstance(severity, ReportSeverity) else severity
        now_utc = _as_utc(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            recorded = record_owner_report_on_connection(
                connection,
                report_id=str(uuid4()),
                severity=severity_value,
                category=category,
                title=title,
                body=body,
                related_type=related_type,
                related_id=related_id,
                now=now_utc,
                dedupe_key=dedupe_key,
                updated_by="retention",
            )
            connection.execute(
                """
                UPDATE owner_report_runtime
                SET delivery_policy = ?, delivery_status = ?, lease_expires_at = NULL,
                    last_error = 'dashboard_only', updated_at = ?
                WHERE report_id = ?
                """,
                (
                    ReportDeliveryPolicy.DASHBOARD_ONLY.value,
                    ReportDeliveryStatus.SUPPRESSED.value,
                    now_utc.isoformat(),
                    recorded["id"],
                ),
            )
            _event(
                connection,
                report_id=recorded["id"],
                event_type="suppressed",
                reason="dashboard_only",
                details={"source": "managed_artifact_retention"},
                occurred_at=now_utc.isoformat(),
            )
            connection.commit()
        return self._owner_report_sync(recorded["id"]) | {
            "merged": recorded["merged"],
            "occurrence_count": recorded["occurrence_count"],
        }

    def _record_owner_report_sync(
        self,
        severity: ReportSeverity | str,
        category: str,
        title: str,
        body: str,
        related_type: str | None,
        related_id: str | None,
        dedupe_key: str | None,
        report_id: str | None,
        now: datetime,
        updated_by: str,
    ) -> dict[str, Any]:
        severity_value = severity.value if isinstance(severity, ReportSeverity) else severity
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            recorded = record_owner_report_on_connection(
                connection,
                report_id=report_id or str(uuid4()),
                severity=severity_value,
                category=category,
                title=title,
                body=body,
                related_type=related_type,
                related_id=related_id,
                now=now,
                dedupe_key=dedupe_key,
                updated_by=updated_by,
            )
            connection.commit()
        return self._owner_report_sync(recorded["id"]) | {
            "merged": recorded["merged"],
            "occurrence_count": recorded["occurrence_count"],
        }

    async def owner_report(self, report_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._owner_report_sync, report_id)

    def _owner_report_sync(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                self._report_select() + " WHERE owner_reports.id = ?",
                (report_id,),
            ).fetchone()
            if row is None:
                return None
            events = connection.execute(
                """
                SELECT * FROM owner_report_events
                WHERE report_id = ? ORDER BY occurred_at ASC
                """,
                (report_id,),
            ).fetchall()
        result = self._decode_report(row)
        result["events"] = [dict(item) for item in events]
        return result

    async def owner_report_queue(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._owner_report_queue_sync, limit)

    def _owner_report_queue_sync(self, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                self._report_select() + " ORDER BY owner_reports.created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._decode_report(row) for row in rows]

    @staticmethod
    def _report_select() -> str:
        return """
            SELECT owner_reports.*, owner_report_runtime.dedupe_key,
                   owner_report_runtime.delivery_policy,
                   owner_report_runtime.delivery_status,
                   owner_report_runtime.occurrence_count,
                   owner_report_runtime.first_occurred_at,
                   owner_report_runtime.last_occurred_at,
                   owner_report_runtime.next_eligible_at,
                   owner_report_runtime.outbox_id,
                   owner_report_runtime.delivery_attempts,
                   owner_report_runtime.last_error
            FROM owner_reports
            LEFT JOIN owner_report_runtime
              ON owner_report_runtime.report_id = owner_reports.id
        """

    @staticmethod
    def _decode_report(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        if result.get("occurrence_count") is None:
            result["occurrence_count"] = 1
        return result

    async def owner_report_policy(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._owner_report_policy_sync)

    def _owner_report_policy_sync(self) -> dict[str, Any]:
        with self._connect() as connection:
            _ensure_policy(
                connection,
                DEFAULT_OWNER_REPORT_POLICY,
                updated_by="system",
                now_iso=datetime.now(UTC).isoformat(),
            )
            row = connection.execute(
                "SELECT * FROM owner_report_delivery_policy WHERE singleton_id = 1"
            ).fetchone()
        return dict(row) if row is not None else {}

    async def upsert_owner_report_policy(
        self,
        policy: OwnerReportSchedulePolicy,
        *,
        updated_by: str,
        now: datetime,
    ) -> None:
        await asyncio.to_thread(self._upsert_owner_report_policy_sync, policy, updated_by, now)

    def _upsert_owner_report_policy_sync(
        self,
        policy: OwnerReportSchedulePolicy,
        updated_by: str,
        now: datetime,
    ) -> None:
        now_iso = _as_utc(now).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO owner_report_delivery_policy(
                    singleton_id, timezone, digest_local_time, dedupe_window_seconds,
                    action_required_policy, critical_policy, warning_policy, info_policy,
                    updated_by, updated_at
                ) VALUES(1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    timezone = excluded.timezone,
                    digest_local_time = excluded.digest_local_time,
                    dedupe_window_seconds = excluded.dedupe_window_seconds,
                    action_required_policy = excluded.action_required_policy,
                    critical_policy = excluded.critical_policy,
                    warning_policy = excluded.warning_policy,
                    info_policy = excluded.info_policy,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    policy.timezone,
                    policy.digest_local_time,
                    policy.dedupe_window_seconds,
                    policy.action_required_policy.value,
                    policy.critical_policy.value,
                    policy.warning_policy.value,
                    policy.info_policy.value,
                    updated_by,
                    now_iso,
                ),
            )

    async def claim_due_owner_report(
        self,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._claim_due_owner_report_sync, now, lease_seconds)

    def _claim_due_owner_report_sync(
        self,
        now: datetime,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        now_iso = _as_utc(now).isoformat()
        lease_iso = (_as_utc(now) + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT owner_reports.*, owner_report_runtime.dedupe_key,
                       owner_report_runtime.delivery_policy,
                       owner_report_runtime.delivery_status,
                       owner_report_runtime.occurrence_count,
                       owner_report_runtime.next_eligible_at,
                       owner_report_runtime.outbox_id,
                       owner_report_runtime.delivery_attempts,
                       outbox.status AS outbox_status,
                       outbox.attempts AS outbox_attempts
                FROM owner_report_runtime
                JOIN owner_reports ON owner_reports.id = owner_report_runtime.report_id
                LEFT JOIN outbox ON outbox.id = owner_report_runtime.outbox_id
                WHERE owner_reports.status != 'acknowledged'
                  AND (
                        (
                            owner_report_runtime.delivery_status IN (?, ?)
                            AND owner_report_runtime.next_eligible_at <= ?
                            AND (
                                owner_report_runtime.lease_expires_at IS NULL
                                OR owner_report_runtime.lease_expires_at <= ?
                            )
                        )
                        OR owner_report_runtime.delivery_status = ?
                  )
                ORDER BY owner_report_runtime.next_eligible_at ASC
                LIMIT 1
                """,
                (
                    ReportDeliveryStatus.PENDING.value,
                    ReportDeliveryStatus.WAITING_DIGEST.value,
                    now_iso,
                    now_iso,
                    ReportDeliveryStatus.QUEUED.value,
                ),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            connection.execute(
                """
                UPDATE owner_report_runtime
                SET lease_expires_at = ?, updated_at = ?
                WHERE report_id = ?
                """,
                (lease_iso, now_iso, row["id"]),
            )
            connection.commit()
        return self._decode_report(row)

    async def mark_owner_report_queued(
        self,
        report_id: str,
        *,
        outbox_id: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._mark_owner_report_queued_sync,
            report_id,
            outbox_id,
            now,
        )

    def _mark_owner_report_queued_sync(
        self,
        report_id: str,
        outbox_id: str,
        now: datetime,
    ) -> bool:
        now_iso = _as_utc(now).isoformat()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE owner_report_runtime
                SET delivery_status = ?, outbox_id = ?, delivery_attempts = delivery_attempts + 1,
                    lease_expires_at = NULL, last_error = NULL, updated_at = ?
                WHERE report_id = ? AND delivery_status IN (?, ?)
                """,
                (
                    ReportDeliveryStatus.QUEUED.value,
                    outbox_id,
                    now_iso,
                    report_id,
                    ReportDeliveryStatus.PENDING.value,
                    ReportDeliveryStatus.WAITING_DIGEST.value,
                ),
            )
            if updated.rowcount == 1:
                _event(
                    connection,
                    report_id=report_id,
                    event_type="queued",
                    reason="outbox",
                    details={"outbox_id": outbox_id},
                    occurred_at=now_iso,
                )
        return updated.rowcount == 1

    async def mark_owner_report_delivered(self, report_id: str, *, now: datetime) -> bool:
        return await asyncio.to_thread(self._mark_owner_report_delivered_sync, report_id, now)

    def _mark_owner_report_delivered_sync(self, report_id: str, now: datetime) -> bool:
        now_iso = _as_utc(now).isoformat()
        with self._connect() as connection:
            runtime = connection.execute(
                """
                UPDATE owner_report_runtime
                SET delivery_status = ?, lease_expires_at = NULL, updated_at = ?
                WHERE report_id = ? AND delivery_status = ?
                """,
                (
                    ReportDeliveryStatus.DELIVERED.value,
                    now_iso,
                    report_id,
                    ReportDeliveryStatus.QUEUED.value,
                ),
            )
            if runtime.rowcount != 1:
                return False
            connection.execute(
                """
                UPDATE owner_reports SET delivered_at = COALESCE(delivered_at, ?)
                WHERE id = ?
                """,
                (now_iso, report_id),
            )
            _event(
                connection,
                report_id=report_id,
                event_type="delivered",
                reason="outbox_sent",
                details={},
                occurred_at=now_iso,
            )
        return True

    async def mark_owner_report_failed(
        self,
        report_id: str,
        *,
        error: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._mark_owner_report_failed_sync,
            report_id,
            error,
            now,
        )

    def _mark_owner_report_failed_sync(
        self,
        report_id: str,
        error: str,
        now: datetime,
    ) -> bool:
        now_iso = _as_utc(now).isoformat()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE owner_report_runtime
                SET delivery_status = ?, last_error = ?, lease_expires_at = NULL, updated_at = ?
                WHERE report_id = ? AND delivery_status = ?
                """,
                (
                    ReportDeliveryStatus.FAILED.value,
                    error[:500],
                    now_iso,
                    report_id,
                    ReportDeliveryStatus.QUEUED.value,
                ),
            )
            if updated.rowcount == 1:
                _event(
                    connection,
                    report_id=report_id,
                    event_type="failed",
                    reason="no_automatic_retry",
                    details={"error": error[:200]},
                    occurred_at=now_iso,
                )
        return updated.rowcount == 1

    async def mark_owner_report_suppressed(
        self,
        report_id: str,
        *,
        reason: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._mark_owner_report_suppressed_sync,
            report_id,
            reason,
            now,
        )

    def _mark_owner_report_suppressed_sync(
        self,
        report_id: str,
        reason: str,
        now: datetime,
    ) -> bool:
        now_iso = _as_utc(now).isoformat()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE owner_report_runtime
                SET delivery_status = ?, last_error = ?, lease_expires_at = NULL, updated_at = ?
                WHERE report_id = ? AND delivery_status IN (?, ?)
                """,
                (
                    ReportDeliveryStatus.SUPPRESSED.value,
                    reason[:500],
                    now_iso,
                    report_id,
                    ReportDeliveryStatus.PENDING.value,
                    ReportDeliveryStatus.WAITING_DIGEST.value,
                ),
            )
            if updated.rowcount == 1:
                _event(
                    connection,
                    report_id=report_id,
                    event_type="suppressed",
                    reason=reason,
                    details={},
                    occurred_at=now_iso,
                )
        return updated.rowcount == 1

    async def enqueue_owner_report_outbox(
        self,
        *,
        report_id: str,
        owner_qq: str,
        title: str,
        body: str,
        occurrence_count: int,
        now: datetime,
    ) -> str | None:
        return await asyncio.to_thread(
            self._enqueue_owner_report_outbox_sync,
            report_id,
            owner_qq,
            title,
            body,
            occurrence_count,
            now,
        )

    def _enqueue_owner_report_outbox_sync(
        self,
        report_id: str,
        owner_qq: str,
        title: str,
        body: str,
        occurrence_count: int,
        now: datetime,
    ) -> str | None:
        outbox_id = str(uuid4())
        suffix = f"\n（合并 {occurrence_count} 次）" if occurrence_count > 1 else ""
        available_at = datetime.now(UTC)
        message = OutboundMessage(
            id=outbox_id,
            idempotency_key=f"owner_report:{report_id}",
            conversation_kind=ConversationKind.PRIVATE,
            target_id=owner_qq,
            segments=(
                MessageSegment(
                    type="text",
                    data={"text": f"[YCH 汇报] {title}\n{body}{suffix}"},
                ),
            ),
            created_at=available_at,
        )
        with self._connect() as connection:
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO outbox(
                    id, idempotency_key, conversation_kind, target_id,
                    segments_json, status, available_at, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.idempotency_key,
                    message.conversation_kind.value,
                    message.target_id,
                    _json([segment.as_onebot() for segment in message.segments]),
                    OutboxStatus.PENDING.value,
                    message.created_at.isoformat(),
                    message.created_at.isoformat(),
                ),
            )
            if inserted.rowcount != 1:
                existing = connection.execute(
                    "SELECT id FROM outbox WHERE idempotency_key = ?",
                    (message.idempotency_key,),
                ).fetchone()
                return None if existing is None else existing["id"]
        return outbox_id

    async def owner_report_summary(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._owner_report_summary_sync)

    def _owner_report_summary_sync(self) -> dict[str, Any]:
        with self._connect() as connection:
            status_rows = connection.execute(
                """
                SELECT COALESCE(owner_report_runtime.delivery_status, 'legacy') AS status,
                       COUNT(*) AS count
                FROM owner_reports
                LEFT JOIN owner_report_runtime
                  ON owner_report_runtime.report_id = owner_reports.id
                GROUP BY COALESCE(owner_report_runtime.delivery_status, 'legacy')
                """
            ).fetchall()
            pending = connection.execute(
                "SELECT COUNT(*) FROM owner_reports WHERE status = 'pending'"
            ).fetchone()[0]
        return {
            "pending_acknowledgements": int(pending),
            "by_delivery_status": {row["status"]: row["count"] for row in status_rows},
        }
