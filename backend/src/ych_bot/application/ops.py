"""Read-only operations snapshot for the YCH dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ych_bot.infrastructure.database import SQLiteRepository

WORKER_ORDER = (
    ("knowledge", "资料"),
    ("proactive", "主动"),
    ("qzone", "空间"),
    ("owner_reports", "汇报"),
    ("outbox", "外发"),
    ("image_orphan", "图片巡检"),
)


@dataclass(frozen=True, slots=True)
class OpsFlags:
    timezone: str
    shadow_enabled: bool
    outbound_enabled: bool
    qzone_publish_enabled: bool


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _worker_state(snapshot: dict[str, Any] | None) -> str:
    if not snapshot or not snapshot.get("configured_enabled"):
        return "未启用"
    if snapshot.get("paused"):
        return "已暂停"
    return "运行中"


def _activity_href(source: str, subject_type: str = "") -> str:
    if source == "owner_report" or subject_type in {"owner_report", "approval"}:
        return "/approvals"
    if source == "control_command" or subject_type in {"control", "worker"}:
        return "/system"
    if subject_type in {"qzone_post", "qzone"}:
        return "/qzone"
    if subject_type in {"inference_run", "reply_candidate"}:
        return "/ops"
    return "/ops"


class OpsDashboardService:
    def __init__(self, repository: SQLiteRepository, *, flags: OpsFlags) -> None:
        self.repository = repository
        self.flags = flags
        self.timezone = ZoneInfo(flags.timezone)

    def _day_bounds(self, local_day: date) -> tuple[str, str]:
        start_local = datetime.combine(local_day, time.min, tzinfo=self.timezone)
        end_local = start_local + timedelta(days=1)
        return _utc_iso(start_local), _utc_iso(end_local)

    def _today(self) -> date:
        return datetime.now(self.timezone).date()

    async def snapshot(
        self,
        *,
        workers: dict[str, dict[str, Any]],
        protection: dict[str, Any],
        onebot: dict[str, Any] | None = None,
        mode: str = "observe_only",
    ) -> dict[str, Any]:
        today = self._today()
        start_iso, end_iso = self._day_bounds(today)
        current = await self.repository.ops_current_counts()
        window = await self.repository.ops_window_counts(start_iso, end_iso)
        chat_state = str((protection.get("chat") or {}).get("state") or "closed")
        image_state = str((protection.get("image") or {}).get("state") or "closed")
        circuit_open = int(chat_state == "open") + int(image_state == "open")
        anomalies_total = (
            current["qzone_uncertain"]
            + current["outbox_failed"]
            + current["report_delivery_failed"]
            + circuit_open
        )
        pending = current["pending_approvals"]
        urgent = current["urgent_reports"]
        return {
            "timezone": self.flags.timezone,
            "day": today.isoformat(),
            "mode": mode,
            "onebot": onebot
            or {
                "connected": False,
                "last_event_at": None,
            },
            "attention": {
                "pending_approvals": pending,
                "urgent_reports": urgent,
                "needs_owner": pending + urgent,
            },
            "inbound_today": window["inbound"],
            "anomalies": {
                "total": anomalies_total,
                "qzone_uncertain": current["qzone_uncertain"],
                "outbox_failed": current["outbox_failed"],
                "circuit_open": circuit_open,
                "report_delivery_failed": current["report_delivery_failed"],
            },
            "lanes": {
                "shadow": self._shadow_lane(window),
                "outbound": self._outbound_lane(current),
                "qzone": self._qzone_lane(current),
            },
            "workers": [
                {
                    "key": key,
                    "name": label,
                    "state": _worker_state(workers.get(key)),
                }
                for key, label in WORKER_ORDER
                if key in workers
            ],
        }

    def _shadow_lane(self, window: dict[str, int]) -> dict[str, Any]:
        if not self.flags.shadow_enabled:
            return {"state": "closed"}
        return {
            "state": "open",
            "completed": window["shadow_completed"],
            "failed": window["shadow_failed"],
            "skipped": window["shadow_skipped"],
        }

    def _outbound_lane(self, current: dict[str, int]) -> dict[str, Any]:
        if not self.flags.outbound_enabled:
            return {"state": "closed"}
        return {
            "state": "open",
            "pending": current["outbox_pending"],
            "failed": current["outbox_failed"],
        }

    def _qzone_lane(self, current: dict[str, int]) -> dict[str, Any]:
        if not self.flags.qzone_publish_enabled:
            return {"state": "closed"}
        return {
            "state": "open",
            "publishing": current["qzone_publishing"],
            "uncertain": current["qzone_uncertain"],
            "pending_delete": current["qzone_pending_delete"],
        }

    async def series(self, days: int) -> dict[str, Any]:
        if days not in {1, 7, 30}:
            raise ValueError("days must be 1, 7 or 30")
        today = self._today()
        points: list[dict[str, Any]] = []
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            start_iso, end_iso = self._day_bounds(day)
            window = await self.repository.ops_window_counts(start_iso, end_iso)
            points.append(
                {
                    "day": day.isoformat(),
                    "inbound": window["inbound"],
                    "commands": window["commands"],
                    "approvals": window["approvals"],
                }
            )
        return {
            "timezone": self.flags.timezone,
            "days": days,
            "points": points,
        }

    async def activity(self, limit: int = 8) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 50))
        rows = await self.repository.ops_activity_rows(safe_limit)
        items = [
            {
                "source": row["source"],
                "at": row["at"],
                "title": row["title"],
                "detail": row["detail"],
                "href": _activity_href(row["source"], row.get("subject_type", "")),
            }
            for row in rows
        ]
        return {"items": items, "limit": safe_limit}
