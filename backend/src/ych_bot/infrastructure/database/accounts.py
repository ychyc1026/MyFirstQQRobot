"""Account partition and chat quota persistence."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class AccountRepositoryMixin:
    def _connect(self) -> sqlite3.Connection:  # pragma: no cover - supplied by repository
        raise NotImplementedError

    async def ensure_account_seed(
        self,
        *,
        owner_qq: str,
        bot_qq: str,
        user_reply: str,
        private_default: int,
        group_default: int,
    ) -> None:
        await asyncio.to_thread(
            self._ensure_account_seed_sync,
            owner_qq,
            bot_qq,
            user_reply,
            private_default,
            group_default,
        )

    def _ensure_account_seed_sync(
        self,
        owner_qq: str,
        bot_qq: str,
        user_reply: str,
        private_default: int,
        group_default: int,
    ) -> None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT owner_qq FROM instance_owner WHERE singleton_id = 1"
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO instance_owner(singleton_id, owner_qq, updated_at, updated_by)
                    VALUES(1, ?, ?, ?)
                    """,
                    (owner_qq, now, "seed"),
                )
            bots = connection.execute("SELECT qq FROM bots LIMIT 1").fetchone()
            if bots is None:
                connection.execute(
                    """
                    INSERT INTO bots(
                        qq, label, enabled, quota_user_default, quota_group_default,
                        quota_user_reply, created_at, updated_at
                    ) VALUES(?, '', 1, ?, ?, ?, ?, ?)
                    """,
                    (bot_qq, private_default, group_default, user_reply, now, now),
                )
            connection.commit()

    async def current_owner_qq(self) -> str | None:
        return await asyncio.to_thread(self._current_owner_qq_sync)

    def _current_owner_qq_sync(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT owner_qq FROM instance_owner WHERE singleton_id = 1"
            ).fetchone()
        return None if row is None else str(row["owner_qq"])

    async def set_owner_qq(self, owner_qq: str, *, updated_by: str) -> None:
        await asyncio.to_thread(self._set_owner_qq_sync, owner_qq, updated_by)

    def _set_owner_qq_sync(self, owner_qq: str, updated_by: str) -> None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO instance_owner(singleton_id, owner_qq, updated_at, updated_by)
                VALUES(1, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    owner_qq = excluded.owner_qq,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (owner_qq, now, updated_by),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('account.owner_changed', 'owner', ?, ?, ?)
                """,
                (owner_qq, _json({"updated_by": updated_by}), now),
            )
            connection.commit()

    async def list_bots(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_bots_sync)

    def _list_bots_sync(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM bots ORDER BY created_at ASC").fetchall()
        return [_bot_row(row) for row in rows]

    async def bot_record(self, bot_qq: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._bot_record_sync, bot_qq)

    def _bot_record_sync(self, bot_qq: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM bots WHERE qq = ?", (bot_qq,)).fetchone()
        return None if row is None else _bot_row(row)

    async def enabled_bot_qqs(self) -> set[str]:
        bots = await self.list_bots()
        return {bot["qq"] for bot in bots if bot["enabled"]}

    async def add_bot(
        self,
        bot_qq: str,
        *,
        label: str,
        updated_by: str,
        user_reply: str,
        private_default: int,
        group_default: int,
    ) -> None:
        await asyncio.to_thread(
            self._add_bot_sync,
            bot_qq,
            label,
            updated_by,
            user_reply,
            private_default,
            group_default,
        )

    def _add_bot_sync(
        self,
        bot_qq: str,
        label: str,
        updated_by: str,
        user_reply: str,
        private_default: int,
        group_default: int,
    ) -> None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO bots(
                    qq, label, enabled, quota_user_default, quota_group_default,
                    quota_user_reply, created_at, updated_at
                ) VALUES(?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (bot_qq, label, private_default, group_default, user_reply, now, now),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('account.bot_added', 'bot', ?, ?, ?)
                """,
                (bot_qq, _json({"updated_by": updated_by, "label": label}), now),
            )

    async def set_bot_enabled(self, bot_qq: str, *, enabled: bool, updated_by: str) -> None:
        await asyncio.to_thread(self._set_bot_enabled_sync, bot_qq, enabled, updated_by)

    def _set_bot_enabled_sync(self, bot_qq: str, enabled: bool, updated_by: str) -> None:
        now = _utc_now()
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE bots SET enabled = ?, updated_at = ? WHERE qq = ?",
                (int(enabled), now, bot_qq),
            )
            if updated.rowcount == 0:
                raise ValueError("bot not found")
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('account.bot_enabled_changed', 'bot', ?, ?, ?)
                """,
                (bot_qq, _json({"enabled": enabled, "updated_by": updated_by}), now),
            )

    async def update_bot(
        self,
        bot_qq: str,
        *,
        updated_by: str,
        label: str | None,
        quota_user_default: int | None,
        quota_group_default: int | None,
        quota_user_reply: str | None,
    ) -> None:
        await asyncio.to_thread(
            self._update_bot_sync,
            bot_qq,
            updated_by,
            label,
            quota_user_default,
            quota_group_default,
            quota_user_reply,
        )

    def _update_bot_sync(
        self,
        bot_qq: str,
        updated_by: str,
        label: str | None,
        quota_user_default: int | None,
        quota_group_default: int | None,
        quota_user_reply: str | None,
    ) -> None:
        now = _utc_now()
        assignments: list[str] = ["updated_at = ?"]
        values: list[Any] = [now]
        if label is not None:
            assignments.append("label = ?")
            values.append(label)
        if quota_user_default is not None:
            assignments.append("quota_user_default = ?")
            values.append(quota_user_default)
        if quota_group_default is not None:
            assignments.append("quota_group_default = ?")
            values.append(quota_group_default)
        if quota_user_reply is not None:
            assignments.append("quota_user_reply = ?")
            values.append(quota_user_reply)
        values.append(bot_qq)
        with self._connect() as connection:
            updated = connection.execute(
                f"UPDATE bots SET {', '.join(assignments)} WHERE qq = ?",
                tuple(values),
            )
            if updated.rowcount == 0:
                raise ValueError("bot not found")
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('account.bot_updated', 'bot', ?, ?, ?)
                """,
                (bot_qq, _json({"updated_by": updated_by}), now),
            )

    async def inference_token_usage(
        self,
        *,
        bot_qq: str,
        conversation_key: str,
        start_iso: str,
        end_iso: str,
    ) -> int:
        return await asyncio.to_thread(
            self._inference_token_usage_sync,
            bot_qq,
            conversation_key,
            start_iso,
            end_iso,
        )

    def _inference_token_usage_sync(
        self,
        bot_qq: str,
        conversation_key: str,
        start_iso: str,
        end_iso: str,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)), 0)
                FROM inference_runs
                WHERE status = 'completed'
                  AND conversation_key = ?
                  AND created_at >= ? AND created_at < ?
                  AND bot_qq = ?
                """,
                (conversation_key, start_iso, end_iso, bot_qq),
            ).fetchone()
        return int(row[0])

    async def chat_quota_override(
        self, bot_qq: str, peer_kind: str, peer_id: str
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._chat_quota_override_sync, bot_qq, peer_kind, peer_id)

    def _chat_quota_override_sync(
        self, bot_qq: str, peer_kind: str, peer_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM chat_quota_overrides
                WHERE bot_qq = ? AND peer_kind = ? AND peer_id = ?
                """,
                (bot_qq, peer_kind, peer_id),
            ).fetchone()
        return None if row is None else dict(row)

    async def list_chat_quota_overrides(
        self, *, bot_qq: str, peer_kind: str | None = None
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_chat_quota_overrides_sync, bot_qq, peer_kind)

    def _list_chat_quota_overrides_sync(
        self, bot_qq: str, peer_kind: str | None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM chat_quota_overrides WHERE bot_qq = ?"
        params: tuple[Any, ...] = (bot_qq,)
        if peer_kind:
            query += " AND peer_kind = ?"
            params = (bot_qq, peer_kind)
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    async def upsert_chat_quota_override(
        self,
        *,
        bot_qq: str,
        peer_kind: str,
        peer_id: str,
        daily_limit: int,
        display_name: str | None,
        updated_by: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._upsert_chat_quota_override_sync,
            bot_qq,
            peer_kind,
            peer_id,
            daily_limit,
            display_name,
            updated_by,
        )

    def _upsert_chat_quota_override_sync(
        self,
        bot_qq: str,
        peer_kind: str,
        peer_id: str,
        daily_limit: int,
        display_name: str | None,
        updated_by: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM chat_quota_overrides
                WHERE bot_qq = ? AND peer_kind = ? AND peer_id = ?
                """,
                (bot_qq, peer_kind, peer_id),
            ).fetchone()
            name = (
                display_name
                if display_name is not None
                else (existing["display_name"] if existing else "")
            )
            bonus = existing["today_bonus"] if existing else 0
            bonus_day = existing["bonus_day"] if existing else ""
            connection.execute(
                """
                INSERT INTO chat_quota_overrides(
                    bot_qq, peer_kind, peer_id, daily_limit, today_bonus, bonus_day,
                    display_name, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_qq, peer_kind, peer_id) DO UPDATE SET
                    daily_limit = excluded.daily_limit,
                    display_name = excluded.display_name,
                    updated_at = excluded.updated_at
                """,
                (bot_qq, peer_kind, peer_id, daily_limit, bonus, bonus_day, name, now),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('quota.daily_limit_changed', ?, ?, ?, ?)
                """,
                (
                    peer_kind,
                    peer_id,
                    _json(
                        {
                            "bot_qq": bot_qq,
                            "daily_limit": daily_limit,
                            "updated_by": updated_by,
                        }
                    ),
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM chat_quota_overrides
                WHERE bot_qq = ? AND peer_kind = ? AND peer_id = ?
                """,
                (bot_qq, peer_kind, peer_id),
            ).fetchone()
        return dict(row)

    async def add_today_quota_bonus(
        self,
        *,
        bot_qq: str,
        peer_kind: str,
        peer_id: str,
        amount: int,
        day_key: str,
        updated_by: str,
        default_daily: int,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._add_today_quota_bonus_sync,
            bot_qq,
            peer_kind,
            peer_id,
            amount,
            day_key,
            updated_by,
            default_daily,
        )

    def _add_today_quota_bonus_sync(
        self,
        bot_qq: str,
        peer_kind: str,
        peer_id: str,
        amount: int,
        day_key: str,
        updated_by: str,
        default_daily: int,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM chat_quota_overrides
                WHERE bot_qq = ? AND peer_kind = ? AND peer_id = ?
                """,
                (bot_qq, peer_kind, peer_id),
            ).fetchone()
            daily_limit = int(existing["daily_limit"]) if existing else default_daily
            display_name = existing["display_name"] if existing else ""
            current_bonus = 0
            if existing and existing["bonus_day"] == day_key:
                current_bonus = int(existing["today_bonus"] or 0)
            connection.execute(
                """
                INSERT INTO chat_quota_overrides(
                    bot_qq, peer_kind, peer_id, daily_limit, today_bonus, bonus_day,
                    display_name, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_qq, peer_kind, peer_id) DO UPDATE SET
                    today_bonus = excluded.today_bonus,
                    bonus_day = excluded.bonus_day,
                    updated_at = excluded.updated_at
                """,
                (
                    bot_qq,
                    peer_kind,
                    peer_id,
                    daily_limit,
                    current_bonus + amount,
                    day_key,
                    display_name,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('quota.today_bonus_added', ?, ?, ?, ?)
                """,
                (
                    peer_kind,
                    peer_id,
                    _json({"bot_qq": bot_qq, "amount": amount, "updated_by": updated_by}),
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM chat_quota_overrides
                WHERE bot_qq = ? AND peer_kind = ? AND peer_id = ?
                """,
                (bot_qq, peer_kind, peer_id),
            ).fetchone()
        return dict(row)

    async def quota_notice(
        self, bot_qq: str, peer_kind: str, peer_id: str, day_key: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._quota_notice_sync, bot_qq, peer_kind, peer_id, day_key)

    def _quota_notice_sync(
        self, bot_qq: str, peer_kind: str, peer_id: str, day_key: str
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM chat_quota_notices
                WHERE bot_qq = ? AND peer_kind = ? AND peer_id = ? AND day_key = ?
                """,
                (bot_qq, peer_kind, peer_id, day_key),
            ).fetchone()
        if row is None:
            return {"owner_notified": False, "user_replied": False}
        return {
            "owner_notified": bool(row["owner_notified"]),
            "user_replied": bool(row["user_replied"]),
        }

    async def mark_quota_notice(
        self,
        bot_qq: str,
        peer_kind: str,
        peer_id: str,
        day_key: str,
        *,
        owner_notified: bool,
        user_replied: bool,
    ) -> None:
        await asyncio.to_thread(
            self._mark_quota_notice_sync,
            bot_qq,
            peer_kind,
            peer_id,
            day_key,
            owner_notified,
            user_replied,
        )

    def _mark_quota_notice_sync(
        self,
        bot_qq: str,
        peer_kind: str,
        peer_id: str,
        day_key: str,
        owner_notified: bool,
        user_replied: bool,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chat_quota_notices(
                    bot_qq, peer_kind, peer_id, day_key, owner_notified, user_replied
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_qq, peer_kind, peer_id, day_key) DO UPDATE SET
                    owner_notified = excluded.owner_notified,
                    user_replied = excluded.user_replied
                """,
                (bot_qq, peer_kind, peer_id, day_key, int(owner_notified), int(user_replied)),
            )

    async def clear_quota_notice(
        self, bot_qq: str, peer_kind: str, peer_id: str, day_key: str
    ) -> None:
        await asyncio.to_thread(self._clear_quota_notice_sync, bot_qq, peer_kind, peer_id, day_key)

    def _clear_quota_notice_sync(
        self, bot_qq: str, peer_kind: str, peer_id: str, day_key: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM chat_quota_notices
                WHERE bot_qq = ? AND peer_kind = ? AND peer_id = ? AND day_key = ?
                """,
                (bot_qq, peer_kind, peer_id, day_key),
            )


def _bot_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "qq": row["qq"],
        "label": row["label"],
        "enabled": bool(row["enabled"]),
        "quota_user_default": row["quota_user_default"],
        "quota_group_default": row["quota_group_default"],
        "quota_user_reply": row["quota_user_reply"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
