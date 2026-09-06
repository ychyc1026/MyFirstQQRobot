"""Production reply-runtime policy, shared control, and one-step execution."""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ych_bot.domain.reply_pipeline import (
    ReplyActivationMode,
    ReplyFailure,
    ReplyFailureCategory,
    ReplyRunStage,
    ReplyRuntimeDecision,
    ReplyRuntimeFailureCode,
)

MODE_ORDER = {
    ReplyActivationMode.OBSERVE_ONLY: 0,
    ReplyActivationMode.SHADOW: 1,
    ReplyActivationMode.OWNER_APPROVED: 2,
    ReplyActivationMode.LIMITED_AUTO: 3,
    ReplyActivationMode.AUTO: 4,
}


def owner_private_conversation_key(bot_qq: str, owner_qq: str) -> str:
    return f"{bot_qq}:private:{owner_qq}"


def is_production_auto_conversation(conversation_key: str, bot_qq: str) -> bool:
    parts = conversation_key.split(":", 2)
    if len(parts) != 3:
        return False
    account, kind, peer = parts
    return account == bot_qq and kind in {"private", "group"} and bool(peer.strip())


def _is_group_conversation_key(conversation_key: str) -> bool:
    parts = conversation_key.split(":", 2)
    return len(parts) == 3 and parts[1] == "group"


def _triggers_mention_bot(detail: dict[str, Any], bot_qq: str) -> bool:
    for trigger in detail.get("triggers") or ():
        for segment in trigger.get("segments") or ():
            if not isinstance(segment, dict) or segment.get("type") != "at":
                continue
            data = segment.get("data") or {}
            if str(data.get("qq") or "").strip() == bot_qq:
                return True
    return False


@dataclass(frozen=True, slots=True)
class ReplyRuntimeGates:
    worker_enabled: bool = False
    max_mode: ReplyActivationMode = ReplyActivationMode.OBSERVE_ONLY
    chat_model_configured: bool = False
    chat_route_enabled: bool = False
    model_network_enabled: bool = False
    chat_circuit_available: bool = True
    outbound_enabled: bool = False
    outbound_worker_active: bool = False
    onebot_token_configured: bool = False
    onebot_connected: bool = False


def evaluate_reply_runtime(state: dict[str, Any], gates: ReplyRuntimeGates) -> ReplyRuntimeDecision:
    requested = ReplyActivationMode(str(state["requested_mode"]))
    revision = int(state["revision"])
    blockers: list[str] = []
    if not gates.worker_enabled:
        blockers.append("reply_worker_disabled")
    if bool(state["emergency_paused"]):
        blockers.append("emergency_paused")
    capped = min((requested, gates.max_mode), key=lambda item: MODE_ORDER[item])
    if capped is not requested:
        blockers.append("configuration_mode_ceiling")
    model_checks = {
        "chat_model_unconfigured": gates.chat_model_configured,
        "chat_route_disabled": gates.chat_route_enabled,
        "model_network_disabled": gates.model_network_enabled,
        "chat_circuit_open": gates.chat_circuit_available,
    }
    delivery_checks = {
        "outbound_disabled": gates.outbound_enabled,
        "outbound_worker_inactive": gates.outbound_worker_active,
        "onebot_token_missing": gates.onebot_token_configured,
        "onebot_disconnected": gates.onebot_connected,
    }
    if requested is not ReplyActivationMode.OBSERVE_ONLY:
        blockers.extend(code for code, passed in model_checks.items() if not passed)
    if requested.allows_delivery:
        blockers.extend(code for code, passed in delivery_checks.items() if not passed)
    model_blocked = not all(model_checks.values())
    if (
        not gates.worker_enabled
        or bool(state["emergency_paused"])
        or capped is ReplyActivationMode.OBSERVE_ONLY
        or model_blocked
    ):
        effective = ReplyActivationMode.OBSERVE_ONLY
    elif capped is ReplyActivationMode.SHADOW or not all(delivery_checks.values()):
        effective = ReplyActivationMode.SHADOW
    else:
        effective = capped
    return ReplyRuntimeDecision(
        bot_qq=str(state["bot_qq"]),
        requested_mode=requested,
        effective_mode=effective,
        revision=revision,
        emergency_paused=bool(state["emergency_paused"]),
        blockers=tuple(dict.fromkeys(blockers)),
    )


class ReplyRuntimeControlError(RuntimeError):
    pass


