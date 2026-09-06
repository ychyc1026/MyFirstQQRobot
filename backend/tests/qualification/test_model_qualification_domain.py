from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationCaseResult,
    QualificationCaseState,
    QualificationCeilings,
    QualificationCheckKind,
    QualificationCheckResult,
    QualificationDecision,
    QualificationDecisionStatus,
    QualificationEvidence,
    QualificationExecutionMode,
    QualificationFixture,
    QualificationHumanReviewNote,
    QualificationPreview,
    QualificationPreviewState,
    QualificationReasonCode,
    QualificationRouteRevision,
    QualificationRun,
    QualificationRunState,
    QualificationSuite,
    apply_human_review_note,
    can_confirm_controlled_live,
    can_transition_qualification_case,
    can_transition_qualification_preview,
    can_transition_qualification_run,
    derive_qualification_acceptance,
    evaluate_qualification_decision,
    reject_non_synthetic_qualification_input,
    sanitize_qualification_host,
)

NOW = datetime(2026, 9, 5, 5, 0, tzinfo=UTC)


def _suite(**overrides: object) -> QualificationSuite:
    values: dict[str, object] = {
        "suite_id": "chat-shadow-v1",
        "capability": QualificationCapability.CHAT,
        "version": "2026.09.05",
        "fixture_ids": ("chat-identity-001", "chat-style-001"),
        "blocking_check_codes": (
            "creator_identity",
            "isolation",
            "schema",
            "forbidden_field",
            "side_effect_isolation",
            "evidence_completeness",
        ),
        "advisory_checks": (("usefulness", Decimal("0.6")), ("style", Decimal("0.4"))),
        "minimum_advisory_score": Decimal("0.70"),
        "max_timeout_rate": Decimal("0.10"),
        "max_error_rate": Decimal("0.10"),
        "required_evidence_fields": ("latency_ms", "usage", "response_hash"),
    }
    values.update(overrides)
    return QualificationSuite(**values)  # type: ignore[arg-type]


def _fixture(**overrides: object) -> QualificationFixture:
    values: dict[str, object] = {
        "fixture_id": "chat-identity-001",
        "suite_id": "chat-shadow-v1",
        "suite_version": "2026.09.05",
        "capability": QualificationCapability.CHAT,
        "input_kind": "chat_text",
        "max_input_bytes": 2048,
        "max_output_bytes": 4096,
        "blocking_check_codes": ("creator_identity",),
        "advisory_rubric_codes": ("usefulness",),
    }
    values.update(overrides)
    return QualificationFixture(**values)  # type: ignore[arg-type]


def _revision(**overrides: object) -> QualificationRouteRevision:
    values: dict[str, object] = {
        "capability": QualificationCapability.CHAT,
        "provider_protocol": "openai_compatible",
        "sanitized_base_host": "api.siliconflow.cn",
        "model_identifier": "deepseek-ai/DeepSeek-V3.2",
        "generation_settings": (("temperature", "0.2"), ("max_output_tokens", "256")),
        "suite_version": "2026.09.05",
        "price_catalog_revision": "siliconflow-2026-09-05",
        "protection_policy_revision": "chat-protection-v1",
    }
    values.update(overrides)
    return QualificationRouteRevision(**values)  # type: ignore[arg-type]


def _ceilings(**overrides: object) -> QualificationCeilings:
    values: dict[str, object] = {
        "max_requests": 4,
        "max_input_tokens": 2000,
        "max_output_tokens": 1000,
        "max_images": None,
        "conservative_max_cost": Decimal("0.50"),
    }
    values.update(overrides)
    return QualificationCeilings(**values)  # type: ignore[arg-type]


