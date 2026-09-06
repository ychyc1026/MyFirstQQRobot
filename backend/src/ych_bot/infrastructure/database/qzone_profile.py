"""SQLite operations for authorized QQ Zone profile reads."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from ych_bot.domain.identity import PrivacyDataClass, QzoneProfileAccessMode
from ych_bot.domain.memory import MemorySource


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _default_policy(user_qq: str) -> dict[str, Any]:
    return {
        "user_qq": user_qq,
        "mode": QzoneProfileAccessMode.DENY.value,
        "max_items": None,
        "expires_at": None,
        "one_time_remaining": 0,
        "reason": "global_default",
        "source": "global_default",
    }


class QzoneProfileRepositoryMixin:
    """Mixed into ``SQLiteRepository`` for per-user QZone profile authorization."""

    def _connect(self) -> sqlite3.Connection:  # pragma: no cover - supplied by repository
        raise NotImplementedError

    async def set_qzone_profile_policy(
        self,
        *,
        user_qq: str,
        mode: QzoneProfileAccessMode,
        updated_by: str,
        reason: str,
        max_items: int | None = None,
        one_time_remaining: int = 0,
        expires_at: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._set_qzone_profile_policy_sync,
            user_qq,
            mode,
            updated_by,
            reason,
            max_items,
            one_time_remaining,
            expires_at,
        )

    def _set_qzone_profile_policy_sync(
        self,
        user_qq: str,
        mode: QzoneProfileAccessMode,
        updated_by: str,
        reason: str,
        max_items: int | None,
        one_time_remaining: int,
        expires_at: str | None,
    ) -> None:
        updated_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO qzone_profile_access_policies(
                    user_qq, mode, max_items, expires_at, one_time_remaining,
                    reason, updated_by, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_qq) DO UPDATE SET
                    mode = excluded.mode,
                    max_items = excluded.max_items,
                    expires_at = excluded.expires_at,
                    one_time_remaining = excluded.one_time_remaining,
                    reason = excluded.reason,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    user_qq,
                    mode.value,
                    max_items,
                    expires_at,
                    one_time_remaining,
                    reason,
                    updated_by,
                    updated_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('qzone_profile.policy_changed', 'user', ?, ?, ?)
                """,
                (user_qq, _json({"mode": mode.value, "updated_by": updated_by}), updated_at),
            )

    async def qzone_profile_policy(self, user_qq: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._qzone_profile_policy_sync, user_qq)

    def _qzone_profile_policy_sync(self, user_qq: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM qzone_profile_access_policies WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
        return dict(row) if row else _default_policy(user_qq)

    async def authorize_qzone_profile_access(
        self,
        *,
        user_qq: str,
        requested_count: int,
        purpose: str,
        accessor: str,
        collection_enabled: bool,
        now: datetime | None = None,
        consume: bool = True,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._authorize_qzone_profile_access_sync,
            user_qq,
            requested_count,
            purpose,
            accessor,
            collection_enabled,
            now or datetime.now(UTC),
            consume,
        )

    def _authorize_qzone_profile_access_sync(
        self,
        user_qq: str,
        requested_count: int,
        purpose: str,
        accessor: str,
        collection_enabled: bool,
        now: datetime,
        consume: bool,
    ) -> dict[str, Any]:
        now_iso = now.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            relationship = connection.execute(
                "SELECT data_frozen FROM user_relationships WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
            policy_row = connection.execute(
                "SELECT * FROM qzone_profile_access_policies WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
            policy = dict(policy_row) if policy_row else _default_policy(user_qq)

            allowed = True
            reason = "authorized"
            if relationship is not None and int(relationship["data_frozen"]) == 1:
                allowed, reason = False, "user_data_frozen"
            elif not collection_enabled:
                allowed, reason = False, "qzone_profile_collection_disabled"
            elif requested_count <= 0:
                allowed, reason = False, "invalid_item_count"
            elif policy["mode"] == QzoneProfileAccessMode.DENY.value:
                allowed, reason = False, "qzone_profile_mode_deny"
            elif policy["mode"] == QzoneProfileAccessMode.TTL.value:
                expires_at = policy.get("expires_at")
                if not expires_at or expires_at <= now_iso:
                    allowed, reason = False, "qzone_profile_authorization_expired"
            elif policy.get("max_items") and requested_count > int(policy["max_items"]):
                allowed, reason = False, "item_limit_exceeded"
            elif policy["mode"] == QzoneProfileAccessMode.ONE_TIME.value:
                remaining = int(policy.get("one_time_remaining") or 0)
                if remaining <= 0:
                    allowed, reason = False, "one_time_authorization_consumed"
                elif consume:
                    consumed = connection.execute(
                        """
                        UPDATE qzone_profile_access_policies
                        SET one_time_remaining = one_time_remaining - 1,
                            updated_at = ?
                        WHERE user_qq = ? AND one_time_remaining > 0
                        """,
                        (now_iso, user_qq),
                    )
                    if consumed.rowcount != 1:
                        allowed, reason = False, "one_time_authorization_consumed"

            if consume or not allowed:
                connection.execute(
                    """
                    INSERT INTO data_access_log(
                        user_qq, data_class, purpose, accessor,
                        decision, policy_snapshot_json, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_qq,
                        PrivacyDataClass.QZONE_CONTENT.value,
                        purpose,
                        accessor,
                        "allowed" if allowed else "denied",
                        _json(
                            {
                                "mode": policy["mode"],
                                "requested_count": requested_count,
                                "reason": reason,
                                "collection_enabled": collection_enabled,
                            }
                        ),
                        now_iso,
                    ),
                )
            connection.commit()
        return {
            "allowed": allowed,
            "reason": reason,
            "mode": policy["mode"],
            "max_items": policy.get("max_items"),
        }

    async def store_qzone_profile_snapshot(
        self,
        *,
        snapshot_id: str,
        user_qq: str,
        purpose: str,
        source: str,
        payload: dict[str, Any],
        fetched_at: str,
        expires_at: str,
        created_by: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._store_qzone_profile_snapshot_sync,
            snapshot_id,
            user_qq,
            purpose,
            source,
            payload,
            fetched_at,
            expires_at,
            created_by,
        )

    def _store_qzone_profile_snapshot_sync(
        self,
        snapshot_id: str,
        user_qq: str,
        purpose: str,
        source: str,
        payload: dict[str, Any],
        fetched_at: str,
        expires_at: str,
        created_by: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO qzone_profile_snapshots(
                    id, user_qq, purpose, source, payload_json,
                    fetched_at, expires_at, created_by
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    user_qq,
                    purpose,
                    source,
                    _json(payload),
                    fetched_at,
                    expires_at,
                    created_by,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('qzone_profile.snapshot_stored', 'user', ?, ?, ?)
                """,
                (
                    user_qq,
                    _json({"snapshot_id": snapshot_id, "purpose": purpose, "source": source}),
                    fetched_at,
                ),
            )
        return _snapshot_view(
            {
                "id": snapshot_id,
                "user_qq": user_qq,
                "purpose": purpose,
                "source": source,
                "payload_json": _json(payload),
                "fetched_at": fetched_at,
                "expires_at": expires_at,
                "created_by": created_by,
            }
        )

    async def latest_qzone_profile_snapshot(self, user_qq: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._latest_qzone_profile_snapshot_sync, user_qq)

    def _latest_qzone_profile_snapshot_sync(self, user_qq: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM qzone_profile_snapshots
                WHERE user_qq = ?
                ORDER BY fetched_at DESC LIMIT 1
                """,
                (user_qq,),
            ).fetchone()
        return _snapshot_view(dict(row)) if row else None

    async def qzone_profile_detail(self, user_qq: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._qzone_profile_detail_sync, user_qq)

    def _qzone_profile_detail_sync(self, user_qq: str) -> dict[str, Any]:
        with self._connect() as connection:
            policy_row = connection.execute(
                "SELECT * FROM qzone_profile_access_policies WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
            snapshot_row = connection.execute(
                """
                SELECT * FROM qzone_profile_snapshots
                WHERE user_qq = ?
                ORDER BY fetched_at DESC LIMIT 1
                """,
                (user_qq,),
            ).fetchone()
            memories = connection.execute(
                """
                SELECT * FROM memory_records
                WHERE user_qq = ? AND source_type = ?
                ORDER BY updated_at DESC
                """,
                (user_qq, MemorySource.QZONE_DERIVED.value),
            ).fetchall()
        return {
            "user_qq": user_qq,
            "policy": dict(policy_row) if policy_row else _default_policy(user_qq),
            "latest_snapshot": (
                _collection_card(_snapshot_view(dict(snapshot_row))) if snapshot_row else None
            ),
            "candidates": [
                {**dict(row), "value": json.loads(row["value_json"])} for row in memories
            ],
        }


def _snapshot_view(row: dict[str, Any]) -> dict[str, Any]:
    raw_payload = row["payload_json"]
    payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    return {
        "id": row["id"],
        "user_qq": row["user_qq"],
        "purpose": row["purpose"],
        "source": row["source"],
        "nickname": payload.get("nickname"),
        "signature": payload.get("signature"),
        "items": payload.get("items") or [],
        "summary": payload.get("summary") or {},
        "fetched_at": row["fetched_at"],
        "expires_at": row["expires_at"],
        "created_by": row.get("created_by"),
    }


def collection_card(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return _collection_card(snapshot)


def _collection_card(snapshot: dict[str, Any]) -> dict[str, Any]:
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    items = snapshot.get("items") if isinstance(snapshot.get("items"), list) else []
    return {
        "fetched_at": snapshot.get("fetched_at"),
        "expires_at": snapshot.get("expires_at"),
        "scanned": int(summary.get("scanned") or len(items)),
        "images_seen": int(summary.get("images_seen") or 0),
        "covers_seen": int(summary.get("covers_seen") or 0),
        "videos_unread": int(summary.get("videos_unread") or 0),
        "candidate_count": int(summary.get("candidate_count") or 0),
    }
