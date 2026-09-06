"""Stats and same-day chat-log queries."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class StatsRepositoryMixin:
    def _connect(self) -> sqlite3.Connection:  # pragma: no cover - supplied by repository
        raise NotImplementedError

    async def stats_message_counts(
        self,
        *,
        start_iso: str,
        end_iso: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._stats_message_counts_sync, start_iso, end_iso)

    def _stats_message_counts_sync(self, start_iso: str, end_iso: str) -> dict[str, Any]:
        with self._connect() as connection:
            private_users = connection.execute(
                """
                SELECT COUNT(DISTINCT conversations.peer_id)
                FROM messages
                JOIN conversations ON conversations.conversation_key = messages.conversation_key
                WHERE conversations.kind = 'private'
                  AND messages.occurred_at >= ? AND messages.occurred_at < ?
                """,
                (start_iso, end_iso),
            ).fetchone()[0]
            groups = connection.execute(
                """
                SELECT COUNT(DISTINCT conversations.peer_id)
                FROM messages
                JOIN conversations ON conversations.conversation_key = messages.conversation_key
                WHERE conversations.kind = 'group'
                  AND messages.occurred_at >= ? AND messages.occurred_at < ?
                """,
                (start_iso, end_iso),
            ).fetchone()[0]
            rounds = connection.execute(
                """
                SELECT COUNT(DISTINCT messages.conversation_key)
                FROM messages
                WHERE messages.occurred_at >= ? AND messages.occurred_at < ?
                """,
                (start_iso, end_iso),
            ).fetchone()[0]
            direction_rows = connection.execute(
                """
                SELECT direction, COUNT(*) AS messages
                FROM messages
                WHERE occurred_at >= ? AND occurred_at < ?
                GROUP BY direction
                """,
                (start_iso, end_iso),
            ).fetchall()
            image_rows = connection.execute(
                """
                SELECT messages.direction, COUNT(*) AS images
                FROM messages, json_each(messages.segments_json) AS segment
                WHERE messages.occurred_at >= ? AND messages.occurred_at < ?
                  AND json_extract(segment.value, '$.type') = 'image'
                GROUP BY messages.direction
                """,
                (start_iso, end_iso),
            ).fetchall()
        by_direction = {row["direction"]: int(row["messages"]) for row in direction_rows}
        images = {row["direction"]: int(row["images"]) for row in image_rows}
        return {
            "private_users": int(private_users),
            "groups": int(groups),
            "rounds": int(rounds),
            "user_messages": by_direction.get("inbound", 0),
            "bot_messages": by_direction.get("outbound", 0),
            "user_images": images.get("inbound", 0),
            "bot_images": images.get("outbound", 0),
        }

    async def stats_daily_messages(
        self,
        *,
        start_iso: str,
        end_iso: str,
        timezone_name: str,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._stats_daily_messages_sync,
            start_iso,
            end_iso,
            timezone_name,
        )

    def _stats_daily_messages_sync(
        self,
        start_iso: str,
        end_iso: str,
        timezone_name: str,
    ) -> list[dict[str, Any]]:
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            zone = UTC
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT messages.occurred_at, messages.direction
                FROM messages
                WHERE messages.occurred_at >= ? AND messages.occurred_at < ?
                """,
                (start_iso, end_iso),
            ).fetchall()
        by_day: dict[str, dict[str, int]] = {}
        for row in rows:
            occurred = datetime.fromisoformat(str(row["occurred_at"]))
            if occurred.tzinfo is None:
                occurred = occurred.replace(tzinfo=UTC)
            day = occurred.astimezone(zone).date().isoformat()
            bucket = by_day.setdefault(day, {"inbound": 0, "outbound": 0})
            direction = str(row["direction"])
            if direction in bucket:
                bucket[direction] += 1
        return [
            {"day": day, "inbound": values["inbound"], "outbound": values["outbound"]}
            for day, values in sorted(by_day.items())
        ]

    async def stats_token_leaders(
        self,
        *,
        start_iso: str,
        end_iso: str,
        limit: int = 12,
    ) -> dict[str, list[dict[str, Any]]]:
        return await asyncio.to_thread(self._stats_token_leaders_sync, start_iso, end_iso, limit)

    def _stats_token_leaders_sync(
        self,
        start_iso: str,
        end_iso: str,
        limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        capped = max(1, min(limit, 50))
        with self._connect() as connection:
            users = connection.execute(
                """
                SELECT conversations.peer_id AS peer_id,
                       COALESCE(SUM(COALESCE(inference_runs.input_tokens, 0)
                                    + COALESCE(inference_runs.output_tokens, 0)), 0) AS tokens
                FROM inference_runs
                JOIN conversations
                  ON conversations.conversation_key = inference_runs.conversation_key
                WHERE inference_runs.status = 'completed'
                  AND inference_runs.created_at >= ? AND inference_runs.created_at < ?
                  AND conversations.kind = 'private'
                GROUP BY conversations.peer_id
                ORDER BY tokens DESC
                LIMIT ?
                """,
                (start_iso, end_iso, capped),
            ).fetchall()
            groups = connection.execute(
                """
                SELECT conversations.peer_id AS peer_id,
                       COALESCE(SUM(COALESCE(inference_runs.input_tokens, 0)
                                    + COALESCE(inference_runs.output_tokens, 0)), 0) AS tokens
                FROM inference_runs
                JOIN conversations
                  ON conversations.conversation_key = inference_runs.conversation_key
                WHERE inference_runs.status = 'completed'
                  AND inference_runs.created_at >= ? AND inference_runs.created_at < ?
                  AND conversations.kind = 'group'
                GROUP BY conversations.peer_id
                ORDER BY tokens DESC
                LIMIT ?
                """,
                (start_iso, end_iso, capped),
            ).fetchall()
            total = connection.execute(
                """
                SELECT COALESCE(SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)), 0)
                FROM inference_runs
                WHERE status = 'completed'
                  AND created_at >= ? AND created_at < ?
                """,
                (start_iso, end_iso),
            ).fetchone()[0]
        return {
            "users": [{"peer_id": row["peer_id"], "tokens": int(row["tokens"])} for row in users],
            "groups": [{"peer_id": row["peer_id"], "tokens": int(row["tokens"])} for row in groups],
            "total_tokens": int(total),
        }

    async def chatlog_peers(self, *, start_iso: str, end_iso: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._chatlog_peers_sync, start_iso, end_iso)

    def _chatlog_peers_sync(self, start_iso: str, end_iso: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT conversations.kind,
                       conversations.peer_id,
                       COUNT(*) AS messages,
                       MAX(messages.occurred_at) AS last_at
                FROM messages
                JOIN conversations ON conversations.conversation_key = messages.conversation_key
                WHERE messages.occurred_at >= ? AND messages.occurred_at < ?
                GROUP BY conversations.kind, conversations.peer_id
                ORDER BY last_at DESC
                """,
                (start_iso, end_iso),
            ).fetchall()
        return [dict(row) for row in rows]

    async def chatlog_messages(
        self,
        *,
        kind: str,
        peer_id: str,
        start_iso: str,
        end_iso: str,
        after_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._chatlog_messages_sync,
            kind,
            peer_id,
            start_iso,
            end_iso,
            after_id,
            limit,
        )

    def _chatlog_messages_sync(
        self,
        kind: str,
        peer_id: str,
        start_iso: str,
        end_iso: str,
        after_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        capped = max(1, min(limit, 500))
        query = """
            SELECT messages.id, messages.direction, messages.sender_id, messages.bot_qq,
                   messages.occurred_at, messages.plain_text, messages.segments_json,
                   conversations.kind, conversations.peer_id
            FROM messages
            JOIN conversations ON conversations.conversation_key = messages.conversation_key
            WHERE conversations.kind = ?
              AND conversations.peer_id = ?
              AND messages.occurred_at >= ? AND messages.occurred_at < ?
        """
        parameters: list[Any] = [kind, peer_id, start_iso, end_iso]
        with self._connect() as connection:
            if after_id:
                anchor = connection.execute(
                    "SELECT occurred_at FROM messages WHERE id = ?",
                    (after_id,),
                ).fetchone()
                if anchor is None:
                    return []
                query += """
                  AND (
                    messages.occurred_at > ?
                    OR (messages.occurred_at = ? AND messages.id > ?)
                  )
                """
                parameters.extend([anchor["occurred_at"], anchor["occurred_at"], after_id])
            query += " ORDER BY messages.occurred_at ASC, messages.id ASC LIMIT ?"
            parameters.append(capped)
            rows = connection.execute(query, tuple(parameters)).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            try:
                payload["segments"] = json.loads(row["segments_json"])
            except json.JSONDecodeError:
                payload["segments"] = []
            del payload["segments_json"]
            items.append(payload)
        return items

    async def summary_conversation_events(
        self,
        *,
        start_iso: str,
        end_iso: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._summary_conversation_events_sync,
            start_iso,
            end_iso,
            limit,
        )

    def _summary_conversation_events_sync(
        self,
        start_iso: str,
        end_iso: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        capped = max(1, min(limit, 20))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT conversations.kind,
                       conversations.peer_id,
                       MIN(messages.occurred_at) AS started_at,
                       MAX(messages.occurred_at) AS ended_at,
                       SUM(
                           CASE WHEN messages.direction = 'inbound' THEN 1 ELSE 0 END
                       ) AS user_messages,
                       SUM(
                           CASE WHEN messages.direction = 'outbound' THEN 1 ELSE 0 END
                       ) AS bot_messages,
                       (
                           SELECT COALESCE(SUM(COALESCE(inference_runs.input_tokens, 0)
                                               + COALESCE(inference_runs.output_tokens, 0)), 0)
                           FROM inference_runs
                           WHERE inference_runs.conversation_key = conversations.conversation_key
                             AND inference_runs.status = 'completed'
                             AND inference_runs.created_at >= ? AND inference_runs.created_at < ?
                       ) AS tokens,
                       (
                           SELECT messages.plain_text
                           FROM messages
                           WHERE messages.conversation_key = conversations.conversation_key
                             AND messages.direction = 'inbound'
                             AND messages.occurred_at >= ? AND messages.occurred_at < ?
                           ORDER BY messages.occurred_at ASC
                           LIMIT 1
                       ) AS first_user_text
                FROM messages
                JOIN conversations ON conversations.conversation_key = messages.conversation_key
                WHERE messages.occurred_at >= ? AND messages.occurred_at < ?
                GROUP BY conversations.conversation_key
                ORDER BY started_at ASC
                LIMIT ?
                """,
                (start_iso, end_iso, start_iso, end_iso, start_iso, end_iso, capped),
            ).fetchall()
        return [dict(row) for row in rows]

    async def record_usage_run(
        self,
        *,
        run_id: str,
        source_message_id: str,
        conversation_key: str,
        actor_qq: str,
        bot_qq: str,
        mode: str,
        model_route: str,
        input_tokens: int | None,
        output_tokens: int | None,
        status: str = "completed",
    ) -> None:
        await asyncio.to_thread(
            self._record_usage_run_sync,
            run_id,
            source_message_id,
            conversation_key,
            actor_qq,
            bot_qq,
            mode,
            model_route,
            input_tokens,
            output_tokens,
            status,
        )

    def _record_usage_run_sync(
        self,
        run_id: str,
        source_message_id: str,
        conversation_key: str,
        actor_qq: str,
        bot_qq: str,
        mode: str,
        model_route: str,
        input_tokens: int | None,
        output_tokens: int | None,
        status: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        parts = conversation_key.split(":", 2)
        with self._connect() as connection:
            if len(parts) == 3:
                connection.execute(
                    """
                    INSERT INTO conversations(
                        conversation_key, kind, peer_id, created_at, last_activity_at
                    ) VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(conversation_key) DO NOTHING
                    """,
                    (conversation_key, parts[1], parts[2], now, now),
                )
            connection.execute(
                """
                INSERT INTO inference_runs(
                    id, source_message_id, conversation_key, actor_qq, bot_qq,
                    mode, status, prompt_hash, model_route, input_tokens, output_tokens,
                    created_at, completed_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    source_message_id,
                    conversation_key,
                    actor_qq,
                    bot_qq,
                    mode,
                    status,
                    model_route,
                    input_tokens,
                    output_tokens,
                    now,
                    now,
                ),
            )