def _preview(**overrides: object) -> QualificationPreview:
    values: dict[str, object] = {
        "preview_id": "preview-1",
        "confirmation_handle_hash": "a" * 64,
        "actor_id": "admin-session-1",
        "process_instance_id": "proc-1",
        "capability": QualificationCapability.CHAT,
        "route_revision": _revision(),
        "suite": _suite(),
        "policy_revision": "chat-protection-v1",
        "fixture_ids": ("chat-identity-001", "chat-style-001"),
        "ceilings": _ceilings(),
        "execution_mode": QualificationExecutionMode.CONTROLLED_LIVE,
        "expires_at": NOW + timedelta(minutes=10),
        "state": QualificationPreviewState.READY,
    }
    values.update(overrides)
    return QualificationPreview(**values)  # type: ignore[arg-type]


def _run(**overrides: object) -> QualificationRun:
    values: dict[str, object] = {
        "run_id": "run-1",
        "preview_id": "preview-1",
        "actor_id": "admin-session-1",
        "process_instance_id": "proc-1",
        "capability": QualificationCapability.CHAT,
        "route_revision": _revision(),
        "suite": _suite(),
        "execution_mode": QualificationExecutionMode.FAKE,
        "state": QualificationRunState.PREPARED,
        "idempotency_key": "admin-session-1:chat:2026.09.05:fingerprint",
        "created_at": NOW,
    }
    values.update(overrides)
    return QualificationRun(**values)  # type: ignore[arg-type]


def test_four_capabilities_are_independent_and_match_model_routes() -> None:
    assert tuple(QualificationCapability) == (
        QualificationCapability.CHAT,
        QualificationCapability.VISION,
        QualificationCapability.IMAGE,
        QualificationCapability.STATS,
    )
    assert {item.value for item in QualificationCapability} == {
        "chat",
        "vision",
        "image",
        "stats",
    }


def test_suite_is_capability_specific_and_versioned() -> None:
    suite = _suite()
    assert suite.capability is QualificationCapability.CHAT
    assert suite.version == "2026.09.05"
    assert suite.fixture_ids == ("chat-identity-001", "chat-style-001")
    with pytest.raises(ValueError, match="capability"):
        _suite(capability=QualificationCapability.VISION)


def test_fixture_is_synthetic_public_and_rejects_production_sources() -> None:
    fixture = _fixture()
    assert fixture.sensitivity == "synthetic_public"
    assert fixture.source_kind == "repository_owned"
    with pytest.raises(ValueError, match="synthetic"):
        reject_non_synthetic_qualification_input(
            {
                "message_id": "stored-qq-message-1",
                "user_qq": "123456789",
                "path": "C:/Users/YCH/imports/diary.txt",
                "uploaded_document": "upload-1",
            }
        )


def test_route_revision_binds_exact_route_and_rejects_secrets() -> None:
    revision = _revision()
    assert revision.fingerprint
    assert "api.siliconflow.cn" in revision.fingerprint or revision.sanitized_base_host == (
        "api.siliconflow.cn"
    )
    changed = _revision(model_identifier="other-model")
    assert changed.fingerprint != revision.fingerprint
    with pytest.raises(ValueError, match="secret|credential|authorization"):
        _revision(generation_settings=(("api_key", "sk-secret"),))
    with pytest.raises(ValueError, match="host"):
        sanitize_qualification_host("https://user:sk-secret@api.siliconflow.cn/v1")


def test_preview_stores_handle_hash_and_requires_bounded_live_plan() -> None:
    preview = _preview()
    assert preview.confirmation_handle_hash == "a" * 64
    assert not hasattr(preview, "confirmation_handle")
    assert preview.fixture_count == 2
    assert can_confirm_controlled_live(preview, price_evidence_present=True, now=NOW)
    assert not can_confirm_controlled_live(preview, price_evidence_present=False, now=NOW)
    unbounded = _preview(ceilings=_ceilings(conservative_max_cost=None, max_requests=0))
    assert not can_confirm_controlled_live(unbounded, price_evidence_present=True, now=NOW)


def test_preview_expires_closed_after_wall_clock_sleep() -> None:
    preview = _preview(expires_at=NOW + timedelta(minutes=5))
    assert preview.state_at(NOW) is QualificationPreviewState.READY
    assert preview.state_at(NOW + timedelta(hours=2)) is QualificationPreviewState.EXPIRED
    assert not can_confirm_controlled_live(
        preview, price_evidence_present=True, now=NOW + timedelta(hours=2)
    )


