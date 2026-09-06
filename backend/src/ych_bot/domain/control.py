"""Owner-only commands and shared control-plane state."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from uuid import NAMESPACE_URL, uuid5

from ych_bot.domain.models import ConversationKind, UnifiedMessage


class OwnerCommandKind(StrEnum):
    HELP = "help"
    STATUS = "status"
    QZONE_QUEUE = "qzone.queue"
    QZONE_DRAFT = "qzone.draft"
    QZONE_PUBLISH = "qzone.publish"
    QZONE_SCHEDULE = "qzone.schedule"
    QZONE_CANCEL = "qzone.cancel"
    QZONE_REVOKE = "qzone.revoke"
    QZONE_PAUSE = "qzone.pause"
    QZONE_RESUME = "qzone.resume"
    APPROVE = "approval.approve"
    REJECT = "approval.reject"
    REPORT_ACK = "report.ack"
    REPORT_QUEUE = "report.queue"
    REPORT_STATUS = "report.status"
    REPORT_WORKER_STATUS = "report.worker_status"
    REPORT_WORKER_PAUSE = "report.worker_pause"
    REPORT_WORKER_RESUME = "report.worker_resume"
    USER_STATUS = "user.status"
    USER_SET_LABEL = "user.set_label"
    GROUP_SET_LABEL = "group.set_label"
    USER_MARK_EXISTING_FRIEND = "user.mark_existing_friend"
    USER_MARK_NEW_FRIEND = "user.mark_new_friend"
    USER_MARK_NOT_FRIEND = "user.mark_not_friend"
    USER_RESTORE_EVIDENCE = "user.restore_evidence"
    USER_HISTORY_DENY = "user.history_deny"
    USER_HISTORY_FILE_ONLY = "user.history_file_only"
    USER_HISTORY_SELECTED_RANGE = "user.history_selected_range"
    USER_HISTORY_ONE_TIME = "user.history_one_time"
    USER_HISTORY_PULL = "user.history_pull"
    USER_QZONE_PROFILE_STATUS = "user.qzone_profile_status"
    USER_QZONE_PROFILE_DENY = "user.qzone_profile_deny"
    USER_QZONE_PROFILE_ONE_TIME = "user.qzone_profile_one_time"
    USER_QZONE_PROFILE_TTL = "user.qzone_profile_ttl"
    USER_QZONE_PROFILE_PREVIEW = "user.qzone_profile_preview"
    PRIVACY_STATUS = "privacy.status"
    PRIVACY_FREEZE = "privacy.freeze"
    PRIVACY_UNFREEZE = "privacy.unfreeze"
    PRIVACY_EXPORT = "privacy.export"
    PRIVACY_DELETE = "privacy.delete"
    FRIEND_BASELINE_STATUS = "friend_baseline.status"
    FRIEND_BASELINE_PREVIEW = "friend_baseline.preview"
    IMAGE_CREATE = "image.create"
    IMAGE_GENERATE = "image.generate"
    IMAGE_STATUS = "image.status"
    IMAGE_RENEW = "image.renew"
    IMAGE_ORPHAN_SCAN = "image.orphan_scan"
    IMAGE_ORPHAN_WORKER_STATUS = "image.orphan_worker_status"
    IMAGE_ORPHAN_WORKER_PAUSE = "image.orphan_worker_pause"
    IMAGE_ORPHAN_WORKER_RESUME = "image.orphan_worker_resume"
    SHADOW_STATUS = "shadow.status"
    SHADOW_RECENT = "shadow.recent"
    SHADOW_DETAIL = "shadow.detail"
    SHADOW_REPLAY = "shadow.replay"
    MODEL_STATUS = "model.status"
    KNOWLEDGE_PROCESS = "knowledge.process"
    KNOWLEDGE_STATUS = "knowledge.status"
    KNOWLEDGE_WORKER_STATUS = "knowledge.worker_status"
    KNOWLEDGE_WORKER_PAUSE = "knowledge.worker_pause"
    KNOWLEDGE_WORKER_RESUME = "knowledge.worker_resume"
    PROACTIVE_CREATE = "proactive.create"
    PROACTIVE_STATUS = "proactive.status"
    PROACTIVE_CANCEL = "proactive.cancel"
    PROACTIVE_USER_STATUS = "proactive.user_status"
    PROACTIVE_USER_ENABLE = "proactive.user_enable"
    PROACTIVE_USER_DISABLE = "proactive.user_disable"
    PROACTIVE_AUTO_CONTENT_ENABLE = "proactive.auto_content_enable"
    PROACTIVE_AUTO_CONTENT_DISABLE = "proactive.auto_content_disable"
    PROACTIVE_DIARY_ENABLE = "proactive.diary_enable"
    PROACTIVE_DIARY_DISABLE = "proactive.diary_disable"
    PROACTIVE_WORKER_STATUS = "proactive.worker_status"
    PROACTIVE_WORKER_PAUSE = "proactive.worker_pause"
    PROACTIVE_WORKER_RESUME = "proactive.worker_resume"
    OUTBOUND_STATUS = "outbound.status"
    OUTBOUND_PAUSE = "outbound.pause"
    OUTBOUND_RESUME = "outbound.resume"
    QUOTA_STATUS = "quota.status"
    QUOTA_TODAY_BONUS = "quota.today_bonus"
    QUOTA_USER_LIMIT = "quota.user_limit"
    QUOTA_GROUP_LIMIT = "quota.group_limit"


class ControlSource(StrEnum):
    OWNER_QQ = "owner_qq"
    DASHBOARD = "dashboard"


class QzonePostStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    EVALUATING = "evaluating"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    WAITING_QUIET_HOURS = "waiting_quiet_hours"
    WAITING_RATE_LIMIT = "waiting_rate_limit"
    REAPPROVAL_REQUIRED = "reapproval_required"
    MISSED = "missed"
    REJECTED = "rejected"
    DELIVERY_UNCERTAIN = "delivery_uncertain"
    APPROVAL_EXPIRED = "approval_expired"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PENDING_DELETE_APPROVAL = "pending_delete_approval"
    DELETE_APPROVED = "delete_approved"
    DELETING = "deleting"
    DELETED = "deleted"
    DELETE_UNCERTAIN = "delete_uncertain"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ReportSeverity(StrEnum):
    INFO = "info"
    ACTION_REQUIRED = "action_required"
    WARNING = "warning"
    CRITICAL = "critical"


class QzoneVisibility(IntEnum):
    EVERYONE = 1
    FRIENDS = 4
    SELECTED_FRIENDS = 16
    PRIVATE = 64
    EXCLUDED_FRIENDS = 128


@dataclass(frozen=True, slots=True)
class OwnerCommand:
    id: str
    kind: OwnerCommandKind
    actor_qq: str
    source_message_id: str
    arguments: dict[str, str]
    source: ControlSource = ControlSource.OWNER_QQ


_SCHEDULE_PATTERN = re.compile(
    r"^/空间\s+定时\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+(.+)$",
    re.DOTALL,
)
_USER_STATUS_PATTERN = re.compile(r"^/用户\s+(\d+)\s+状态$")
_USER_LABEL_PATTERN = re.compile(r"^/用户\s+(\d+)\s+备注(?:\s+(.+))?$")
_GROUP_LABEL_PATTERN = re.compile(r"^/群\s+(\d+)\s+备注(?:\s+(.+))?$")
_USER_MARK_PATTERN = re.compile(r"^/用户\s+(\d+)\s+标记(旧友|新友|非好友)$")
_USER_RESTORE_EVIDENCE_PATTERN = re.compile(r"^/用户\s+(\d+)\s+恢复自动判定$")
_USER_HISTORY_SIMPLE_PATTERN = re.compile(r"^/用户\s+(\d+)\s+历史\s+(禁止|仅文件)$")
_USER_HISTORY_RANGE_PATTERN = re.compile(
    r"^/用户\s+(\d+)\s+历史\s+指定范围\s+"
    r"(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+(\d+)$"
)
_USER_HISTORY_ONCE_PATTERN = re.compile(r"^/用户\s+(\d+)\s+历史\s+一次授权\s+(\d+)$")
_USER_HISTORY_PULL_PATTERN = re.compile(r"^/用户\s+(\d+)\s+历史\s+拉取$")
_USER_QZONE_PROFILE_STATUS_PATTERN = re.compile(r"^/用户\s+(\d+)\s+空间资料\s+状态$")
_USER_QZONE_PROFILE_DENY_PATTERN = re.compile(r"^/用户\s+(\d+)\s+空间资料\s+禁止$")
_USER_QZONE_PROFILE_ONCE_PATTERN = re.compile(r"^/用户\s+(\d+)\s+空间资料\s+一次授权\s+(\d+)$")
_USER_QZONE_PROFILE_TTL_PATTERN = re.compile(r"^/用户\s+(\d+)\s+空间资料\s+限期\s+(\d+)\s+(\d+)$")
_USER_QZONE_PROFILE_PREVIEW_PATTERN = re.compile(r"^/用户\s+(\d+)\s+空间资料\s+预览$")
_PRIVACY_USER_PATTERN = re.compile(r"^/隐私\s+(冻结|解冻|导出|删除)\s+(\d+)$")
_IMAGE_TASK_PATTERN = re.compile(r"^/图片\s+(生成|状态|续期)\s+([0-9a-fA-F-]+)$")
_SHADOW_DETAIL_PATTERN = re.compile(r"^/影子\s+查看\s+([0-9a-fA-F-]+)$")
_SHADOW_REPLAY_PATTERN = re.compile(r"^/影子\s+重跑\s+(\S+)$")
_KNOWLEDGE_TASK_PATTERN = re.compile(r"^/资料\s+(处理|状态)\s+([0-9a-fA-F-]+)$")
_PROACTIVE_CREATE_PATTERN = re.compile(
    r"^/主动\s+创建\s+(\d+)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+(.+)$",
    re.DOTALL,
)
_PROACTIVE_TASK_PATTERN = re.compile(r"^/主动\s+(状态|取消)\s+([0-9a-fA-F-]+)$")
_PROACTIVE_USER_PATTERN = re.compile(r"^/主动\s+用户\s+(\d+)\s+(状态|开启|关闭)$")
_PROACTIVE_CONTENT_PATTERN = re.compile(r"^/主动\s+(内容|日记)\s+(\d+)\s+(开启|关闭)$")
_REPORT_STATUS_PATTERN = re.compile(r"^/汇报\s+状态\s+([0-9a-fA-F-]+)$")
_QUOTA_TODAY_PATTERN = re.compile(r"^/额度\s+今天\s+(\d+)\s+(\d+)$")
_QUOTA_USER_LIMIT_PATTERN = re.compile(r"^/额度\s+用户\s+(\d+)\s+(\d+)$")
_QUOTA_GROUP_LIMIT_PATTERN = re.compile(r"^/额度\s+群\s+(\d+)\s+(\d+)$")


def parse_owner_command(message: UnifiedMessage, *, owner_qq: str) -> OwnerCommand | None:
    if message.conversation_kind is not ConversationKind.PRIVATE:
        return None
    if message.sender_id != owner_qq:
        return None

    text = message.plain_text.strip()
    if not text.startswith("/"):
        return None

    kind: OwnerCommandKind | None = None
    arguments: dict[str, str] = {}

    if text in {"/帮助", "/help"}:
        kind = OwnerCommandKind.HELP
    elif text == "/状态":
        kind = OwnerCommandKind.STATUS
    elif text == "/空间 队列":
        kind = OwnerCommandKind.QZONE_QUEUE
    elif text == "/空间 暂停":
        kind = OwnerCommandKind.QZONE_PAUSE
    elif text == "/空间 恢复":
        kind = OwnerCommandKind.QZONE_RESUME
    elif text.startswith("/空间 草稿 "):
        kind = OwnerCommandKind.QZONE_DRAFT
        arguments["content"] = text.removeprefix("/空间 草稿 ").strip()
    elif text.startswith("/空间 发布 "):
        kind = OwnerCommandKind.QZONE_PUBLISH
        arguments["content"] = text.removeprefix("/空间 发布 ").strip()
    elif match := _SCHEDULE_PATTERN.match(text):
        kind = OwnerCommandKind.QZONE_SCHEDULE
        arguments = {"scheduled_for": match.group(1), "content": match.group(2).strip()}
    elif text.startswith("/空间 取消 "):
        kind = OwnerCommandKind.QZONE_CANCEL
        arguments["post_id"] = text.removeprefix("/空间 取消 ").strip()
    elif text.startswith("/空间 撤销 "):
        kind = OwnerCommandKind.QZONE_REVOKE
        arguments["post_id"] = text.removeprefix("/空间 撤销 ").strip()
    elif text.startswith("/确认 "):
        kind = OwnerCommandKind.APPROVE
        arguments["approval_code"] = text.removeprefix("/确认 ").strip()
    elif text.startswith("/拒绝 "):
        kind = OwnerCommandKind.REJECT
        arguments["approval_code"] = text.removeprefix("/拒绝 ").strip()
    elif text == "/汇报 队列":
        kind = OwnerCommandKind.REPORT_QUEUE
    elif match := _REPORT_STATUS_PATTERN.match(text):
        kind = OwnerCommandKind.REPORT_STATUS
        arguments["report_id"] = match.group(1)
    elif text == "/汇报 自动 状态":
        kind = OwnerCommandKind.REPORT_WORKER_STATUS
    elif text == "/汇报 自动 暂停":
        kind = OwnerCommandKind.REPORT_WORKER_PAUSE
    elif text == "/汇报 自动 恢复":
        kind = OwnerCommandKind.REPORT_WORKER_RESUME
    elif text.startswith("/汇报 已读 "):
        kind = OwnerCommandKind.REPORT_ACK
        arguments["report_id"] = text.removeprefix("/汇报 已读 ").strip()
    elif text == "/隐私 状态":
        kind = OwnerCommandKind.PRIVACY_STATUS
    elif text == "/好友基线 状态":
        kind = OwnerCommandKind.FRIEND_BASELINE_STATUS
    elif text == "/好友基线 预览":
        kind = OwnerCommandKind.FRIEND_BASELINE_PREVIEW
    elif text.startswith("/图片 创建 "):
        kind = OwnerCommandKind.IMAGE_CREATE
        arguments["prompt"] = text.removeprefix("/图片 创建 ").strip()
    elif text == "/图片 巡检 状态":
        kind = OwnerCommandKind.IMAGE_ORPHAN_WORKER_STATUS
    elif text == "/图片 巡检 暂停":
        kind = OwnerCommandKind.IMAGE_ORPHAN_WORKER_PAUSE
    elif text == "/图片 巡检 恢复":
        kind = OwnerCommandKind.IMAGE_ORPHAN_WORKER_RESUME
    elif text == "/图片 巡检":
        kind = OwnerCommandKind.IMAGE_ORPHAN_SCAN
    elif text == "/模型 状态":
        kind = OwnerCommandKind.MODEL_STATUS
    elif text == "/影子 状态":
        kind = OwnerCommandKind.SHADOW_STATUS
    elif text == "/影子 最近":
        kind = OwnerCommandKind.SHADOW_RECENT
    elif match := _SHADOW_DETAIL_PATTERN.match(text):
        kind = OwnerCommandKind.SHADOW_DETAIL
        arguments["run_id"] = match.group(1)
    elif match := _SHADOW_REPLAY_PATTERN.match(text):
        kind = OwnerCommandKind.SHADOW_REPLAY
        arguments["message_id"] = match.group(1)
    elif match := _IMAGE_TASK_PATTERN.match(text):
        action = match.group(1)
        if action == "生成":
            kind = OwnerCommandKind.IMAGE_GENERATE
        elif action == "续期":
            kind = OwnerCommandKind.IMAGE_RENEW
        else:
            kind = OwnerCommandKind.IMAGE_STATUS
        arguments["task_id"] = match.group(2)
    elif text == "/资料 自动 状态":
        kind = OwnerCommandKind.KNOWLEDGE_WORKER_STATUS
    elif text == "/资料 自动 暂停":
        kind = OwnerCommandKind.KNOWLEDGE_WORKER_PAUSE
    elif text == "/资料 自动 恢复":
        kind = OwnerCommandKind.KNOWLEDGE_WORKER_RESUME
    elif match := _KNOWLEDGE_TASK_PATTERN.match(text):
        kind = (
            OwnerCommandKind.KNOWLEDGE_PROCESS
            if match.group(1) == "处理"
            else OwnerCommandKind.KNOWLEDGE_STATUS
        )
        arguments["job_id"] = match.group(2)
    elif text == "/主动 自动 状态":
        kind = OwnerCommandKind.PROACTIVE_WORKER_STATUS
    elif text == "/主动 自动 暂停":
        kind = OwnerCommandKind.PROACTIVE_WORKER_PAUSE
    elif text == "/主动 自动 恢复":
        kind = OwnerCommandKind.PROACTIVE_WORKER_RESUME
    elif text == "/外发 状态":
        kind = OwnerCommandKind.OUTBOUND_STATUS
    elif text == "/外发 暂停":
        kind = OwnerCommandKind.OUTBOUND_PAUSE
    elif text == "/外发 恢复":
        kind = OwnerCommandKind.OUTBOUND_RESUME
    elif match := _PROACTIVE_CREATE_PATTERN.match(text):
        kind = OwnerCommandKind.PROACTIVE_CREATE
        arguments = {
            "target_qq": match.group(1),
            "scheduled_for": match.group(2),
            "content": match.group(3).strip(),
        }
    elif match := _PROACTIVE_TASK_PATTERN.match(text):
        kind = (
            OwnerCommandKind.PROACTIVE_STATUS
            if match.group(1) == "状态"
            else OwnerCommandKind.PROACTIVE_CANCEL
        )
        arguments["task_id"] = match.group(2)
    elif match := _PROACTIVE_CONTENT_PATTERN.match(text):
        content_commands = {
            ("内容", "开启"): OwnerCommandKind.PROACTIVE_AUTO_CONTENT_ENABLE,
            ("内容", "关闭"): OwnerCommandKind.PROACTIVE_AUTO_CONTENT_DISABLE,
            ("日记", "开启"): OwnerCommandKind.PROACTIVE_DIARY_ENABLE,
            ("日记", "关闭"): OwnerCommandKind.PROACTIVE_DIARY_DISABLE,
        }
        kind = content_commands[(match.group(1), match.group(3))]
        arguments["user_qq"] = match.group(2)
    elif match := _PROACTIVE_USER_PATTERN.match(text):
        user_commands = {
            "状态": OwnerCommandKind.PROACTIVE_USER_STATUS,
            "开启": OwnerCommandKind.PROACTIVE_USER_ENABLE,
            "关闭": OwnerCommandKind.PROACTIVE_USER_DISABLE,
        }
        kind = user_commands[match.group(2)]
        arguments["user_qq"] = match.group(1)
    elif match := _USER_STATUS_PATTERN.match(text):
        kind = OwnerCommandKind.USER_STATUS
        arguments["user_qq"] = match.group(1)
    elif match := _USER_LABEL_PATTERN.match(text):
        kind = OwnerCommandKind.USER_SET_LABEL
        arguments["user_qq"] = match.group(1)
        raw = (match.group(2) or "").strip()
        if raw in {"清除", "删除"}:
            arguments["clear"] = "true"
        elif raw:
            arguments["label"] = raw
    elif match := _GROUP_LABEL_PATTERN.match(text):
        kind = OwnerCommandKind.GROUP_SET_LABEL
        arguments["group_qq"] = match.group(1)
        raw = (match.group(2) or "").strip()
        if raw in {"清除", "删除"}:
            arguments["clear"] = "true"
        elif raw:
            arguments["label"] = raw
    elif match := _USER_MARK_PATTERN.match(text):
        state_commands = {
            "旧友": OwnerCommandKind.USER_MARK_EXISTING_FRIEND,
            "新友": OwnerCommandKind.USER_MARK_NEW_FRIEND,
            "非好友": OwnerCommandKind.USER_MARK_NOT_FRIEND,
        }
        kind = state_commands[match.group(2)]
        arguments["user_qq"] = match.group(1)
    elif match := _USER_RESTORE_EVIDENCE_PATTERN.match(text):
        kind = OwnerCommandKind.USER_RESTORE_EVIDENCE
        arguments["user_qq"] = match.group(1)
    elif match := _USER_HISTORY_SIMPLE_PATTERN.match(text):
        history_commands = {
            "禁止": OwnerCommandKind.USER_HISTORY_DENY,
            "仅文件": OwnerCommandKind.USER_HISTORY_FILE_ONLY,
        }
        kind = history_commands[match.group(2)]
        arguments["user_qq"] = match.group(1)
    elif match := _USER_HISTORY_RANGE_PATTERN.match(text):
        kind = OwnerCommandKind.USER_HISTORY_SELECTED_RANGE
        arguments = {
            "user_qq": match.group(1),
            "selected_from": match.group(2),
            "selected_to": match.group(3),
            "max_messages": match.group(4),
        }
    elif match := _USER_HISTORY_ONCE_PATTERN.match(text):
        kind = OwnerCommandKind.USER_HISTORY_ONE_TIME
        arguments = {"user_qq": match.group(1), "max_messages": match.group(2)}
    elif match := _USER_HISTORY_PULL_PATTERN.match(text):
        kind = OwnerCommandKind.USER_HISTORY_PULL
        arguments = {"user_qq": match.group(1)}
    elif match := _USER_QZONE_PROFILE_STATUS_PATTERN.match(text):
        kind = OwnerCommandKind.USER_QZONE_PROFILE_STATUS
        arguments["user_qq"] = match.group(1)
    elif match := _USER_QZONE_PROFILE_DENY_PATTERN.match(text):
        kind = OwnerCommandKind.USER_QZONE_PROFILE_DENY
        arguments["user_qq"] = match.group(1)
    elif match := _USER_QZONE_PROFILE_ONCE_PATTERN.match(text):
        kind = OwnerCommandKind.USER_QZONE_PROFILE_ONE_TIME
        arguments = {"user_qq": match.group(1), "max_items": match.group(2)}
    elif match := _USER_QZONE_PROFILE_TTL_PATTERN.match(text):
        kind = OwnerCommandKind.USER_QZONE_PROFILE_TTL
        arguments = {
            "user_qq": match.group(1),
            "days": match.group(2),
            "max_items": match.group(3),
        }
    elif match := _USER_QZONE_PROFILE_PREVIEW_PATTERN.match(text):
        kind = OwnerCommandKind.USER_QZONE_PROFILE_PREVIEW
        arguments["user_qq"] = match.group(1)
    elif text == "/额度 状态" or text.startswith("/额度 状态 "):
        kind = OwnerCommandKind.QUOTA_STATUS
        rest = text.removeprefix("/额度 状态").strip()
        arguments["bot_qq"] = message.bot_qq
        if rest:
            arguments["user_qq"] = rest
    elif match := _QUOTA_TODAY_PATTERN.match(text):
        kind = OwnerCommandKind.QUOTA_TODAY_BONUS
        arguments = {
            "bot_qq": message.bot_qq,
            "user_qq": match.group(1),
            "amount": match.group(2),
        }
    elif match := _QUOTA_USER_LIMIT_PATTERN.match(text):
        kind = OwnerCommandKind.QUOTA_USER_LIMIT
        arguments = {
            "bot_qq": message.bot_qq,
            "user_qq": match.group(1),
            "daily_limit": match.group(2),
        }
    elif match := _QUOTA_GROUP_LIMIT_PATTERN.match(text):
        kind = OwnerCommandKind.QUOTA_GROUP_LIMIT
        arguments = {
            "bot_qq": message.bot_qq,
            "group_id": match.group(1),
            "daily_limit": match.group(2),
        }
    elif match := _PRIVACY_USER_PATTERN.match(text):
        privacy_commands = {
            "冻结": OwnerCommandKind.PRIVACY_FREEZE,
            "解冻": OwnerCommandKind.PRIVACY_UNFREEZE,
            "导出": OwnerCommandKind.PRIVACY_EXPORT,
            "删除": OwnerCommandKind.PRIVACY_DELETE,
        }
        kind = privacy_commands[match.group(1)]
        arguments["user_qq"] = match.group(2)

    if kind is None or any(not value for value in arguments.values()):
        return None

    command_key = f"owner-command:{message.event_key}:{kind.value}"
    return OwnerCommand(
        id=str(uuid5(NAMESPACE_URL, command_key)),
        kind=kind,
        actor_qq=owner_qq,
        source_message_id=message.source_message_id,
        arguments=arguments,
    )
