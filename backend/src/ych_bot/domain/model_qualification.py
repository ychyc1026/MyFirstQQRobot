"""Typed contracts for controlled model-route qualification."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from .readiness import require_aware_utc

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_SETTING_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
        "access_token",
    }
)
_FORBIDDEN_INPUT_KEYS = frozenset(
    {
        "diary",
        "filesystem_path",
        "history_id",
        "memory",
        "message_id",
        "path",
        "persona",
        "qq",
        "qzone",
        "uploaded_document",
        "user_document",
        "user_qq",
    }
)


class QualificationCapability(StrEnum):
    CHAT = "chat"
    VISION = "vision"
    IMAGE = "image"
    STATS = "stats"


class QualificationExecutionMode(StrEnum):
    FAKE = "fake"
    CONTROLLED_LIVE = "controlled_live"


class QualificationPreviewState(StrEnum):
    READY = "ready"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self is not QualificationPreviewState.READY


class QualificationRunState(StrEnum):
    PREPARED = "prepared"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_QUALIFICATION_RUN_STATES


class QualificationCaseState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    INCONCLUSIVE = "inconclusive"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_QUALIFICATION_CASE_STATES


class QualificationDecisionStatus(StrEnum):
    UNQUALIFIED = "unqualified"
    PASSED = "passed"
    FAILED = "failed"
    STALE = "stale"
    BLOCKED = "blocked"


class QualificationCheckKind(StrEnum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"


class QualificationReasonCode(StrEnum):
    IDENTITY_VIOLATION = "identity_violation"
    ISOLATION_VIOLATION = "isolation_violation"
    SCHEMA_INVALID = "schema_invalid"
    FORBIDDEN_FIELD = "forbidden_field"
    ARTIFACT_UNSAFE = "artifact_unsafe"
    SIDE_EFFECT_ISOLATION = "side_effect_isolation"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    ADVISORY_THRESHOLD_UNMET = "advisory_threshold_unmet"
    COST_UNBOUNDED = "cost_unbounded"
    CONFIRMATION_REUSED = "confirmation_reused"
    CONFIRMATION_ACTOR_MISMATCH = "confirmation_actor_mismatch"
    CONFIRMATION_PROCESS_MISMATCH = "confirmation_process_mismatch"
    CONFIRMATION_SCOPE_MISMATCH = "confirmation_scope_mismatch"
    PREVIEW_EXPIRED = "preview_expired"
    LEASE_EXPIRED = "lease_expired"
    STALE_ROUTE_REVISION = "stale_route_revision"
    STALE_SUITE_VERSION = "stale_suite_version"
    STALE_POLICY_REVISION = "stale_policy_revision"
    STALE_PRICE_CATALOG = "stale_price_catalog"
    EMERGENCY_PAUSE = "emergency_pause"
    QUOTA_EXHAUSTED = "quota_exhausted"
    CIRCUIT_OPEN = "circuit_open"
    READINESS_STALE = "readiness_stale"
    CEILING_EXHAUSTED = "ceiling_exhausted"
    COMPATIBILITY_UNSUPPORTED = "compatibility_unsupported"
    TIMEOUT = "timeout"
    RETRYABLE_PROVIDER_ERROR = "retryable_provider_error"
    INVALID_RESPONSE = "invalid_response"
    AMBIGUOUS_INTERRUPTION = "ambiguous_interruption"
    PRODUCTION_INPUT_REJECTED = "production_input_rejected"
    DEFAULT_DENIED = "default_denied"
    CANCELLED = "cancelled"


TERMINAL_QUALIFICATION_RUN_STATES = frozenset(
    {
        QualificationRunState.PASSED,
        QualificationRunState.FAILED,
        QualificationRunState.INCONCLUSIVE,
        QualificationRunState.BLOCKED,
        QualificationRunState.CANCELLED,
    }
)

TERMINAL_QUALIFICATION_CASE_STATES = frozenset(
    {
        QualificationCaseState.PASSED,
        QualificationCaseState.FAILED,
        QualificationCaseState.BLOCKED,
        QualificationCaseState.CANCELLED,
        QualificationCaseState.INCONCLUSIVE,
    }
)

QUALIFICATION_RUN_TRANSITIONS: dict[QualificationRunState, frozenset[QualificationRunState]] = {
    QualificationRunState.PREPARED: frozenset(
        {
            QualificationRunState.RUNNING,
            QualificationRunState.BLOCKED,
            QualificationRunState.CANCELLED,
        }
    ),
    QualificationRunState.RUNNING: frozenset(
        {
            QualificationRunState.PASSED,
            QualificationRunState.FAILED,
            QualificationRunState.INCONCLUSIVE,
            QualificationRunState.BLOCKED,
            QualificationRunState.CANCELLED,
        }
    ),
    **{state: frozenset() for state in TERMINAL_QUALIFICATION_RUN_STATES},
}

QUALIFICATION_CASE_TRANSITIONS: dict[QualificationCaseState, frozenset[QualificationCaseState]] = {
    QualificationCaseState.PENDING: frozenset(
        {
            QualificationCaseState.RUNNING,
            QualificationCaseState.BLOCKED,
            QualificationCaseState.CANCELLED,
        }
    ),
    QualificationCaseState.RUNNING: frozenset(
        {
            QualificationCaseState.PASSED,
            QualificationCaseState.FAILED,
            QualificationCaseState.BLOCKED,
            QualificationCaseState.CANCELLED,
            QualificationCaseState.INCONCLUSIVE,
        }
    ),
    **{state: frozenset() for state in TERMINAL_QUALIFICATION_CASE_STATES},
}

QUALIFICATION_PREVIEW_TRANSITIONS: dict[
    QualificationPreviewState, frozenset[QualificationPreviewState]
] = {
    QualificationPreviewState.READY: frozenset(
        {
            QualificationPreviewState.CONSUMED,
            QualificationPreviewState.EXPIRED,
            QualificationPreviewState.REJECTED,
            QualificationPreviewState.CANCELLED,
        }
    ),
    **{state: frozenset() for state in QualificationPreviewState if state.terminal},
}


def can_transition_qualification_run(
    current: QualificationRunState, target: QualificationRunState
) -> bool:
    return target in QUALIFICATION_RUN_TRANSITIONS[current]


def can_transition_qualification_case(
    current: QualificationCaseState, target: QualificationCaseState
) -> bool:
    return target in QUALIFICATION_CASE_TRANSITIONS[current]


def can_transition_qualification_preview(
    current: QualificationPreviewState, target: QualificationPreviewState
) -> bool:
    return target in QUALIFICATION_PREVIEW_TRANSITIONS[current]


def sanitize_qualification_host(value: str) -> str:
    if not value.strip():
        raise ValueError("host is required")
    candidate = value if "://" in value else f"https://{value}"
    parsed = urlparse(candidate)
    if parsed.username or parsed.password:
        raise ValueError("host must not contain credentials")
    host = parsed.hostname
    if host is None or not host.strip():
        raise ValueError("host is required")
    return host


def reject_non_synthetic_qualification_input(payload: dict[str, Any]) -> None:
    forbidden = sorted(key for key in payload if key in _FORBIDDEN_INPUT_KEYS and payload[key])
    if forbidden:
        raise ValueError("qualification inputs must be synthetic repository fixtures")


def _require_hex_digest(value: str, field_name: str) -> str:
    if not _HEX_64.fullmatch(value):
        raise ValueError(f"{field_name} must be a sha256 hex digest")
    return value


def _require_positive_decimal(value: Decimal, field_name: str) -> Decimal:
    if value < 0 or value > 1:
        raise ValueError(f"{field_name} must be between 0 and 1 inclusive")
    return value


@dataclass(frozen=True, slots=True)
class QualificationSuite:
    suite_id: str
    capability: QualificationCapability
    version: str
    fixture_ids: tuple[str, ...]
    blocking_check_codes: tuple[str, ...]
    advisory_checks: tuple[tuple[str, Decimal], ...]
    minimum_advisory_score: Decimal
    max_timeout_rate: Decimal
    max_error_rate: Decimal
    required_evidence_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.suite_id or not self.version:
            raise ValueError("suite id and version are required")
        if not self.suite_id.startswith(self.capability.value):
            raise ValueError("suite capability must match suite_id")
        prefix = f"{self.capability.value}-"
        if not self.fixture_ids or any(
            not fixture_id.startswith(prefix) for fixture_id in self.fixture_ids
        ):
            raise ValueError("fixture ids must be capability-specific")
        if len(set(self.fixture_ids)) != len(self.fixture_ids):
            raise ValueError("fixture ids must be unique")
        if not self.blocking_check_codes:
            raise ValueError("blocking checks are required")
        _require_positive_decimal(self.minimum_advisory_score, "minimum_advisory_score")
        _require_positive_decimal(self.max_timeout_rate, "max_timeout_rate")
        _require_positive_decimal(self.max_error_rate, "max_error_rate")
        for check_code, weight in self.advisory_checks:
            if not check_code:
                raise ValueError("advisory check codes are required")
            if weight <= 0:
                raise ValueError("advisory weights must be positive")


@dataclass(frozen=True, slots=True)
class QualificationFixture:
    fixture_id: str
    suite_id: str
    suite_version: str
    capability: QualificationCapability
    input_kind: str
    max_input_bytes: int
    max_output_bytes: int
    blocking_check_codes: tuple[str, ...]
    advisory_rubric_codes: tuple[str, ...]
    sensitivity: str = "synthetic_public"
    source_kind: str = "repository_owned"

    def __post_init__(self) -> None:
        if not self.fixture_id or not self.suite_id or not self.suite_version:
            raise ValueError("fixture identity fields are required")
        if not self.fixture_id.startswith(f"{self.capability.value}-"):
            raise ValueError("fixture capability must match fixture_id")
        if self.sensitivity != "synthetic_public":
            raise ValueError("qualification fixtures must be synthetic_public")
        if self.source_kind != "repository_owned":
            raise ValueError("qualification fixtures must be repository-owned")
        if self.max_input_bytes < 1 or self.max_output_bytes < 1:
            raise ValueError("fixture size bounds must be positive")
        if not self.input_kind:
            raise ValueError("fixture input kind is required")


@dataclass(frozen=True, slots=True)
class QualificationRouteRevision:
    capability: QualificationCapability
    provider_protocol: str
    sanitized_base_host: str
    model_identifier: str
    generation_settings: tuple[tuple[str, str], ...]
    suite_version: str
    price_catalog_revision: str
    protection_policy_revision: str
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not all(
            (
                self.provider_protocol,
                self.model_identifier,
                self.suite_version,
                self.price_catalog_revision,
                self.protection_policy_revision,
            )
        ):
            raise ValueError("route revision identity fields are required")
        host = sanitize_qualification_host(self.sanitized_base_host)
        object.__setattr__(self, "sanitized_base_host", host)
        for key, value in self.generation_settings:
            if key.strip().lower() in _SECRET_SETTING_KEYS or not value:
                raise ValueError("generation settings must not contain secrets or empty values")
        digest = hashlib.sha256(
            json.dumps(
                {
                    "capability": self.capability.value,
                    "provider_protocol": self.provider_protocol,
                    "sanitized_base_host": host,
                    "model_identifier": self.model_identifier,
                    "generation_settings": list(self.generation_settings),
                    "suite_version": self.suite_version,
                    "price_catalog_revision": self.price_catalog_revision,
                    "protection_policy_revision": self.protection_policy_revision,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        object.__setattr__(self, "fingerprint", digest)


@dataclass(frozen=True, slots=True)
class QualificationCeilings:
    max_requests: int
    max_input_tokens: int | None
    max_output_tokens: int | None
    max_images: int | None
    conservative_max_cost: Decimal | None

    def __post_init__(self) -> None:
        if self.max_requests < 0:
            raise ValueError("max_requests must not be negative")
        for field_name in ("max_input_tokens", "max_output_tokens", "max_images"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must not be negative")
        if self.conservative_max_cost is not None and self.conservative_max_cost < 0:
            raise ValueError("conservative_max_cost must not be negative")


@dataclass(frozen=True, slots=True)
class QualificationPreview:
    preview_id: str
    confirmation_handle_hash: str
    actor_id: str
    process_instance_id: str
    capability: QualificationCapability
    route_revision: QualificationRouteRevision
    suite: QualificationSuite
    policy_revision: str
    fixture_ids: tuple[str, ...]
    ceilings: QualificationCeilings
    execution_mode: QualificationExecutionMode
    expires_at: datetime
    state: QualificationPreviewState = QualificationPreviewState.READY
    revision: int = 1
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if not all(
            (
                self.preview_id,
                self.actor_id,
                self.process_instance_id,
                self.policy_revision,
            )
        ):
            raise ValueError("preview identity and binding fields are required")
        _require_hex_digest(self.confirmation_handle_hash, "confirmation_handle_hash")
        if self.capability is not self.route_revision.capability:
            raise ValueError("preview capability must match the route revision")
        if self.capability is not self.suite.capability:
            raise ValueError("preview capability must match the suite")
        if not self.fixture_ids or any(
            fixture_id not in self.suite.fixture_ids for fixture_id in self.fixture_ids
        ):
            raise ValueError("preview fixtures must come from the suite")
        if self.revision < 1:
            raise ValueError("preview revision must be positive")
        object.__setattr__(self, "expires_at", require_aware_utc(self.expires_at, "expires_at"))

    @property
    def fixture_count(self) -> int:
        return len(self.fixture_ids)

    def state_at(self, now: datetime) -> QualificationPreviewState:
        if self.state is not QualificationPreviewState.READY:
            return self.state
        if require_aware_utc(now, "now") > self.expires_at:
            return QualificationPreviewState.EXPIRED
        return QualificationPreviewState.READY


def can_confirm_controlled_live(
    preview: QualificationPreview,
    *,
    price_evidence_present: bool,
    now: datetime,
) -> bool:
    if preview.execution_mode is not QualificationExecutionMode.CONTROLLED_LIVE:
        return False
    if preview.state_at(now) is not QualificationPreviewState.READY:
        return False
    if not price_evidence_present:
        return False
    ceilings = preview.ceilings
    if ceilings.max_requests < 1:
        return False
    if ceilings.conservative_max_cost is None:
        return False
    if preview.capability is QualificationCapability.IMAGE:
        return ceilings.max_images is not None and ceilings.max_images >= 1
    return (
        ceilings.max_input_tokens is not None
        and ceilings.max_output_tokens is not None
        and ceilings.max_input_tokens >= 1
        and ceilings.max_output_tokens >= 1
    )


@dataclass(frozen=True, slots=True)
class QualificationCheckResult:
    check_code: str
    kind: QualificationCheckKind
    passed: bool | None = None
    score: Decimal | None = None
    reason_code: QualificationReasonCode | None = None

    def __post_init__(self) -> None:
        if not self.check_code:
            raise ValueError("check_code is required")
        if self.kind is QualificationCheckKind.BLOCKING and self.passed is None:
            raise ValueError("blocking checks require a pass result")
        if self.kind is QualificationCheckKind.ADVISORY and self.score is not None:
            _require_positive_decimal(self.score, "advisory score")

    def as_sanitized_dict(self) -> dict[str, Any]:
        return {
            "check_code": self.check_code,
            "kind": self.kind.value,
            "passed": self.passed,
            "score": None if self.score is None else str(self.score),
            "reason_code": None if self.reason_code is None else self.reason_code.value,
        }


@dataclass(frozen=True, slots=True)
class QualificationEvidence:
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    image_count: int
    cost_amount: Decimal | None
    cost_currency: str | None
    provider_request_id: str | None
    response_hash: str | None
    artifact_id: str | None

    def __post_init__(self) -> None:
        if self.latency_ms < 0 or self.image_count < 0:
            raise ValueError("latency and image counts must not be negative")
        if self.input_tokens is not None and self.input_tokens < 0:
            raise ValueError("token counts must not be negative")
        if self.output_tokens is not None and self.output_tokens < 0:
            raise ValueError("token counts must not be negative")
        if self.response_hash is not None:
            _require_hex_digest(self.response_hash, "response_hash")
        if self.provider_request_id is not None and (
            not self.provider_request_id or len(self.provider_request_id) > 128
        ):
            raise ValueError("provider_request_id is invalid")

    def as_sanitized_dict(self) -> dict[str, Any]:
        return {
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "image_count": self.image_count,
            "cost_amount": None if self.cost_amount is None else str(self.cost_amount),
            "cost_currency": self.cost_currency,
            "provider_request_id": self.provider_request_id,
            "response_hash": self.response_hash,
            "artifact_id": self.artifact_id,
        }


@dataclass(frozen=True, slots=True)
class QualificationRun:
    run_id: str
    preview_id: str
    actor_id: str
    process_instance_id: str
    capability: QualificationCapability
    route_revision: QualificationRouteRevision
    suite: QualificationSuite
    execution_mode: QualificationExecutionMode
    state: QualificationRunState
    idempotency_key: str
    created_at: datetime
    lease_token: str = ""
    lease_expires_at: datetime | None = None
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if not all(
            (
                self.run_id,
                self.preview_id,
                self.actor_id,
                self.process_instance_id,
                self.idempotency_key,
            )
        ):
            raise ValueError("run identity fields are required")
        if self.capability is not self.route_revision.capability:
            raise ValueError("run capability must match the route revision")
        if self.capability is not self.suite.capability:
            raise ValueError("run capability must match the suite")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at, "created_at"))
        if self.lease_expires_at is not None:
            object.__setattr__(
                self,
                "lease_expires_at",
                require_aware_utc(self.lease_expires_at, "lease_expires_at"),
            )


@dataclass(frozen=True, slots=True)
class QualificationCaseResult:
    case_id: str
    run_id: str
    fixture_id: str
    fixture_hash: str
    input_hash: str
    state: QualificationCaseState
    idempotency_key: str
    check_results: tuple[QualificationCheckResult, ...]
    evidence: QualificationEvidence
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reason_code: QualificationReasonCode | None = None
    lease_token: str = ""
    lease_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not all((self.case_id, self.run_id, self.fixture_id, self.idempotency_key)):
            raise ValueError("case identity fields are required")
        _require_hex_digest(self.fixture_hash, "fixture_hash")
        _require_hex_digest(self.input_hash, "input_hash")
        if self.started_at is not None:
            object.__setattr__(self, "started_at", require_aware_utc(self.started_at, "started_at"))
        if self.finished_at is not None:
            object.__setattr__(
                self, "finished_at", require_aware_utc(self.finished_at, "finished_at")
            )
        if self.lease_expires_at is not None:
            object.__setattr__(
                self,
                "lease_expires_at",
                require_aware_utc(self.lease_expires_at, "lease_expires_at"),
            )

    def as_sanitized_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "run_id": self.run_id,
            "fixture_id": self.fixture_id,
            "fixture_hash": self.fixture_hash,
            "input_hash": self.input_hash,
            "state": self.state.value,
            "idempotency_key": self.idempotency_key,
            "check_results": tuple(item.as_sanitized_dict() for item in self.check_results),
            "evidence": self.evidence.as_sanitized_dict(),
            "started_at": None if self.started_at is None else self.started_at.isoformat(),
            "finished_at": None if self.finished_at is None else self.finished_at.isoformat(),
            "reason_code": None if self.reason_code is None else self.reason_code.value,
        }


@dataclass(frozen=True, slots=True)
class QualificationDecision:
    decision_id: str
    capability: QualificationCapability
    route_revision: QualificationRouteRevision
    suite_version: str
    status: QualificationDecisionStatus
    run_id: str | None
    evaluated_at: datetime
    blocker_codes: tuple[QualificationReasonCode, ...] = ()
    advisory_score: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.decision_id or not self.suite_version:
            raise ValueError("decision identity fields are required")
        if self.capability is not self.route_revision.capability:
            raise ValueError("decision capability must match the route revision")
        object.__setattr__(
            self, "evaluated_at", require_aware_utc(self.evaluated_at, "evaluated_at")
        )
        if self.status is QualificationDecisionStatus.PASSED and self.blocker_codes:
            raise ValueError("a passing qualification decision cannot contain blockers")
        if self.advisory_score is not None:
            _require_positive_decimal(self.advisory_score, "advisory_score")

    @property
    def qualifies(self) -> bool:
        return self.status is QualificationDecisionStatus.PASSED

    @property
    def activates_production(self) -> bool:
        return False

    def against_current(self, current: QualificationRouteRevision) -> QualificationDecision:
        if (
            self.status is QualificationDecisionStatus.PASSED
            and current.fingerprint != self.route_revision.fingerprint
        ):
            return QualificationDecision(
                decision_id=self.decision_id,
                capability=self.capability,
                route_revision=current,
                suite_version=self.suite_version,
                status=QualificationDecisionStatus.STALE,
                run_id=self.run_id,
                evaluated_at=self.evaluated_at,
                blocker_codes=(QualificationReasonCode.STALE_ROUTE_REVISION,),
                advisory_score=self.advisory_score,
            )
        return self


@dataclass(frozen=True, slots=True)
class QualificationHumanReviewNote:
    note_id: str
    run_id: str
    actor_id: str
    created_at: datetime
    safe_summary: str

    def __post_init__(self) -> None:
        if not all((self.note_id, self.run_id, self.actor_id, self.safe_summary)):
            raise ValueError("human review note fields are required")
        if len(self.safe_summary) > 240:
            raise ValueError("human review notes must stay short and sanitized")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at, "created_at"))


def derive_qualification_acceptance(
    *,
    suite: QualificationSuite,
    check_results: tuple[QualificationCheckResult, ...],
    timeout_rate: Decimal,
    error_rate: Decimal,
) -> QualificationRunState:
    blocking_by_code: dict[str, list[QualificationCheckResult]] = {}
    advisory_by_code: dict[str, list[QualificationCheckResult]] = {}
    for item in check_results:
        if item.kind is QualificationCheckKind.BLOCKING:
            blocking_by_code.setdefault(item.check_code, []).append(item)
        elif item.kind is QualificationCheckKind.ADVISORY:
            advisory_by_code.setdefault(item.check_code, []).append(item)
    for check_code in suite.blocking_check_codes:
        results = blocking_by_code.get(check_code, [])
        if not results or any(item.passed is not True for item in results):
            return QualificationRunState.FAILED
    if timeout_rate > suite.max_timeout_rate or error_rate > suite.max_error_rate:
        return QualificationRunState.FAILED
    weighted = Decimal("0")
    total_weight = Decimal("0")
    for check_code, weight in suite.advisory_checks:
        scores = [
            item.score for item in advisory_by_code.get(check_code, ()) if item.score is not None
        ]
        if not scores:
            return QualificationRunState.FAILED
        average = sum(scores, Decimal("0")) / Decimal(len(scores))
        weighted += weight * average
        total_weight += weight
    if total_weight <= 0 or (weighted / total_weight) < suite.minimum_advisory_score:
        return QualificationRunState.FAILED
    return QualificationRunState.PASSED


def evaluate_qualification_decision(
    *,
    capability: QualificationCapability,
    route_revision: QualificationRouteRevision,
    suite: QualificationSuite,
    run: QualificationRun | None,
    case_results: tuple[QualificationCaseResult, ...],
    now: datetime | None = None,
) -> QualificationDecision:
    if capability is not route_revision.capability or capability is not suite.capability:
        raise ValueError("capability must match the suite and route revision")
    evaluated_at = require_aware_utc(now or datetime.now(UTC), "evaluated_at")
    if run is None and not case_results:
        return QualificationDecision(
            decision_id=f"unqualified-{capability.value}",
            capability=capability,
            route_revision=route_revision,
            suite_version=suite.version,
            status=QualificationDecisionStatus.UNQUALIFIED,
            run_id=None,
            evaluated_at=evaluated_at,
            blocker_codes=(QualificationReasonCode.DEFAULT_DENIED,),
        )
    if run is None:
        raise ValueError("case results require a qualification run")
    acceptance = derive_qualification_acceptance(
        suite=suite,
        check_results=tuple(check for case in case_results for check in case.check_results),
        timeout_rate=Decimal("0"),
        error_rate=Decimal("0"),
    )
    status = (
        QualificationDecisionStatus.PASSED
        if acceptance is QualificationRunState.PASSED
        else QualificationDecisionStatus.FAILED
    )
    return QualificationDecision(
        decision_id=f"decision-{run.run_id}",
        capability=capability,
        route_revision=route_revision,
        suite_version=suite.version,
        status=status,
        run_id=run.run_id,
        evaluated_at=evaluated_at,
    )


def apply_human_review_note(
    decision: QualificationDecision, note: QualificationHumanReviewNote
) -> QualificationDecision:
    del note
    return decision