def test_run_and_case_transitions_are_explicit() -> None:
    assert can_transition_qualification_run(
        QualificationRunState.PREPARED, QualificationRunState.RUNNING
    )
    assert can_transition_qualification_run(
        QualificationRunState.PREPARED, QualificationRunState.BLOCKED
    )
    assert can_transition_qualification_run(
        QualificationRunState.PREPARED, QualificationRunState.CANCELLED
    )
    assert can_transition_qualification_run(
        QualificationRunState.RUNNING, QualificationRunState.PASSED
    )
    assert can_transition_qualification_run(
        QualificationRunState.RUNNING, QualificationRunState.INCONCLUSIVE
    )
    assert not can_transition_qualification_run(
        QualificationRunState.PASSED, QualificationRunState.RUNNING
    )
    assert can_transition_qualification_case(
        QualificationCaseState.PENDING, QualificationCaseState.RUNNING
    )
    assert can_transition_qualification_case(
        QualificationCaseState.RUNNING, QualificationCaseState.INCONCLUSIVE
    )
    assert can_transition_qualification_preview(
        QualificationPreviewState.READY, QualificationPreviewState.CONSUMED
    )
    assert not can_transition_qualification_preview(
        QualificationPreviewState.CONSUMED, QualificationPreviewState.READY
    )


def test_default_decision_is_unqualified_and_not_production_enabled() -> None:
    decision = evaluate_qualification_decision(
        capability=QualificationCapability.CHAT,
        route_revision=_revision(),
        suite=_suite(),
        run=None,
        case_results=(),
    )
    assert decision.status is QualificationDecisionStatus.UNQUALIFIED
    assert decision.qualifies is False
    assert decision.activates_production is False
    assert _run().state is QualificationRunState.PREPARED
    assert _preview().state is QualificationPreviewState.READY


def test_identity_blocker_cannot_be_overridden_by_advisory_or_human_note() -> None:
    blocking = QualificationCheckResult(
        check_code="creator_identity",
        kind=QualificationCheckKind.BLOCKING,
        passed=False,
        reason_code=QualificationReasonCode.IDENTITY_VIOLATION,
    )
    advisory = QualificationCheckResult(
        check_code="usefulness",
        kind=QualificationCheckKind.ADVISORY,
        passed=True,
        score=Decimal("1.00"),
    )
    acceptance = derive_qualification_acceptance(
        suite=_suite(),
        check_results=(blocking, advisory),
        timeout_rate=Decimal("0"),
        error_rate=Decimal("0"),
    )
    assert acceptance is QualificationRunState.FAILED
    decision = QualificationDecision(
        decision_id="decision-1",
        capability=QualificationCapability.CHAT,
        route_revision=_revision(),
        suite_version="2026.09.05",
        status=QualificationDecisionStatus.FAILED,
        run_id="run-1",
        evaluated_at=NOW,
        blocker_codes=(QualificationReasonCode.IDENTITY_VIOLATION,),
        advisory_score=Decimal("1.00"),
    )
    noted = apply_human_review_note(
        decision,
        QualificationHumanReviewNote(
            note_id="note-1",
            run_id="run-1",
            actor_id="admin-session-1",
            created_at=NOW,
            safe_summary="style looks good",
        ),
    )
    assert noted.status is QualificationDecisionStatus.FAILED
    assert noted.blocker_codes == (QualificationReasonCode.IDENTITY_VIOLATION,)


