"""Shared command executor for owner QQ and dashboard operations."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from ych_bot.domain.control import (
    ApprovalStatus,
    ControlSource,
    OwnerCommand,
    OwnerCommandKind,
)
from ych_bot.domain.identity import FriendState, HistoryAccessMode, QzoneProfileAccessMode
from ych_bot.domain.images import ImageIntendedUse, ImageTaskStatus
from ych_bot.domain.model_qualification import QualificationCapability, QualificationReasonCode
from ych_bot.domain.operator_labels import (
    OperatorLabelError,
    OperatorSubjectKind,
    normalize_label,
    normalize_subject_id,
)
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.client import NapCatClient

from .images import ImageGenerationService
from .inference import ShadowReplyService
from .knowledge_processing import KnowledgeProcessingService
from .privacy import NapCatHistoryReader
from .proactive import ProactiveMessageService
from .qzone import QzoneTaskService
from .qzone_profile import MAX_PROFILE_ITEMS, QzoneProfileService
from .reports import OwnerReportError, OwnerReportService

LIVE_HISTORY_PULL_LIMIT = 5

MAX_HISTORY_MESSAGES = 500


@dataclass(frozen=True, slots=True)
class ControlExecutionResult:
    status: str
    command_id: str
    data: dict[str, Any]


class KnowledgeWorkerControl(Protocol):
    def snapshot(self) -> dict[str, Any]: ...

    def pause(self) -> bool: ...

    def resume(self) -> bool: ...


class ProactiveWorkerControl(Protocol):
    def snapshot(self) -> dict[str, Any]: ...

    def pause(self) -> bool: ...

    def resume(self) -> bool: ...


class QzoneWorkerControl(Protocol):
    def snapshot(self) -> dict[str, Any]: ...

    def pause(self) -> bool: ...

    def resume(self) -> bool: ...


class OwnerReportWorkerControl(Protocol):
    def snapshot(self) -> dict[str, Any]: ...

    def pause(self) -> bool: ...

    def resume(self) -> bool: ...


class OwnerControlService:
    def __init__(
        self,
        repository: SQLiteRepository,
        napcat_client: NapCatClient,
        *,
        owner_qq: str,
        bot_qq: str,
        image_generation_service: ImageGenerationService | None = None,
        image_orphan_worker_control: KnowledgeWorkerControl | None = None,
        knowledge_processing_service: KnowledgeProcessingService | None = None,
        knowledge_worker_control: KnowledgeWorkerControl | None = None,
        proactive_message_service: ProactiveMessageService | None = None,
        proactive_worker_control: ProactiveWorkerControl | None = None,
        outbox_worker_control: ProactiveWorkerControl | None = None,
        qzone_task_service: QzoneTaskService | None = None,
        qzone_worker_control: QzoneWorkerControl | None = None,
        qzone_profile_service: QzoneProfileService | None = None,
        history_reader: NapCatHistoryReader | None = None,
        owner_report_service: OwnerReportService | None = None,
        owner_report_worker_control: OwnerReportWorkerControl | None = None,
        shadow_reply_service: ShadowReplyService | None = None,
        quota_service: Any | None = None,
        qualification_status_service: Any | None = None,
    ) -> None:
        self.repository = repository
        self.napcat_client = napcat_client
        self.owner_qq = owner_qq
        self.bot_qq = bot_qq
        self.image_generation_service = image_generation_service
        self.image_orphan_worker_control = image_orphan_worker_control
        self.knowledge_processing_service = knowledge_processing_service
        self.knowledge_worker_control = knowledge_worker_control
        self.proactive_message_service = proactive_message_service
        self.proactive_worker_control = proactive_worker_control
        self.outbox_worker_control = outbox_worker_control
        self.qzone_task_service = qzone_task_service
        self.qzone_worker_control = qzone_worker_control
        self.qzone_profile_service = qzone_profile_service
        self.history_reader = history_reader
        self.owner_report_service = owner_report_service
        self.owner_report_worker_control = owner_report_worker_control
        self.shadow_reply_service = shadow_reply_service
        self.quota_service = quota_service
        self.qualification_status_service = qualification_status_service

    async def _active_owner_qq(self) -> str:
        return await self.repository.current_owner_qq() or self.owner_qq

    async def execute(self, command: OwnerCommand) -> ControlExecutionResult:
        if command.actor_qq != await self._active_owner_qq():
            return ControlExecutionResult("forbidden", command.id, {})
        inserted = await self.repository.store_control_command(command)
        if not inserted:
            return ControlExecutionResult("duplicate", command.id, {})

        try:
            data = await self._dispatch(command)
        except Exception as error:
            data = {"error": type(error).__name__, "message": str(error)[:500]}
            await self.repository.finish_control_command(command.id, status="failed", result=data)
            return ControlExecutionResult("failed", command.id, data)

        status = "pending_approval" if data.get("approval_code") else "completed"
        await self.repository.finish_control_command(command.id, status=status, result=data)
        return ControlExecutionResult(status, command.id, data)

    async def _dispatch(self, command: OwnerCommand) -> dict[str, Any]:
        kind = command.kind
        arguments = command.arguments

        if kind is OwnerCommandKind.HELP:
            return {
                "title": "YCH 主号控制帮助",
                "read_only": [
                    "/状态",
                    "/隐私 状态",
                    "/好友基线 状态",
                    "/外发 状态",
                    "/汇报 队列",
                    "/模型 状态",
                ],
                "controlled": [
                    "/用户 <QQ> 状态",
                    "/用户 <QQ> 备注 <名字>",
                    "/群 <QQ> 备注 <名字>",
                    "/用户 <QQ> 历史 拉取",
                    "/影子 状态",
                    "/资料 自动 状态",
                    "/主动 自动 状态",
                    "/空间 队列",
                ],
                "high_risk": [
                    "/确认 <审批码>",
                    "/拒绝 <审批码>",
                    "/隐私 删除 <QQ>",
                    "/空间 发布 <内容>",
                ],
                "handbook": "仪表盘「操作手册」和仓库 docs/operator/",
                "safety": (
                    "帮助不会打开模型、外发、历史、空间或 worker；高风险动作仍需独立审批，"
                    "当前 readiness 与总开关优先。"
                ),
            }
        if kind is OwnerCommandKind.MODEL_STATUS:
            return await self._handle_model_status()
        if kind in {OwnerCommandKind.STATUS, OwnerCommandKind.PRIVACY_STATUS}:
            return await self.repository.identity_summary()
        if kind in {
            OwnerCommandKind.QUOTA_STATUS,
            OwnerCommandKind.QUOTA_TODAY_BONUS,
            OwnerCommandKind.QUOTA_USER_LIMIT,
            OwnerCommandKind.QUOTA_GROUP_LIMIT,
        }:
            return await self._handle_quota_command(command)
        if kind is OwnerCommandKind.USER_STATUS:
            return await self.repository.user_detail(arguments["user_qq"])
        if kind in {OwnerCommandKind.USER_SET_LABEL, OwnerCommandKind.GROUP_SET_LABEL}:
            return await self._handle_operator_label(command)
        if kind is OwnerCommandKind.FRIEND_BASELINE_STATUS:
            return await self.repository.identity_summary()
        if kind is OwnerCommandKind.FRIEND_BASELINE_PREVIEW:
            return await self._stage_friend_baseline(command)
        if kind is OwnerCommandKind.REPORT_ACK:
            acknowledged = await self.repository.acknowledge_owner_report(
                arguments["report_id"],
                actor_qq=command.actor_qq,
            )
            return {"report_id": arguments["report_id"], "acknowledged": acknowledged}
        if kind in {
            OwnerCommandKind.REPORT_QUEUE,
            OwnerCommandKind.REPORT_STATUS,
        }:
            return await self._handle_report_command(command)
        if kind in {
            OwnerCommandKind.REPORT_WORKER_STATUS,
            OwnerCommandKind.REPORT_WORKER_PAUSE,
            OwnerCommandKind.REPORT_WORKER_RESUME,
        }:
            return self._handle_report_worker_command(kind)
        if kind in {
            OwnerCommandKind.IMAGE_CREATE,
            OwnerCommandKind.IMAGE_GENERATE,
            OwnerCommandKind.IMAGE_STATUS,
            OwnerCommandKind.IMAGE_RENEW,
            OwnerCommandKind.IMAGE_ORPHAN_SCAN,
        }:
            return await self._handle_image_command(command)
        if kind in {
            OwnerCommandKind.IMAGE_ORPHAN_WORKER_STATUS,
            OwnerCommandKind.IMAGE_ORPHAN_WORKER_PAUSE,
            OwnerCommandKind.IMAGE_ORPHAN_WORKER_RESUME,
        }:
            return self._handle_image_orphan_worker_command(kind)
        if kind in {
            OwnerCommandKind.SHADOW_STATUS,
            OwnerCommandKind.SHADOW_RECENT,
            OwnerCommandKind.SHADOW_DETAIL,
            OwnerCommandKind.SHADOW_REPLAY,
        }:
            return await self._handle_shadow_command(command)
        if kind in {
            OwnerCommandKind.KNOWLEDGE_PROCESS,
            OwnerCommandKind.KNOWLEDGE_STATUS,
        }:
            return await self._handle_knowledge_command(command)
        if kind in {
            OwnerCommandKind.KNOWLEDGE_WORKER_STATUS,
            OwnerCommandKind.KNOWLEDGE_WORKER_PAUSE,
            OwnerCommandKind.KNOWLEDGE_WORKER_RESUME,
        }:
            return self._handle_knowledge_worker_command(kind)
        if kind in {
            OwnerCommandKind.PROACTIVE_CREATE,
            OwnerCommandKind.PROACTIVE_STATUS,
            OwnerCommandKind.PROACTIVE_CANCEL,
            OwnerCommandKind.PROACTIVE_USER_STATUS,
            OwnerCommandKind.PROACTIVE_USER_ENABLE,
            OwnerCommandKind.PROACTIVE_USER_DISABLE,
            OwnerCommandKind.PROACTIVE_AUTO_CONTENT_ENABLE,
            OwnerCommandKind.PROACTIVE_AUTO_CONTENT_DISABLE,
            OwnerCommandKind.PROACTIVE_DIARY_ENABLE,
            OwnerCommandKind.PROACTIVE_DIARY_DISABLE,
        }:
            return await self._handle_proactive_command(command)
        if kind in {
            OwnerCommandKind.PROACTIVE_WORKER_STATUS,
            OwnerCommandKind.PROACTIVE_WORKER_PAUSE,
            OwnerCommandKind.PROACTIVE_WORKER_RESUME,
        }:
            return self._handle_proactive_worker_command(kind)
        if kind in {
            OwnerCommandKind.OUTBOUND_STATUS,
            OwnerCommandKind.OUTBOUND_PAUSE,
            OwnerCommandKind.OUTBOUND_RESUME,
        }:
            return self._handle_outbox_worker_command(kind)
        if kind in {
            OwnerCommandKind.QZONE_QUEUE,
            OwnerCommandKind.QZONE_DRAFT,
            OwnerCommandKind.QZONE_PUBLISH,
            OwnerCommandKind.QZONE_SCHEDULE,
            OwnerCommandKind.QZONE_CANCEL,
            OwnerCommandKind.QZONE_REVOKE,
        }:
            return await self._handle_qzone_command(command)
        if kind in {
            OwnerCommandKind.QZONE_PAUSE,
            OwnerCommandKind.QZONE_RESUME,
        }:
            return self._handle_qzone_worker_command(kind)

        friend_state_by_command = {
            OwnerCommandKind.USER_MARK_EXISTING_FRIEND: FriendState.EXISTING_FRIEND,
            OwnerCommandKind.USER_MARK_NEW_FRIEND: FriendState.NEW_FRIEND,
            OwnerCommandKind.USER_MARK_NOT_FRIEND: FriendState.NOT_FRIEND,
        }
        if state := friend_state_by_command.get(kind):
            await self.repository.set_friend_state(
                user_qq=arguments["user_qq"],
                state=state,
                updated_by=command.actor_qq,
                reason=f"command:{command.id}",
            )
            return {"user_qq": arguments["user_qq"], "friend_state": state.value}

        if kind is OwnerCommandKind.USER_RESTORE_EVIDENCE:
            return await self.repository.restore_evidence_friend_state(
                user_qq=arguments["user_qq"],
                updated_by=command.actor_qq,
                reason=f"command:{command.id}",
            )

        if kind is OwnerCommandKind.USER_HISTORY_DENY:
            await self._set_history(command, HistoryAccessMode.DENY)
            return {"user_qq": arguments["user_qq"], "history_mode": "deny"}
        if kind is OwnerCommandKind.USER_HISTORY_FILE_ONLY:
            await self._set_history(command, HistoryAccessMode.FILE_IMPORT_ONLY)
            return {"user_qq": arguments["user_qq"], "history_mode": "file_import_only"}
        if kind is OwnerCommandKind.USER_QZONE_PROFILE_STATUS:
            return await self.repository.user_detail(arguments["user_qq"])
        if kind is OwnerCommandKind.USER_QZONE_PROFILE_DENY:
            await self.repository.set_qzone_profile_policy(
                user_qq=arguments["user_qq"],
                mode=QzoneProfileAccessMode.DENY,
                updated_by=command.actor_qq,
                reason=f"command:{command.id}",
            )
            return {
                "user_qq": arguments["user_qq"],
                "qzone_profile_mode": QzoneProfileAccessMode.DENY.value,
            }
        if kind is OwnerCommandKind.USER_QZONE_PROFILE_ONE_TIME:
            _bounded_profile_items(arguments["max_items"])
            return await self._request_approval(
                command,
                "qzone_profile.one_time",
                arguments["user_qq"],
            )
        if kind is OwnerCommandKind.USER_QZONE_PROFILE_TTL:
            _bounded_profile_days(arguments["days"])
            _bounded_profile_items(arguments["max_items"])
            return await self._request_approval(
                command,
                "qzone_profile.ttl",
                arguments["user_qq"],
            )
        if kind is OwnerCommandKind.USER_QZONE_PROFILE_PREVIEW:
            if self.qzone_profile_service is None:
                return {"allowed": False, "reason": "qzone_profile_service_unavailable"}
            result = await self.qzone_profile_service.preview(
                arguments["user_qq"],
                purpose="initialize_user_profile",
                actor_qq=command.actor_qq,
                source="dashboard" if command.source is ControlSource.DASHBOARD else "command",
            )
            return result.public_dict()

        if kind in {
            OwnerCommandKind.USER_HISTORY_SELECTED_RANGE,
            OwnerCommandKind.USER_HISTORY_ONE_TIME,
        }:
            _bounded_history_messages(arguments["max_messages"])
            if kind is OwnerCommandKind.USER_HISTORY_SELECTED_RANGE:
                _validated_history_range(
                    arguments["selected_from"],
                    arguments["selected_to"],
                )
            request_type = (
                "history.selected_range"
                if kind is OwnerCommandKind.USER_HISTORY_SELECTED_RANGE
                else "history.one_time"
            )
            return await self._request_approval(command, request_type, arguments["user_qq"])
        if kind is OwnerCommandKind.USER_HISTORY_PULL:
            return await self._pull_live_history(command)

        if kind in {OwnerCommandKind.PRIVACY_FREEZE, OwnerCommandKind.PRIVACY_UNFREEZE}:
            frozen = kind is OwnerCommandKind.PRIVACY_FREEZE
            await self.repository.set_user_frozen(
                user_qq=arguments["user_qq"],
                frozen=frozen,
                updated_by=command.actor_qq,
            )
            return {"user_qq": arguments["user_qq"], "frozen": frozen}

        if kind in {OwnerCommandKind.PRIVACY_EXPORT, OwnerCommandKind.PRIVACY_DELETE}:
            request_type = (
                "privacy.export" if kind is OwnerCommandKind.PRIVACY_EXPORT else "privacy.delete"
            )
            return await self._request_approval(command, request_type, arguments["user_qq"])

        if kind is OwnerCommandKind.APPROVE:
            return await self._approve(command.arguments["approval_code"], command.actor_qq)
        if kind is OwnerCommandKind.REJECT:
            return await self._reject(command.arguments["approval_code"], command.actor_qq)

        return {"deferred": True, "action": kind.value}

    async def _set_history(self, command: OwnerCommand, mode: HistoryAccessMode) -> None:
        await self.repository.set_history_policy(
            user_qq=command.arguments["user_qq"],
            mode=mode,
            updated_by=command.actor_qq,
            reason=f"command:{command.id}",
        )

    async def _pull_live_history(self, command: OwnerCommand) -> dict[str, Any]:
        user_qq = command.arguments["user_qq"]
        if user_qq != await self._active_owner_qq():
            return {
                "allowed": False,
                "reason": "live_history_owner_only",
                "user_qq": user_qq,
                "message_count": 0,
            }
        if self.history_reader is None:
            return {
                "allowed": False,
                "reason": "history_reader_unavailable",
                "user_qq": user_qq,
                "message_count": 0,
            }
        result = await self.history_reader.read_private_history(
            user_qq=user_qq,
            count=LIVE_HISTORY_PULL_LIMIT,
            include_media=False,
            purpose="restore_recent_context",
        )
        return {
            "allowed": result.allowed,
            "reason": result.reason,
            "user_qq": user_qq,
            "message_count": len(result.messages),
        }

    async def _request_approval(
        self,
        command: OwnerCommand,
        request_type: str,
        subject_id: str,
    ) -> dict[str, Any]:
        approval_id = str(uuid4())
        approval_code = secrets.token_hex(3).upper()
        await self.repository.create_approval(
            approval_id=approval_id,
            request_type=request_type,
            subject_id=subject_id,
            approval_code=approval_code,
            requested_to=self.owner_qq,
            payload=command.arguments,
        )
        return {
            "approval_id": approval_id,
            "approval_code": approval_code,
            "request_type": request_type,
            "expires_in_minutes": 30,
        }

    async def _stage_friend_baseline(self, command: OwnerCommand) -> dict[str, Any]:
        friend_ids = await self.napcat_client.get_friend_ids()
        candidate_id = str(uuid4())
        candidate = await self.repository.stage_friend_baseline_candidate(
            candidate_id=candidate_id,
            bot_qq=self.bot_qq,
            friend_ids=friend_ids,
        )
        approval = await self._request_approval(command, "friend_baseline.create", candidate_id)
        return {**candidate, **approval}

    async def _approve(self, code: str, actor_qq: str) -> dict[str, Any]:
        approval = await self.repository.approval_by_code(code)
        if approval is None:
            return {"approved": False, "reason": "approval_not_found"}
        if approval["requested_to"] != actor_qq:
            return {"approved": False, "reason": "wrong_approver"}
        if approval["status"] != ApprovalStatus.PENDING.value:
            return {"approved": False, "reason": f"already_{approval['status']}"}
        if datetime.fromisoformat(approval["expires_at"]) < datetime.now(UTC):
            if approval["request_type"] == "image_generation.use":
                await self.repository.decide_image_task(
                    task_id=approval["subject_id"],
                    status=ImageTaskStatus.APPROVAL_EXPIRED,
                    actor_qq=None,
                )
            elif approval["request_type"] == "knowledge.preview":
                await self.repository.expire_knowledge_preview(approval["subject_id"])
            elif (
                approval["request_type"]
                in {
                    "proactive_message.schedule",
                    "proactive_message.reapprove",
                }
                and self.proactive_message_service is not None
            ):
                await self.proactive_message_service.expire_approval(approval["subject_id"])
            elif (
                approval["request_type"] in {"qzone.publish", "qzone.publish.reapprove"}
                and self.qzone_task_service is not None
            ):
                await self.qzone_task_service.expire(approval["subject_id"])
            elif approval["request_type"] == "qzone.delete" and self.qzone_task_service is not None:
                await self.qzone_task_service.expire_delete(approval["subject_id"])
            await self.repository.decide_approval(approval["id"], ApprovalStatus.EXPIRED)
            return {"approved": False, "reason": "approval_expired"}

        request_type = approval["request_type"]
        payload = approval["payload"]
        if request_type == "history.selected_range":
            selected_from, selected_to = _validated_history_range(
                payload["selected_from"],
                payload["selected_to"],
            )
            await self.repository.set_history_policy(
                user_qq=payload["user_qq"],
                mode=HistoryAccessMode.SELECTED_RANGE,
                selected_from=selected_from,
                selected_to=selected_to,
                max_messages=_bounded_history_messages(payload["max_messages"]),
                updated_by=actor_qq,
                reason=f"approval:{approval['id']}",
            )
        elif request_type == "history.one_time":
            await self.repository.set_history_policy(
                user_qq=payload["user_qq"],
                mode=HistoryAccessMode.ONE_TIME,
                max_messages=_bounded_history_messages(payload["max_messages"]),
                one_time_remaining=1,
                updated_by=actor_qq,
                reason=f"approval:{approval['id']}",
            )
        elif request_type == "qzone_profile.one_time":
            await self.repository.set_qzone_profile_policy(
                user_qq=payload["user_qq"],
                mode=QzoneProfileAccessMode.ONE_TIME,
                max_items=_bounded_profile_items(payload["max_items"]),
                one_time_remaining=1,
                updated_by=actor_qq,
                reason=f"approval:{approval['id']}",
            )
        elif request_type == "qzone_profile.ttl":
            days = _bounded_profile_days(payload["days"])
            await self.repository.set_qzone_profile_policy(
                user_qq=payload["user_qq"],
                mode=QzoneProfileAccessMode.TTL,
                max_items=_bounded_profile_items(payload["max_items"]),
                expires_at=(datetime.now(UTC) + timedelta(days=days)).isoformat(),
                updated_by=actor_qq,
                reason=f"approval:{approval['id']}",
            )
        elif request_type in {"privacy.export", "privacy.delete"}:
            await self.repository.create_privacy_request(
                request_id=str(uuid4()),
                user_qq=payload["user_qq"],
                request_kind=request_type.removeprefix("privacy."),
                requested_by=actor_qq,
                reason=f"approval:{approval['id']}",
            )
        elif request_type == "friend_baseline.create":
            candidate = await self.repository.baseline_candidate(approval["subject_id"])
            if candidate is None or candidate["status"] != "pending_approval":
                return {"approved": False, "reason": "baseline_candidate_unavailable"}
            await self.repository.record_friend_baseline(
                bot_qq=candidate["bot_qq"],
                friend_ids=candidate["friend_ids"],
                captured_at=datetime.fromisoformat(candidate["fetched_at"]),
            )
            await self.repository.finish_baseline_candidate(candidate["id"], "completed")
        elif request_type == "image_generation.use":
            approved = await self.repository.decide_image_task(
                task_id=approval["subject_id"],
                status=ImageTaskStatus.APPROVED,
                actor_qq=actor_qq,
            )
            if not approved:
                return {"approved": False, "reason": "image_task_unavailable"}
        elif request_type == "knowledge.preview":
            approved = await self.repository.approve_knowledge_preview(
                job_id=approval["subject_id"],
                approved_by=actor_qq,
            )
            if approved is None:
                return {"approved": False, "reason": "knowledge_preview_unavailable"}
        elif request_type in {
            "proactive_message.schedule",
            "proactive_message.reapprove",
        }:
            if self.proactive_message_service is None:
                return {"approved": False, "reason": "proactive_service_unavailable"}
            approved = await self.proactive_message_service.approve(
                approval["subject_id"],
                actor_qq=actor_qq,
                late=request_type == "proactive_message.reapprove",
            )
            if not approved:
                return {"approved": False, "reason": "proactive_task_unavailable"}
        elif request_type in {"qzone.publish", "qzone.publish.reapprove"}:
            if self.qzone_task_service is None:
                return {"approved": False, "reason": "qzone_service_unavailable"}
            approved = await self.qzone_task_service.approve(
                approval["subject_id"],
                actor_qq=actor_qq,
                late=request_type == "qzone.publish.reapprove",
            )
            if not approved:
                return {"approved": False, "reason": "qzone_post_unavailable"}
        elif request_type == "qzone.delete":
            if self.qzone_task_service is None:
                return {"approved": False, "reason": "qzone_service_unavailable"}
            approved = await self.qzone_task_service.approve_delete(
                approval["subject_id"],
                actor_qq=actor_qq,
            )
            if not approved:
                return {"approved": False, "reason": "qzone_post_unavailable"}
        else:
            return {"approved": False, "reason": "unsupported_approval_type"}

        await self.repository.decide_approval(approval["id"], ApprovalStatus.APPROVED)
        return {"approved": True, "approval_id": approval["id"], "request_type": request_type}

    async def _reject(self, code: str, actor_qq: str) -> dict[str, Any]:
        approval = await self.repository.approval_by_code(code)
        if approval is None or approval["requested_to"] != actor_qq:
            return {"rejected": False, "reason": "approval_not_found"}
        if approval["status"] != ApprovalStatus.PENDING.value:
            return {"rejected": False, "reason": f"already_{approval['status']}"}
        if approval["request_type"] == "image_generation.use":
            rejected = await self.repository.decide_image_task(
                task_id=approval["subject_id"],
                status=ImageTaskStatus.REJECTED,
                actor_qq=actor_qq,
            )
            if not rejected:
                return {"rejected": False, "reason": "image_task_unavailable"}
        elif approval["request_type"] == "knowledge.preview":
            rejected = await self.repository.reject_knowledge_preview(
                job_id=approval["subject_id"],
                rejected_by=actor_qq,
            )
            if not rejected:
                return {"rejected": False, "reason": "knowledge_preview_unavailable"}
        elif approval["request_type"] in {
            "proactive_message.schedule",
            "proactive_message.reapprove",
        }:
            if self.proactive_message_service is None:
                return {"rejected": False, "reason": "proactive_service_unavailable"}
            rejected = await self.proactive_message_service.reject(
                approval["subject_id"],
                actor_qq=actor_qq,
            )
            if not rejected:
                return {"rejected": False, "reason": "proactive_task_unavailable"}
        elif approval["request_type"] in {"qzone.publish", "qzone.publish.reapprove"}:
            if self.qzone_task_service is None:
                return {"rejected": False, "reason": "qzone_service_unavailable"}
            rejected = await self.qzone_task_service.reject(
                approval["subject_id"],
                actor_qq=actor_qq,
            )
            if not rejected:
                return {"rejected": False, "reason": "qzone_post_unavailable"}
        elif approval["request_type"] == "qzone.delete":
            if self.qzone_task_service is None:
                return {"rejected": False, "reason": "qzone_service_unavailable"}
            rejected = await self.qzone_task_service.reject_delete(
                approval["subject_id"],
                actor_qq=actor_qq,
            )
            if not rejected:
                return {"rejected": False, "reason": "qzone_post_unavailable"}
        await self.repository.decide_approval(approval["id"], ApprovalStatus.REJECTED)
        return {"rejected": True, "approval_id": approval["id"]}

    async def _handle_image_command(self, command: OwnerCommand) -> dict[str, Any]:
        service = self.image_generation_service
        if service is None:
            return {"available": False, "reason": "image_service_unavailable"}
        if command.kind is OwnerCommandKind.IMAGE_CREATE:
            return await service.create_task(
                prompt=command.arguments["prompt"],
                intended_use=ImageIntendedUse.GENERAL,
                requested_by=command.actor_qq,
                request_source="owner_qq",
            )
        if command.kind is OwnerCommandKind.IMAGE_GENERATE:
            return await service.generate(command.arguments["task_id"])
        if command.kind is OwnerCommandKind.IMAGE_RENEW:
            return await service.renew_approval(command.arguments["task_id"])
        if command.kind is OwnerCommandKind.IMAGE_ORPHAN_SCAN:
            return await service.scan_orphans(created_by=command.actor_qq)
        return await service.task(command.arguments["task_id"])

    async def _handle_shadow_command(self, command: OwnerCommand) -> dict[str, Any]:
        service = self.shadow_reply_service
        if service is None:
            return {"available": False, "reason": "shadow_service_unavailable"}
        if command.kind is OwnerCommandKind.SHADOW_DETAIL:
            return await service.run_detail(command.arguments["run_id"])
        if command.kind is OwnerCommandKind.SHADOW_REPLAY:
            return await service.replay(
                command.arguments["message_id"],
                created_by=command.actor_qq,
            )
        limit = 20 if command.kind is OwnerCommandKind.SHADOW_RECENT else 5
        return await service.snapshot(limit=limit)

    def _handle_image_orphan_worker_command(self, kind: OwnerCommandKind) -> dict[str, Any]:
        worker = self.image_orphan_worker_control
        if worker is None:
            return {"available": False, "reason": "image_orphan_worker_unavailable"}
        changed = False
        if kind is OwnerCommandKind.IMAGE_ORPHAN_WORKER_PAUSE:
            changed = worker.pause()
        elif kind is OwnerCommandKind.IMAGE_ORPHAN_WORKER_RESUME:
            changed = worker.resume()
        return {"changed": changed, **worker.snapshot()}

    async def _handle_knowledge_command(self, command: OwnerCommand) -> dict[str, Any]:
        service = self.knowledge_processing_service
        job_id = command.arguments["job_id"]
        if command.kind is OwnerCommandKind.KNOWLEDGE_STATUS:
            job = await self.repository.knowledge_job(job_id)
            return job or {"available": False, "reason": "knowledge_job_not_found"}
        if service is None:
            return {"available": False, "reason": "knowledge_service_unavailable"}
        result = await service.process(job_id)
        job = await self.repository.knowledge_job(result.job_id)
        return job or {
            "job_id": result.job_id,
            "status": result.status,
            "completed_chunks": result.completed_chunks,
            "total_chunks": result.total_chunks,
        }

    def _handle_knowledge_worker_command(self, kind: OwnerCommandKind) -> dict[str, Any]:
        worker = self.knowledge_worker_control
        if worker is None:
            return {"available": False, "reason": "knowledge_worker_unavailable"}
        changed = False
        if kind is OwnerCommandKind.KNOWLEDGE_WORKER_PAUSE:
            changed = worker.pause()
        elif kind is OwnerCommandKind.KNOWLEDGE_WORKER_RESUME:
            changed = worker.resume()
        return {"changed": changed, **worker.snapshot()}

    async def _handle_proactive_command(self, command: OwnerCommand) -> dict[str, Any]:
        service = self.proactive_message_service
        if service is None:
            return {"available": False, "reason": "proactive_service_unavailable"}
        if command.kind is OwnerCommandKind.PROACTIVE_CREATE:
            return await service.create_from_local_time(
                target_qq=command.arguments["target_qq"],
                content=command.arguments["content"],
                scheduled_local=command.arguments["scheduled_for"],
                created_by=command.actor_qq,
                source="owner_qq",
            )
        if command.kind is OwnerCommandKind.PROACTIVE_STATUS:
            return await service.task(command.arguments["task_id"])
        if command.kind is OwnerCommandKind.PROACTIVE_CANCEL:
            return await service.cancel(command.arguments["task_id"], actor_qq=command.actor_qq)
        if command.kind is OwnerCommandKind.PROACTIVE_USER_STATUS:
            return await service.policy(command.arguments["user_qq"])
        if command.kind in {
            OwnerCommandKind.PROACTIVE_AUTO_CONTENT_ENABLE,
            OwnerCommandKind.PROACTIVE_AUTO_CONTENT_DISABLE,
        }:
            return await service.set_auto_content_enabled(
                command.arguments["user_qq"],
                enabled=command.kind is OwnerCommandKind.PROACTIVE_AUTO_CONTENT_ENABLE,
                updated_by=command.actor_qq,
            )
        if command.kind in {
            OwnerCommandKind.PROACTIVE_DIARY_ENABLE,
            OwnerCommandKind.PROACTIVE_DIARY_DISABLE,
        }:
            return await service.set_diary_enabled(
                command.arguments["user_qq"],
                enabled=command.kind is OwnerCommandKind.PROACTIVE_DIARY_ENABLE,
                updated_by=command.actor_qq,
            )
        enabled = command.kind is OwnerCommandKind.PROACTIVE_USER_ENABLE
        return await service.set_policy_enabled(
            command.arguments["user_qq"],
            enabled=enabled,
            updated_by=command.actor_qq,
        )

    def _handle_proactive_worker_command(self, kind: OwnerCommandKind) -> dict[str, Any]:
        worker = self.proactive_worker_control
        if worker is None:
            return {"available": False, "reason": "proactive_worker_unavailable"}
        changed = False
        if kind is OwnerCommandKind.PROACTIVE_WORKER_PAUSE:
            changed = worker.pause()
        elif kind is OwnerCommandKind.PROACTIVE_WORKER_RESUME:
            changed = worker.resume()
        return {"changed": changed, **worker.snapshot()}

    def _handle_outbox_worker_command(self, kind: OwnerCommandKind) -> dict[str, Any]:
        worker = self.outbox_worker_control
        if worker is None:
            return {"available": False, "reason": "outbox_worker_unavailable"}
        changed = False
        if kind is OwnerCommandKind.OUTBOUND_PAUSE:
            changed = worker.pause()
        elif kind is OwnerCommandKind.OUTBOUND_RESUME:
            changed = worker.resume()
        return {"changed": changed, **worker.snapshot()}

    async def _handle_qzone_command(self, command: OwnerCommand) -> dict[str, Any]:
        service = self.qzone_task_service
        if service is None:
            return {"available": False, "reason": "qzone_service_unavailable"}
        if command.kind is OwnerCommandKind.QZONE_QUEUE:
            return {
                "items": await service.posts(limit=50),
                "summary": await self.repository.qzone_summary(),
            }
        if command.kind is OwnerCommandKind.QZONE_DRAFT:
            return await service.create_draft(
                content=command.arguments["content"],
                created_by=command.actor_qq,
                source="owner_qq",
            )
        if command.kind in {
            OwnerCommandKind.QZONE_PUBLISH,
            OwnerCommandKind.QZONE_SCHEDULE,
        }:
            return await service.request_from_local_time(
                content=command.arguments["content"],
                scheduled_local=command.arguments.get("scheduled_for"),
                created_by=command.actor_qq,
                source="owner_qq",
            )
        if command.kind is OwnerCommandKind.QZONE_REVOKE:
            return await service.request_revoke(
                command.arguments["post_id"],
                actor_qq=command.actor_qq,
                source="owner_qq",
            )
        return await service.cancel(
            command.arguments["post_id"],
            actor_qq=command.actor_qq,
        )

    def _handle_qzone_worker_command(self, kind: OwnerCommandKind) -> dict[str, Any]:
        worker = self.qzone_worker_control
        if worker is None:
            return {"available": False, "reason": "qzone_worker_unavailable"}
        changed = worker.pause() if kind is OwnerCommandKind.QZONE_PAUSE else worker.resume()
        return {"changed": changed, **worker.snapshot()}

    async def _handle_operator_label(self, command: OwnerCommand) -> dict[str, Any]:
        if command.kind is OwnerCommandKind.GROUP_SET_LABEL:
            kind = OperatorSubjectKind.GROUP
            subject_id = normalize_subject_id(command.arguments["group_qq"])
        else:
            kind = OperatorSubjectKind.USER
            subject_id = normalize_subject_id(command.arguments["user_qq"])
        if command.arguments.get("clear") == "true":
            label = ""
        elif "label" not in command.arguments:
            labels = await self.repository.list_operator_labels()
            current = next(
                (
                    item
                    for item in labels
                    if item["subject_kind"] == kind.value and item["subject_id"] == subject_id
                ),
                None,
            )
            return {
                "subject_kind": kind.value,
                "subject_id": subject_id,
                "label": current["label"] if current else "",
            }
        else:
            try:
                label = normalize_label(command.arguments.get("label"))
            except OperatorLabelError as exc:
                return {"error": str(exc), "subject_kind": kind.value, "subject_id": subject_id}
        saved = await self.repository.upsert_operator_label(
            subject_kind=kind.value,
            subject_id=subject_id,
            label=label,
            updated_by=command.actor_qq,
        )
        return saved

    async def _handle_report_command(self, command: OwnerCommand) -> dict[str, Any]:
        service = self.owner_report_service
        if service is None:
            return {"available": False, "reason": "owner_report_service_unavailable"}
        if command.kind is OwnerCommandKind.REPORT_QUEUE:
            return {
                "items": await service.queue(limit=50),
                "summary": await self.repository.owner_report_summary(),
            }
        try:
            return await service.report(command.arguments["report_id"])
        except OwnerReportError as exc:
            return {"error": str(exc)}

    def _handle_report_worker_command(self, kind: OwnerCommandKind) -> dict[str, Any]:
        worker = self.owner_report_worker_control
        if worker is None:
            return {"available": False, "reason": "owner_report_worker_unavailable"}
        changed = False
        if kind is OwnerCommandKind.REPORT_WORKER_PAUSE:
            changed = worker.pause()
        elif kind is OwnerCommandKind.REPORT_WORKER_RESUME:
            changed = worker.resume()
        return {"changed": changed, **worker.snapshot()}

    async def _handle_quota_command(self, command: OwnerCommand) -> dict[str, Any]:
        service = self.quota_service
        if service is None:
            return {"available": False, "reason": "quota_service_unavailable"}
        bot_qq = command.arguments.get("bot_qq") or self.bot_qq
        kind = command.kind
        if kind is OwnerCommandKind.QUOTA_STATUS:
            user_qq = command.arguments.get("user_qq")
            items = await service.list_quotas(bot_qq=bot_qq, peer_kind="private")
            if user_qq:
                items = [item for item in items if item["peer_id"] == user_qq]
            return {"bot_qq": bot_qq, "items": items}
        if kind is OwnerCommandKind.QUOTA_TODAY_BONUS:
            return await service.add_today_bonus(
                bot_qq=bot_qq,
                peer_kind="private",
                peer_id=command.arguments["user_qq"],
                amount=int(command.arguments["amount"]),
                updated_by=command.actor_qq,
            )
        if kind is OwnerCommandKind.QUOTA_USER_LIMIT:
            return await service.set_daily_limit(
                bot_qq=bot_qq,
                peer_kind="private",
                peer_id=command.arguments["user_qq"],
                daily_limit=int(command.arguments["daily_limit"]),
                updated_by=command.actor_qq,
            )
        return await service.set_daily_limit(
            bot_qq=bot_qq,
            peer_kind="group",
            peer_id=command.arguments["group_id"],
            daily_limit=int(command.arguments["daily_limit"]),
            updated_by=command.actor_qq,
        )

    async def _handle_model_status(self) -> dict[str, Any]:
        if self.qualification_status_service is not None:
            return await self.qualification_status_service.owner_status_projection()
        return {
            "can_start_paid_run": False,
            "items": [
                {
                    "capability": capability.value,
                    "status": "unqualified",
                    "qualifies": False,
                    "activates_production": False,
                    "blocker_codes": [QualificationReasonCode.DEFAULT_DENIED.value],
                }
                for capability in QualificationCapability
            ],
        }


def _bounded_profile_items(raw: str | int) -> int:
    value = int(raw)
    if not 1 <= value <= MAX_PROFILE_ITEMS:
        raise ValueError(f"max_items must be between 1 and {MAX_PROFILE_ITEMS}")
    return value


def _bounded_history_messages(raw: str | int) -> int:
    value = int(raw)
    if not 1 <= value <= MAX_HISTORY_MESSAGES:
        raise ValueError(f"max_messages must be between 1 and {MAX_HISTORY_MESSAGES}")
    return value


def _validated_history_range(selected_from: str, selected_to: str) -> tuple[str, str]:
    start = date.fromisoformat(selected_from)
    end = date.fromisoformat(selected_to)
    if start > end:
        raise ValueError("selected_from must not be after selected_to")
    return start.isoformat(), end.isoformat()


def _bounded_profile_days(raw: str | int) -> int:
    value = int(raw)
    if not 1 <= value <= 90:
        raise ValueError("days must be between 1 and 90")
    return value
