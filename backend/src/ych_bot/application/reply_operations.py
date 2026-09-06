"""Sanitized, read-only operations views for the durable reply pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ych_bot.domain.reply_pipeline import ReplyRunStage

TERMINAL_STAGES = frozenset({"shadow_completed", "completed", "suppressed", "failed", "cancelled"})
FAILURE_SUMMARIES = {
    "unsupported_input": "The trigger could not be interpreted safely.",
    "policy_denied": "Reply policy denied this run.",
    "context": "Context assembly failed safely.",
    "model_unavailable": "The chat model was unavailable.",
    "model_timeout": "The chat model timed out.",
    "model_response": "The model response was invalid.",
    "outbox": "The reply plan could not enter the local outbox.",
    "delivery_rejected": "NapCat confirmed that delivery was rejected.",
    "delivery_unknown": "Delivery could not be confirmed and was quarantined.",
    "internal": "The run failed without exposing internal details.",
}


@dataclass(frozen=True, slots=True)
class ReplyPipelineFlags:
    ingestion_enabled: bool
    chat_model_configured: bool
    model_network_enabled: bool
    chat_route_enabled: bool
    outbound_enabled: bool
    onebot_token_configured: bool
    reply_worker_wired: bool = False
    reply_worker_enabled: bool = False


class ReplyOperationsService:
    def __init__(self, repository: Any, *, flags: ReplyPipelineFlags) -> None:
        self._repository = repository
        self._flags = flags

    async def list_runs(
        self,
        *,
        stage: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if stage is not None and stage not in {item.value for item in ReplyRunStage}:
            raise ValueError("unknown reply run stage")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        result = await self._repository.list_reply_runs(
            stage=stage,
            limit=limit,
            offset=offset,
        )
        return {
            **{key: result[key] for key in ("total", "limit", "offset")},
            "items": [self._run_summary(item) for item in result["items"]],
        }

    async def detail(self, run_id: str) -> dict[str, Any] | None:
        run_id = run_id.strip()
        if not run_id or len(run_id) > 128:
            raise ValueError("run_id must contain between 1 and 128 characters")
        detail = await self._repository.reply_run_detail(run_id)
        if detail is None:
            return None
        inference = await self._repository.inference_run(f"reply:{run_id}")
        return {
            **self._run_summary(detail),
            "timestamps": {
                key: detail.get(key)
                for key in (
                    "created_at",
                    "updated_at",
                    "settled_until",
                    "started_at",
                    "completed_at",
                )
            },
            "triggers": [self._trigger(item) for item in detail["triggers"]],
            "policy": self._policy(detail),
            "context_manifests": [
                self._context_manifest(item) for item in detail["context_manifests"]
            ],
            "model": self._model_summary(inference),
            "reply_plan": self._plan_summary(detail.get("reply_plan")),
            "delivery": [self._delivery(item) for item in detail["delivery_evidence"]],
            "lease": self._lease_summary(detail.get("lease")),
        }

    async def readiness(
        self,
        *,
        onebot_connected: bool,
        outbound_worker: dict[str, Any],
        chat_protection: dict[str, Any],
        runtime_status: dict[str, Any] | None = None,
        runtime_worker: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        database_ok = bool(await self._repository.healthcheck())
        summary = await self._repository.reply_pipeline_summary()
        checks = {
            "database": database_ok,
            "ingestion": self._flags.ingestion_enabled,
            "reply_worker_wired": self._flags.reply_worker_wired,
            "reply_worker_enabled": self._flags.reply_worker_enabled,
            "chat_model_configured": self._flags.chat_model_configured,
            "model_network": self._flags.model_network_enabled,
            "chat_route": self._flags.chat_route_enabled,
            "outbound_gate": self._flags.outbound_enabled,
            "outbound_worker_active": bool(outbound_worker.get("active")),
            "onebot_token": self._flags.onebot_token_configured,
            "onebot_connected": onebot_connected,
            "chat_circuit_available": chat_protection.get("state") != "open",
        }
        blockers = [
            {"code": code, "stage": _blocker_stage(code)}
            for code, passed in checks.items()
            if not passed
        ]
        return {
            "mode": (
                str(runtime_status.get("effective_mode"))
                if runtime_status
                else ("activation_ready" if not blockers else "observe_only")
            ),
            "production_activation_ready": not blockers,
            "safe_observation_ready": database_ok,
            "checks": checks,
            "blockers": blockers,
            "safeguards": {
                "stable_bubble_idempotency": True,
                "context_isolation": True,
                "ambiguous_delivery_quarantine": True,
                "automatic_ambiguous_retry": False,
                "real_network_changed_by_this_endpoint": False,
            },
            "summary": summary,
            "runtime": (
                {
                    "requested_mode": runtime_status.get("requested_mode"),
                    "effective_mode": runtime_status.get("effective_mode"),
                    "configuration_ceiling": runtime_status.get("configuration_ceiling"),
                    "persistent_pause": bool(
                        runtime_status.get("state", {}).get("emergency_paused")
                    ),
                    "revision": runtime_status.get("state", {}).get("revision"),
                    "approval_backlog": runtime_status.get("approval_backlog", 0),
                    "eligibility_count": len(runtime_status.get("eligibility", [])),
                    "worker": runtime_worker or {},
                    "blockers": runtime_status.get("blockers", []),
                }
                if runtime_status
                else None
            ),
        }

    @staticmethod
    def _run_summary(item: dict[str, Any]) -> dict[str, Any]:
        category = item.get("failure_category")
        return {
            "id": item["id"],
            "bot_qq": item["bot_qq"],
            "conversation_kind": item["conversation_kind"],
            "peer_id": item.get("peer_id"),
            "subject_user_qq": item.get("subject_user_qq"),
            "stage": item["stage"],
            "terminal": item["stage"] in TERMINAL_STAGES,
            "attempt_count": int(item.get("attempt_count") or 0),
            "trigger_count": int(item.get("trigger_count") or len(item.get("triggers", []))),
            "manifest_count": int(
                item.get("manifest_count") or len(item.get("context_manifests", []))
            ),
            "latest_delivery_outcome": item.get("latest_delivery_outcome"),
            "failure": (
                {
                    "code": item.get("failure_code"),
                    "category": category,
                    "summary": FAILURE_SUMMARIES.get(
                        str(category), "The run ended with a sanitized failure."
                    ),
                }
                if category
                else None
            ),
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
        }

    @staticmethod
    def _trigger(item: dict[str, Any]) -> dict[str, Any]:
        text = str(item.get("plain_text") or "")
        preview = text[:240]
        return {
            "sequence": int(item["sequence"]),
            "message_id": item["message_id"],
            "sender_qq": item["sender_id"],
            "direction": item["direction"],
            "occurred_at": item["occurred_at"],
            "text_preview": preview,
            "text_truncated": len(text) > len(preview),
            "segment_types": [str(segment.get("type", "unknown")) for segment in item["segments"]],
        }

    @staticmethod
    def _policy(detail: dict[str, Any]) -> dict[str, Any]:
        snapshot = detail.get("policy_snapshot") or {}
        denied_sources = sorted(
            {
                str(section.get("source_class", "unknown"))
                for manifest in detail["context_manifests"]
                for section in manifest["manifest"].get("sections", [])
                if section.get("policy_decision") == "denied"
            }
        )
        return {
            "snapshot_keys": sorted(str(key) for key in snapshot),
            "blockers": [
                {"code": "context_source_denied", "source_class": source}
                for source in denied_sources
            ],
        }

    @staticmethod
    def _context_manifest(item: dict[str, Any]) -> dict[str, Any]:
        sections = item["manifest"].get("sections", [])
        return {
            "id": item["id"],
            "revision": int(item["revision"]),
            "budget_chars": item.get("budget_chars"),
            "used_chars": item.get("used_chars"),
            "rendered_sha256": item.get("rendered_sha256"),
            "created_at": item["created_at"],
            "sections": [
                {
                    "section_id": section.get("section_id"),
                    "source_class": section.get("source_class"),
                    "scope": section.get("scope"),
                    "subject_qq": section.get("subject_qq"),
                    "policy_decision": section.get("policy_decision"),
                    "record_ids": list(section.get("record_ids") or []),
                    "truncated": bool(section.get("truncated")),
                    "omitted_chars": int(section.get("omitted_chars") or 0),
                }
                for section in sections
            ],
        }

    @staticmethod
    def _model_summary(inference: dict[str, Any] | None) -> dict[str, Any] | None:
        if inference is None:
            return None
        candidate = inference.get("candidate")
        return {
            "status": inference["status"],
            "mode": inference["mode"],
            "model_route": inference["model_route"],
            "provider_request_id": inference.get("provider_request_id"),
            "input_tokens": inference.get("input_tokens"),
            "output_tokens": inference.get("output_tokens"),
            "error_type": inference.get("error_type"),
            "created_at": inference["created_at"],
            "completed_at": inference.get("completed_at"),
            "candidate": (
                {
                    "id": candidate["id"],
                    "status": candidate["status"],
                    "content_chars": len(str(candidate.get("content") or "")),
                    "safety_flags": list(candidate.get("safety_flags") or []),
                }
                if candidate
                else None
            ),
        }

    @staticmethod
    def _plan_summary(plan: dict[str, Any] | None) -> dict[str, Any] | None:
        if plan is None:
            return None
        bubbles = plan.get("bubbles") or []
        return {
            "bubble_count": len(bubbles),
            "bubbles": [
                {
                    "sequence": int(bubble["sequence"]),
                    "idempotency_key": bubble["idempotency_key"],
                    "segment_types": [segment["type"] for segment in bubble["segments"]],
                    "text_chars": sum(
                        len(str(segment.get("data", {}).get("text", "")))
                        for segment in bubble["segments"]
                        if segment.get("type") == "text"
                    ),
                }
                for bubble in bubbles
            ],
        }

    @staticmethod
    def _delivery(item: dict[str, Any]) -> dict[str, Any]:
        evidence = item.get("evidence") or {}
        return {
            "outbox_id": item["outbox_id"],
            "bubble_sequence": int(item["bubble_sequence"]),
            "attempt": int(item["attempt"]),
            "outcome": item["outcome"],
            "provider_message_id": item.get("provider_message_id"),
            "source": evidence.get("source"),
            "observed_at": item["observed_at"],
        }

    @staticmethod
    def _lease_summary(lease: dict[str, Any] | None) -> dict[str, Any]:
        if lease is None:
            return {"present": False, "expired": False}
        expires = datetime.fromisoformat(str(lease["expires_at"])).astimezone(UTC)
        return {
            "present": True,
            "owner": lease["lease_owner"],
            "acquired_at": lease["acquired_at"],
            "heartbeat_at": lease["heartbeat_at"],
            "expires_at": lease["expires_at"],
            "expired": expires <= datetime.now(UTC),
        }


def _blocker_stage(code: str) -> str:
    if code in {"database", "ingestion", "reply_worker_wired", "reply_worker_enabled"}:
        return "orchestration"
    if code in {
        "chat_model_configured",
        "model_network",
        "chat_route",
        "chat_circuit_available",
    }:
        return "model"
    return "delivery"
