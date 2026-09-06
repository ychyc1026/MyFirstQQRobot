"""Persist a validated, deterministic reply plan without creating outbox items."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ych_bot.domain.reply_pipeline import (
    ReplyFailure,
    ReplyFailureCategory,
    ReplyPlan,
    ReplyRunStage,
)
from ych_bot.domain.reply_planning import (
    ReplyPlanningError,
    build_reply_plan,
    restore_reply_plan,
)
from ych_bot.domain.reply_style import policy_for_reply_run


class ReplyPlanServiceError(RuntimeError):
    """A reply run cannot safely create or recover its bubble plan."""


class ReplyPlanService:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    async def plan(
        self,
        *,
        run_id: str,
        lease_token: str,
        now: datetime | None = None,
        target_stage: ReplyRunStage = ReplyRunStage.CREATING_OUTBOX,
    ) -> ReplyPlan:
        if target_stage not in {
            ReplyRunStage.CREATING_OUTBOX,
            ReplyRunStage.AWAITING_APPROVAL,
            ReplyRunStage.SHADOW_COMPLETED,
        }:
            raise ValueError("reply plan target stage is unsupported")
        current_time = (now or datetime.now(UTC)).astimezone(UTC)
        detail = await self._repository.reply_run_detail(run_id)
        if detail is None:
            raise ReplyPlanServiceError("reply run not found")
        if detail["stage"] not in {
            ReplyRunStage.PLANNING_REPLY.value,
            ReplyRunStage.CREATING_OUTBOX.value,
            ReplyRunStage.AWAITING_APPROVAL.value,
            ReplyRunStage.SHADOW_COMPLETED.value,
        }:
            raise ReplyPlanServiceError("reply run is not ready for planning")
        lease = detail["lease"]
        if not lease or lease["lease_token"] != lease_token:
            raise ReplyPlanServiceError("reply run lease does not match")
        if datetime.fromisoformat(lease["expires_at"]).astimezone(UTC) <= current_time:
            raise ReplyPlanServiceError("reply run lease has expired")
        if detail["stage"] != ReplyRunStage.PLANNING_REPLY.value and detail["reply_plan"]:
            return restore_reply_plan(detail["reply_plan"])

        inference = await self._repository.inference_run(f"reply:{run_id}")
        candidate = inference.get("candidate") if inference else None
        if (
            not inference
            or inference["status"] != "completed"
            or not candidate
            or candidate["status"] != "shadow"
            or "not_for_delivery" not in candidate["safety_flags"]
        ):
            return await self._fail_invalid_candidate(
                run_id,
                lease_token,
                current_time,
                "reply candidate evidence is missing or ineligible",
            )
        subject_qq = str(detail["subject_user_qq"] or detail["triggers"][0]["sender_id"])
        stored_policy = (
            await self._repository.reply_style_policy(subject_qq)
            if detail["conversation_kind"] == "private"
            else None
        )
        policy = policy_for_reply_run(
            user_qq=subject_qq,
            conversation_kind=str(detail["conversation_kind"]),
            stored_policy=stored_policy,
            safety_flags=tuple(candidate.get("safety_flags") or ()),
        )
        try:
            plan = build_reply_plan(
                run_id=run_id,
                content=str(candidate["content"]),
                policy=policy,
            )
        except (ReplyPlanningError, ValueError) as exc:
            return await self._fail_invalid_candidate(
                run_id,
                lease_token,
                current_time,
                str(exc),
            )
        transitioned = await self._repository.transition_reply_run(
            run_id=run_id,
            expected_stage=ReplyRunStage.PLANNING_REPLY,
            target_stage=target_stage,
            reply_plan=plan,
            lease_token=lease_token,
            now=current_time,
        )
        if not transitioned:
            raise ReplyPlanServiceError("reply run lost its lease while storing the plan")
        return plan

    async def _fail_invalid_candidate(
        self,
        run_id: str,
        lease_token: str,
        now: datetime,
        reason: str,
    ) -> ReplyPlan:
        failure = ReplyFailure(
            code="invalid_reply_candidate",
            category=ReplyFailureCategory.MODEL_RESPONSE,
            retryable=False,
            safe_detail="reply candidate could not be normalized into bounded bubbles",
        )
        transitioned = await self._repository.transition_reply_run(
            run_id=run_id,
            expected_stage=ReplyRunStage.PLANNING_REPLY,
            target_stage=ReplyRunStage.FAILED,
            failure=failure,
            lease_token=lease_token,
            now=now,
        )
        if not transitioned:
            raise ReplyPlanServiceError("invalid reply candidate failure could not be stored")
        raise ReplyPlanServiceError(reason)
