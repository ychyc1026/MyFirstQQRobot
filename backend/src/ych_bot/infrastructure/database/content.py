"""Diary, materials, daily summaries, and auto proactive enqueue."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from ych_bot.domain.proactive import ProactiveTaskStatus


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class ContentRepositoryMixin:
    def _connect(self) -> sqlite3.Connection:  # pragma: no cover - supplied by repository
        raise NotImplementedError

    async def diary_entries(self, *, limit: int = 60) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._diary_entries_sync, limit)

    def _diary_entries_sync(self, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM diary_entries
                ORDER BY day_key DESC, updated_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    async def diary_entry(self, day_key: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._diary_entry_sync, day_key)

    def _diary_entry_sync(self, day_key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM diary_entries WHERE day_key = ?",
                (day_key,),
            ).fetchone()
        return dict(row) if row else None

    async def upsert_diary_entry(
        self, *, day_key: str, content: str, now: datetime
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._upsert_diary_entry_sync, day_key, content, now)

    def _upsert_diary_entry_sync(self, day_key: str, content: str, now: datetime) -> dict[str, Any]:
        now_iso = now.astimezone(UTC).isoformat()
        entry_id = str(uuid5(NAMESPACE_URL, f"diary:{day_key}"))
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT status FROM diary_entries WHERE day_key = ?",
                (day_key,),
            ).fetchone()
            status = existing["status"] if existing and existing["status"] == "sent" else "pending"
            if not content.strip():
                status = "pending"
            connection.execute(
                """
                INSERT INTO diary_entries(id, day_key, content, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(day_key) DO UPDATE SET
                    content = excluded.content,
                    status = CASE
                        WHEN diary_entries.status = 'sent' THEN diary_entries.status
                        ELSE excluded.status
                    END,
                    updated_at = excluded.updated_at
                """,
                (entry_id, day_key, content, status, now_iso, now_iso),
            )
            row = connection.execute(
                "SELECT * FROM diary_entries WHERE day_key = ?",
                (day_key,),
            ).fetchone()
        return dict(row)

    async def mark_diary_status(self, day_key: str, status: str, *, now: datetime) -> None:
        await asyncio.to_thread(self._mark_diary_status_sync, day_key, status, now)

    def _mark_diary_status_sync(self, day_key: str, status: str, now: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE diary_entries SET status = ?, updated_at = ? WHERE day_key = ?",
                (status, now.astimezone(UTC).isoformat(), day_key),
            )

    async def expire_pending_diaries(self, *, before_day: str, now: datetime) -> int:
        return await asyncio.to_thread(self._expire_pending_diaries_sync, before_day, now)

    def _expire_pending_diaries_sync(self, before_day: str, now: datetime) -> int:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE diary_entries
                SET status = 'expired', updated_at = ?
                WHERE status = 'pending' AND day_key < ?
                """,
                (now.astimezone(UTC).isoformat(), before_day),
            )
        return updated.rowcount

    async def proactive_materials(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._proactive_materials_sync)

    def _proactive_materials_sync(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM proactive_materials ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    async def create_proactive_material(
        self,
        *,
        title: str,
        content: str,
        uploader_claim: str,
        file_name: str,
        created_by: str,
        now: datetime,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._create_proactive_material_sync,
            title,
            content,
            uploader_claim,
            file_name,
            created_by,
            now,
        )

    def _create_proactive_material_sync(
        self,
        title: str,
        content: str,
        uploader_claim: str,
        file_name: str,
        created_by: str,
        now: datetime,
    ) -> dict[str, Any]:
        now_iso = now.astimezone(UTC).isoformat()
        material_id = str(uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO proactive_materials(
                    id, title, content, file_name, uploader_claim, ai_verdict,
                    ai_reason, enabled, created_by, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, 'pending', '', 0, ?, ?, ?)
                """,
                (
                    material_id,
                    title,
                    content,
                    file_name,
                    uploader_claim,
                    created_by,
                    now_iso,
                    now_iso,
                ),
            )
            row = connection.execute(
                "SELECT * FROM proactive_materials WHERE id = ?",
                (material_id,),
            ).fetchone()
        return dict(row)

    async def update_material_verdict(
        self,
        material_id: str,
        *,
        verdict: str,
        reason: str,
        enabled: bool,
        now: datetime,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._update_material_verdict_sync,
            material_id,
            verdict,
            reason,
            enabled,
            now,
        )

    def _update_material_verdict_sync(
        self,
        material_id: str,
        verdict: str,
        reason: str,
        enabled: bool,
        now: datetime,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE proactive_materials
                SET ai_verdict = ?, ai_reason = ?, enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (verdict, reason, int(enabled), now.astimezone(UTC).isoformat(), material_id),
            )
            row = connection.execute(
                "SELECT * FROM proactive_materials WHERE id = ?",
                (material_id,),
            ).fetchone()
        return dict(row) if row else None

    async def set_material_enabled(self, material_id: str, *, enabled: bool, now: datetime) -> None:
        await asyncio.to_thread(self._set_material_enabled_sync, material_id, enabled, now)

    def _set_material_enabled_sync(self, material_id: str, enabled: bool, now: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE proactive_materials SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), now.astimezone(UTC).isoformat(), material_id),
            )

    async def enabled_materials(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._enabled_materials_sync)

    def _enabled_materials_sync(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM proactive_materials
                WHERE enabled = 1 AND ai_verdict = 'match'
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    async def daily_summary(self, day_key: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._daily_summary_sync, day_key)

    def _daily_summary_sync(self, day_key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM daily_summaries WHERE day_key = ?",
                (day_key,),
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["entries"] = json.loads(row["entries_json"])
        payload["source_peer_ids"] = json.loads(row["source_peer_ids_json"])
        return payload

    async def save_daily_summary(
        self,
        *,
        day_key: str,
        entries: list[dict[str, Any]],
        source_peer_ids: list[str],
        model_route: str,
        now: datetime,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._save_daily_summary_sync,
            day_key,
            entries,
            source_peer_ids,
            model_route,
            now,
        )

    def _save_daily_summary_sync(
        self,
        day_key: str,
        entries: list[dict[str, Any]],
        source_peer_ids: list[str],
        model_route: str,
        now: datetime,
    ) -> dict[str, Any]:
        now_iso = now.astimezone(UTC).isoformat()
        summary_id = str(uuid5(NAMESPACE_URL, f"daily-summary:{day_key}"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO daily_summaries(
                    id, day_key, entries_json, source_peer_ids_json,
                    model_route, generated_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(day_key) DO UPDATE SET
                    entries_json = excluded.entries_json,
                    source_peer_ids_json = excluded.source_peer_ids_json,
                    model_route = excluded.model_route,
                    generated_at = excluded.generated_at
                """,
                (
                    summary_id,
                    day_key,
                    _json(entries),
                    _json(sorted(set(source_peer_ids))),
                    model_route,
                    now_iso,
                ),
            )
        return {
            "id": summary_id,
            "day_key": day_key,
            "entries": entries,
            "source_peer_ids": sorted(set(source_peer_ids)),
            "model_route": model_route,
            "generated_at": now_iso,
        }

    async def enabled_proactive_policies(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._enabled_proactive_policies_sync)

    def _enabled_proactive_policies_sync(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM proactive_user_policies WHERE enabled = 1"
            ).fetchall()
        results = []
        for row in rows:
            payload = dict(row)
            payload["enabled"] = True
            payload["quiet_hours_enabled"] = bool(payload["quiet_hours_enabled"])
            payload["auto_content_enabled"] = bool(payload.get("auto_content_enabled", 0))
            payload["send_diary"] = bool(payload.get("send_diary", 0))
            results.append(payload)
        return results

    async def auto_content_sent_today(
        self,
        *,
        user_qq: str,
        start_iso: str,
        end_iso: str,
        content_source: str = "",
    ) -> bool:
        return await asyncio.to_thread(
            self._auto_content_sent_today_sync,
            user_qq,
            start_iso,
            end_iso,
            content_source,
        )

    def _auto_content_sent_today_sync(
        self,
        user_qq: str,
        start_iso: str,
        end_iso: str,
        content_source: str = "",
    ) -> bool:
        sources = (content_source,) if content_source else ("diary", "material", "context")
        placeholders = ", ".join("?" * len(sources))
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) FROM proactive_message_tasks
                WHERE target_qq = ?
                  AND content_source IN ({placeholders})
                  AND status IN (?, ?)
                  AND COALESCE(enqueued_at, sent_at, created_at) >= ?
                  AND COALESCE(enqueued_at, sent_at, created_at) < ?
                """,
                (
                    user_qq,
                    *sources,
                    ProactiveTaskStatus.ENQUEUED.value,
                    ProactiveTaskStatus.SENT.value,
                    start_iso,
                    end_iso,
                ),
            ).fetchone()
        return int(row[0]) > 0

    async def enqueue_auto_proactive_message(
        self,
        *,
        target_qq: str,
        content: str,
        content_source: str,
        timezone_name: str,
        created_by: str,
        now: datetime,
        day_key: str = "",
    ) -> str:
        return await asyncio.to_thread(
            self._enqueue_auto_proactive_message_sync,
            target_qq,
            content,
            content_source,
            timezone_name,
            created_by,
            now,
            day_key,
        )

    def _enqueue_auto_proactive_message_sync(
        self,
        target_qq: str,
        content: str,
        content_source: str,
        timezone_name: str,
        created_by: str,
        now: datetime,
        day_key: str = "",
    ) -> str:
        now_iso = now.astimezone(UTC).isoformat()
        task_id = str(uuid4())
        outbox_id = str(uuid5(NAMESPACE_URL, f"proactive-outbox:{task_id}"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO outbox(
                    id, idempotency_key, conversation_kind, target_id,
                    segments_json, status, available_at, created_at
                ) VALUES(?, ?, 'private', ?, ?, 'pending', ?, ?)
                """,
                (
                    outbox_id,
                    f"proactive:{task_id}",
                    target_qq,
                    _json([{"type": "text", "data": {"text": content}}]),
                    now_iso,
                    now_iso,
                ),
            )
            connection.execute(
                """
                INSERT INTO proactive_message_tasks(
                    id, target_qq, content, status, original_scheduled_for,
                    scheduled_for, timezone, missed_policy, missed_grace_seconds,
                    created_by, source, content_source, next_eligible_at,
                    outbox_id, enqueued_at, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'skip', 0, ?, 'auto', ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    target_qq,
                    content,
                    ProactiveTaskStatus.ENQUEUED.value,
                    now_iso,
                    now_iso,
                    timezone_name,
                    created_by,
                    content_source,
                    now_iso,
                    outbox_id,
                    now_iso,
                    now_iso,
                    now_iso,
                ),
            )
            connection.execute(
                """
                INSERT INTO proactive_delivery_events(
                    id, task_id, event_type, reason, details_json, occurred_at
                ) VALUES(?, ?, 'enqueued', 'auto_content', ?, ?)
                """,
                (
                    str(uuid4()),
                    task_id,
                    _json({"content_source": content_source, "day_key": day_key}),
                    now_iso,
                ),
            )
            connection.commit()
        return task_id