def test_low_advisory_score_does_not_pass_even_when_blockers_succeed() -> None:
    acceptance = derive_qualification_acceptance(
        suite=_suite(minimum_advisory_score=Decimal("0.80")),
        check_results=(
            QualificationCheckResult(
                "creator_identity", QualificationCheckKind.BLOCKING, passed=True
            ),
            QualificationCheckResult("isolation", QualificationCheckKind.BLOCKING, passed=True),
            QualificationCheckResult("schema", QualificationCheckKind.BLOCKING, passed=True),
            QualificationCheckResult(
                "forbidden_field", QualificationCheckKind.BLOCKING, passed=True
            ),
            QualificationCheckResult(
                "side_effect_isolation", QualificationCheckKind.BLOCKING, passed=True
            ),
            QualificationCheckResult(
                "evidence_completeness", QualificationCheckKind.BLOCKING, passed=True
            ),
            QualificationCheckResult(
                "usefulness",
                QualificationCheckKind.ADVISORY,
                passed=True,
                score=Decimal("0.50"),
            ),
            QualificationCheckResult(
                "style",
                QualificationCheckKind.ADVISORY,
                passed=True,
                score=Decimal("0.40"),
            ),
        ),
        timeout_rate=Decimal("0"),
        error_rate=Decimal("0"),
    )
    assert acceptance is QualificationRunState.FAILED


def test_stale_revision_does_not_qualify_the_new_route() -> None:
    passed = QualificationDecision(
        decision_id="decision-1",
        capability=QualificationCapability.CHAT,
        route_revision=_revision(),
        suite_version="2026.09.05",
        status=QualificationDecisionStatus.PASSED,
        run_id="run-1",
        evaluated_at=NOW,
        advisory_score=Decimal("0.90"),
    )
    assert passed.qualifies is True
    stale = passed.against_current(_revision(model_identifier="new-model"))
    assert stale.status is QualificationDecisionStatus.STALE
    assert stale.qualifies is False
    assert stale.activates_production is False


def test_one_capability_pass_does_not_qualify_other_routes() -> None:
    chat_pass = QualificationDecision(
        decision_id="decision-chat",
        capability=QualificationCapability.CHAT,
        route_revision=_revision(),
        suite_version="2026.09.05",
        status=QualificationDecisionStatus.PASSED,
        run_id="run-chat",
        evaluated_at=NOW,
        advisory_score=Decimal("0.90"),
    )
    vision = evaluate_qualification_decision(
        capability=QualificationCapability.VISION,
        route_revision=_revision(capability=QualificationCapability.VISION),
        suite=_suite(
            suite_id="vision-shadow-v1",
            capability=QualificationCapability.VISION,
            fixture_ids=("vision-ground-001",),
        ),
        run=None,
        case_results=(),
    )
    assert chat_pass.qualifies is True
    assert vision.status is QualificationDecisionStatus.UNQUALIFIED
    assert vision.capability is QualificationCapability.VISION


def test_case_result_and_evidence_are_allowlisted() -> None:
    case = QualificationCaseResult(
        case_id="case-1",
        run_id="run-1",
        fixture_id="chat-identity-001",
        fixture_hash="b" * 64,
        input_hash="c" * 64,
        state=QualificationCaseState.PASSED,
        idempotency_key="run-1:chat-identity-001",
        check_results=(
            QualificationCheckResult(
                "creator_identity", QualificationCheckKind.BLOCKING, passed=True
            ),
        ),
        evidence=QualificationEvidence(
            latency_ms=120,
            input_tokens=11,
            output_tokens=4,
            image_count=0,
            cost_amount=Decimal("0.01"),
            cost_currency="CNY",
            provider_request_id="req-1",
            response_hash="d" * 64,
            artifact_id=None,
        ),
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
    )
    payload = case.as_sanitized_dict()
    assert "prompt" not in payload
    assert "response" not in payload
    assert "authorization" not in payload
    assert payload["fixture_hash"] == "b" * 64
    assert "creator_identity" in str(payload["check_results"])


def test_reason_codes_are_stable_and_sanitized() -> None:
    codes = {item.value for item in QualificationReasonCode}
    assert "identity_violation" in codes
    assert "cost_unbounded" in codes
    assert "confirmation_reused" in codes
    assert "side_effect_isolation" in codes
    assert "preview_expired" in codes
    assert all("_" in item.value or item.value.isascii() for item in QualificationReasonCode)
    assert all(" " not in item.value for item in QualificationReasonCode)