class ReplyRuntimeControlService:
    def __init__(
        self,
        repository: Any,
        *,
        bot_qq: str,
        owner_qq: str,
        gates_provider: Any,
        outbox_control: Any | None = None,
    ) -> None:
        self._repository = repository
        self.bot_qq = bot_qq
        self.owner_qq = owner_qq
        self._gates_provider = gates_provider
        self._outbox_control = outbox_control
        self._previews: dict[str, dict[str, Any]] = {}

    async def _gates(self) -> ReplyRuntimeGates:
        value = self._gates_provider()
        if inspect.isawaitable(value):
            value = await value
        return value if isinstance(value, ReplyRuntimeGates) else ReplyRuntimeGates(**value)

    async def status(self) -> dict[str, Any]:
        state = await self._repository.ensure_reply_runtime_state(self.bot_qq)
        gates = await self._gates()
        decision = evaluate_reply_runtime(state, gates)
        eligibility = await self._repository.reply_eligibility(self.bot_qq)
        approvals = await self._repository.reply_runtime_approvals(self.bot_qq, status="pending")
        return {
            "state": state,
            "requested_mode": decision.requested_mode.value,
            "effective_mode": decision.effective_mode.value,
            "blockers": list(decision.blockers),
            "configuration_ceiling": gates.max_mode.value,
            "eligibility": eligibility,
            "approval_backlog": len(approvals),
        }

    async def preview(self, *, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        state = await self._repository.ensure_reply_runtime_state(self.bot_qq)
        gates = await self._gates()
        now = datetime.now(UTC)
        token = str(uuid4())
        evidence = self._evidence_hash(state, gates, action, payload)
        self._previews[token] = {
            "action": action,
            "payload": payload,
            "revision": int(state["revision"]),
            "evidence": evidence,
            "expires_at": now + timedelta(minutes=5),
        }
        return {
            "confirmation_token": token,
            "action": action,
            "payload": payload,
            "runtime_revision": int(state["revision"]),
            "readiness_hash": evidence,
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
        }

    async def confirm(self, *, token: str, actor: str, source: str = "dashboard") -> dict[str, Any]:
        preview = self._previews.pop(token, None)
        if preview is None or preview["expires_at"] <= datetime.now(UTC):
            await self._record_rejection(
                action="confirmation", actor=actor, source=source, reason="missing_or_expired"
            )
            raise ReplyRuntimeControlError("reply runtime confirmation is missing or expired")
        state = await self._repository.ensure_reply_runtime_state(self.bot_qq)
        gates = await self._gates()
        current_evidence = self._evidence_hash(state, gates, preview["action"], preview["payload"])
        if preview["revision"] != int(state["revision"]) or preview["evidence"] != current_evidence:
            await self._record_rejection(
                action=str(preview["action"]), actor=actor, source=source, reason="stale"
            )
            raise ReplyRuntimeControlError("reply runtime confirmation became stale")
        try:
            return await self._execute(
                preview["action"], preview["payload"], actor=actor, source=source, state=state
            )
        except (ReplyRuntimeControlError, ValueError):
            await self._record_rejection(
                action=str(preview["action"]), actor=actor, source=source, reason="policy_rejected"
            )
            raise

    async def owner_action(self, *, actor_qq: str, action: str, payload: dict[str, Any]) -> dict:
        if actor_qq != self.owner_qq:
            await self._record_rejection(
                action=action, actor=actor_qq, source="owner_qq", reason="not_owner"
            )
            raise ReplyRuntimeControlError("only the current instance owner may control replies")
        state = await self._repository.ensure_reply_runtime_state(self.bot_qq)
        try:
            return await self._execute(
                action, payload, actor=actor_qq, source="owner_qq", state=state
            )
        except (ReplyRuntimeControlError, ValueError):
            await self._record_rejection(
                action=action, actor=actor_qq, source="owner_qq", reason="policy_rejected"
            )
            raise

    async def _record_rejection(self, *, action: str, actor: str, source: str, reason: str) -> None:
        await self._repository.record_reply_runtime_event(
            bot_qq=self.bot_qq,
            event_type="transition_rejected",
            actor=actor,
            source=source,
            details={"action": action, "reason": reason},
        )

    async def _execute(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        actor: str,
        source: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        revision = int(state["revision"])
        gates = await self._gates()
        if action == "set_mode":
            mode = ReplyActivationMode(str(payload["mode"]))
            if MODE_ORDER[mode] > MODE_ORDER[gates.max_mode]:
                raise ReplyRuntimeControlError("requested mode exceeds configuration ceiling")
            updated = await self._repository.compare_and_set_reply_runtime(
                bot_qq=self.bot_qq,
                expected_revision=revision,
                requested_mode=mode,
                actor=actor,
                source=source,
                event_type="mode_changed",
                details={"mode": mode.value},
            )
        elif action in {"emergency_stop", "resume"}:
            paused = action == "emergency_stop"
            if self._outbox_control is not None:
                self._outbox_control.pause() if paused else self._outbox_control.resume()
            updated = await self._repository.compare_and_set_reply_runtime(
                bot_qq=self.bot_qq,
                expected_revision=revision,
                emergency_paused=paused,
                actor=actor,
                source=source,
                event_type=action,
                details={"paused": paused},
            )
        elif action == "set_eligibility":
            item = await self._repository.set_reply_eligibility(
                bot_qq=self.bot_qq,
                conversation_key=str(payload["conversation_key"]),
                enabled=bool(payload["enabled"]),
                expected_revision=payload.get("expected_revision"),
                actor=actor,
                source=source,
            )
            if item is None:
                raise ReplyRuntimeControlError("eligibility revision conflict")
            return {"eligibility": item, **await self.status()}
        elif action in {"decide_approval", "cancel_approval"}:
            approval_id = str(payload["approval_id"])
            existing = await self._repository.reply_runtime_approval(approval_id)
            if existing is None or existing["status"] != "pending":
                raise ReplyRuntimeControlError("approval is missing or already decided")
            run = await self._repository.reply_run_detail(str(existing["run_id"]))
            if run is None or run["stage"] != ReplyRunStage.AWAITING_APPROVAL.value:
                raise ReplyRuntimeControlError("approval run is no longer awaiting approval")
            if (
                not run.get("reply_plan")
                or self._repository.plan_hash(run["reply_plan"]) != existing["plan_hash"]
                or int(existing["runtime_revision"]) != revision
            ):
                raise ReplyRuntimeControlError("approval plan or runtime revision became stale")
            cancel = action == "cancel_approval"
            approve = bool(payload.get("approve")) and not cancel
            if approve:
                decision = evaluate_reply_runtime(state, gates)
                if (
                    decision.effective_mode is not ReplyActivationMode.OWNER_APPROVED
                    or not decision.delivery_allowed
                ):
                    raise ReplyRuntimeControlError("delivery gates changed before approval")
            item = await self._repository.decide_reply_runtime_approval(
                approval_id=approval_id,
                approve=approve,
                actor=actor,
                cancel=cancel,
            )
            if item is None:
                raise ReplyRuntimeControlError("approval is expired, stale, or already decided")
            await self._repository.record_reply_runtime_event(
                bot_qq=self.bot_qq,
                event_type=(
                    "approval_cancelled"
                    if cancel
                    else ("approval_approved" if approve else "approval_rejected")
                ),
                actor=actor,
                source=source,
                details={"approval_id": approval_id, "run_id": str(item["run_id"])},
            )
            lease_token = str(uuid4())
            acquired = await self._repository.acquire_reply_run_lease(
                run_id=str(item["run_id"]),
                lease_owner=f"approval:{actor}",
                lease_token=lease_token,
                ttl_seconds=60,
            )
            if not acquired:
                raise ReplyRuntimeControlError("approval run lease could not be acquired")
            if approve:
                transitioned = await self._repository.transition_reply_run(
                    run_id=str(item["run_id"]),
                    expected_stage=ReplyRunStage.AWAITING_APPROVAL,
                    target_stage=ReplyRunStage.CREATING_OUTBOX,
                    lease_token=lease_token,
                )
                if not transitioned:
                    raise ReplyRuntimeControlError("approved run could not advance")
                await self._repository.handoff_reply_plan_to_outbox(
                    run_id=str(item["run_id"]), lease_token=lease_token
                )
            else:
                transitioned = await self._repository.transition_reply_run(
                    run_id=str(item["run_id"]),
                    expected_stage=ReplyRunStage.AWAITING_APPROVAL,
                    target_stage=ReplyRunStage.SUPPRESSED,
                    failure=ReplyFailure(
                        code="owner_cancelled" if cancel else "owner_rejected",
                        category=ReplyFailureCategory.POLICY_DENIED,
                        retryable=False,
                        safe_detail=(
                            "reply plan was cancelled by the instance owner"
                            if cancel
                            else "reply plan was rejected by the instance owner"
                        ),
                    ),
                    lease_token=lease_token,
                )
                if not transitioned:
                    raise ReplyRuntimeControlError("rejected run could not be suppressed")
            return {"approval": item, **await self.status()}
        else:
            raise ReplyRuntimeControlError("unknown reply runtime action")
        if updated is None:
            raise ReplyRuntimeControlError("reply runtime revision conflict")
        return await self.status()

    @staticmethod
    def _evidence_hash(
        state: dict[str, Any], gates: ReplyRuntimeGates, action: str, payload: dict[str, Any]
    ) -> str:
        body = json.dumps(
            {
                "revision": state["revision"],
                "paused": bool(state["emergency_paused"]),
                "gates": asdict(gates),
                "action": action,
                "payload": payload,
            },
            default=str,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProductionReplyRuntimeResult:
    status: str
    run_id: str | None = None
    stage: str | None = None
    recovered_lease: bool = False


class ProductionReplyRuntimeService:
    def __init__(
        self,
        repository: Any,
        *,
        bot_qq: str,
        owner_qq: str,
        control: ReplyRuntimeControlService,
        generation_worker: Any,
        planner: Any,
        outbox: Any,
        approval_ttl_seconds: int = 1800,
    ) -> None:
        self._repository = repository
        self.bot_qq = bot_qq
        self.owner_qq = owner_qq
        self.control = control
        self._generation_worker = generation_worker
        self._planner = planner
        self._outbox = outbox
        self._approval_ttl_seconds = approval_ttl_seconds

    async def before_model(self, run_id: str, lease_token: str) -> bool:
        status = await self.control.status()
        mode = ReplyActivationMode(status["effective_mode"])
        if not mode.allows_model:
            return False
        if mode not in {
            ReplyActivationMode.SHADOW,
            ReplyActivationMode.OWNER_APPROVED,
            ReplyActivationMode.LIMITED_AUTO,
            ReplyActivationMode.AUTO,
        }:
            return True
        detail = await self._repository.reply_run_detail(run_id)
        if detail is None:
            return False
        conversation_key = str(detail["conversation_key"])
        if mode is ReplyActivationMode.AUTO:
            allowed = is_production_auto_conversation(conversation_key, self.bot_qq)
            safe_detail = "auto model calls are limited to this bot's conversations"
        else:
            allowed = conversation_key == owner_private_conversation_key(self.bot_qq, self.owner_qq)
            safe_detail = "live model calls are limited to the owner private conversation"
        if allowed:
            if _is_group_conversation_key(conversation_key) and not _triggers_mention_bot(
                detail, self.bot_qq
            ):
                await self._repository.transition_reply_run(
                    run_id=run_id,
                    expected_stage=ReplyRunStage.ASSEMBLING_CONTEXT,
                    target_stage=ReplyRunStage.SUPPRESSED,
                    failure=ReplyFailure(
                        code=ReplyRuntimeFailureCode.GROUP_NOT_MENTIONED.value,
                        category=ReplyFailureCategory.POLICY_DENIED,
                        retryable=False,
                        safe_detail="group auto replies require mentioning the bot",
                    ),
                    lease_token=lease_token,
                    now=datetime.now(UTC),
                )
                return False
            return True
        await self._repository.transition_reply_run(
            run_id=run_id,
            expected_stage=ReplyRunStage.ASSEMBLING_CONTEXT,
            target_stage=ReplyRunStage.SUPPRESSED,
            failure=ReplyFailure(
                code=ReplyRuntimeFailureCode.CONVERSATION_INELIGIBLE.value,
                category=ReplyFailureCategory.POLICY_DENIED,
                retryable=False,
                safe_detail=safe_detail,
            ),
            lease_token=lease_token,
            now=datetime.now(UTC),
        )
        return False

    async def run_once(
        self, *, worker_id: str, now: datetime | None = None
    ) -> ProductionReplyRuntimeResult:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        await self._expire_approvals(current)
        status = await self.control.status()
        mode = ReplyActivationMode(status["effective_mode"])
        if not mode.allows_model:
            return ProductionReplyRuntimeResult("paused")
        generated = await self._generation_worker.run_once(
            worker_id=worker_id, owner_qq=self.owner_qq, bot_qq=self.bot_qq, now=current
        )
        if generated.status != "generated" or generated.run_id is None:
            return ProductionReplyRuntimeResult(
                generated.status,
                generated.run_id,
                generated.stage,
                bool(getattr(generated, "recovered_lease", False)),
            )
        detail = await self._repository.reply_run_detail(generated.run_id)
        if detail is None or detail["lease"] is None:
            raise ReplyRuntimeControlError("generated reply lost durable lease evidence")
        lease_token = str(detail["lease"]["lease_token"])
        status = await self.control.status()
        mode = ReplyActivationMode(status["effective_mode"])
        eligible = await self._repository.conversation_reply_eligible(
            self.bot_qq, str(detail["conversation_key"])
        )
        if mode is ReplyActivationMode.SHADOW or (
            mode is ReplyActivationMode.LIMITED_AUTO and not eligible
        ):
            await self._planner.plan(
                run_id=generated.run_id,
                lease_token=lease_token,
                target_stage=ReplyRunStage.SHADOW_COMPLETED,
                now=current,
            )
            return ProductionReplyRuntimeResult(
                "shadow_completed",
                generated.run_id,
                ReplyRunStage.SHADOW_COMPLETED.value,
                generated.recovered_lease,
            )
        if mode is ReplyActivationMode.OWNER_APPROVED:
            plan = await self._planner.plan(
                run_id=generated.run_id,
                lease_token=lease_token,
                target_stage=ReplyRunStage.AWAITING_APPROVAL,
                now=current,
            )
            approval = await self._repository.create_reply_runtime_approval(
                run_id=generated.run_id,
                plan_hash=self._repository.plan_hash(asdict(plan)),
                runtime_revision=int(status["state"]["revision"]),
                requested_by="reply_runtime",
                ttl_seconds=self._approval_ttl_seconds,
                now=current,
            )
            await self._repository.record_reply_runtime_event(
                bot_qq=self.bot_qq,
                event_type="approval_requested",
                actor="reply_runtime",
                source="worker",
                details={"approval_id": str(approval["id"]), "run_id": generated.run_id},
                now=current,
            )
            await self._repository.release_reply_run_lease(
                run_id=generated.run_id, lease_token=lease_token
            )
            return ProductionReplyRuntimeResult(
                "awaiting_approval",
                generated.run_id,
                ReplyRunStage.AWAITING_APPROVAL.value,
                generated.recovered_lease,
            )
        await self._planner.plan(
            run_id=generated.run_id,
            lease_token=lease_token,
            target_stage=ReplyRunStage.CREATING_OUTBOX,
            now=current,
        )
        final_status = await self.control.status()
        final_mode = ReplyActivationMode(final_status["effective_mode"])
        if not final_mode.allows_delivery or int(final_status["state"]["revision"]) != int(
            status["state"]["revision"]
        ):
            await self._repository.transition_reply_run(
                run_id=generated.run_id,
                expected_stage=ReplyRunStage.CREATING_OUTBOX,
                target_stage=ReplyRunStage.FAILED,
                failure=ReplyFailure(
                    code="delivery_gate_blocked",
                    category=ReplyFailureCategory.POLICY_DENIED,
                    retryable=False,
                    safe_detail="delivery gates changed before outbox handoff",
                ),
                lease_token=lease_token,
                now=current,
            )
            return ProductionReplyRuntimeResult(
                "delivery_blocked",
                generated.run_id,
                ReplyRunStage.FAILED.value,
                generated.recovered_lease,
            )
        await self._outbox.handoff(run_id=generated.run_id, lease_token=lease_token, now=current)
        return ProductionReplyRuntimeResult(
            "queued",
            generated.run_id,
            ReplyRunStage.AWAITING_DELIVERY.value,
            generated.recovered_lease,
        )

    async def _expire_approvals(self, now: datetime) -> None:
        approvals = await self._repository.reply_runtime_approvals(self.bot_qq, status="pending")
        for approval in approvals:
            if datetime.fromisoformat(str(approval["expires_at"])).astimezone(UTC) > now:
                continue
            await self._repository.decide_reply_runtime_approval(
                approval_id=str(approval["id"]),
                approve=False,
                actor="system_expiry",
                now=now,
            )
            lease_token = str(uuid4())
            acquired = await self._repository.acquire_reply_run_lease(
                run_id=str(approval["run_id"]),
                lease_owner="approval-expiry",
                lease_token=lease_token,
                ttl_seconds=60,
                now=now,
            )
            if not acquired:
                continue
            await self._repository.transition_reply_run(
                run_id=str(approval["run_id"]),
                expected_stage=ReplyRunStage.AWAITING_APPROVAL,
                target_stage=ReplyRunStage.SUPPRESSED,
                failure=ReplyFailure(
                    code="approval_expired",
                    category=ReplyFailureCategory.POLICY_DENIED,
                    retryable=False,
                    safe_detail="owner approval expired before delivery",
                ),
                lease_token=lease_token,
                now=now,
            )
            await self._repository.record_reply_runtime_event(
                bot_qq=self.bot_qq,
                event_type="approval_expired",
                actor="system_expiry",
                source="worker",
                details={"approval_id": str(approval["id"]), "run_id": str(approval["run_id"])},
                now=now,
            )
