"""Qualification runner that only consumes catalog fixtures and capability gateways."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from ych_bot.application.readiness_guard import ReadinessBlockedError
from ych_bot.domain.model_qualification import (
    QualificationCapability,
    QualificationCaseResult,
    QualificationCaseState,
    QualificationCeilings,
    QualificationCheckResult,
    QualificationDecisionStatus,
    QualificationExecutionMode,
    QualificationPreview,
    QualificationReasonCode,
    QualificationRun,
    QualificationRunState,
    QualificationSuite,
    derive_qualification_acceptance,
)
from ych_bot.domain.modeling import (
    ChatGenerationRequest,
    ChatGenerationResult,
    ImageGenerationRequest,
    ImageGenerationResult,
    ModelBudgetExceededError,
    ModelCircuitOpenError,
    ModelRequestError,
    ModelResponseError,
)
from ych_bot.qualification.artifacts import QualificationImageStore
from ych_bot.qualification.catalog import QualificationCatalog
from ych_bot.qualification.fakes import FakeAmbiguousInterruption
from ych_bot.qualification.identity import qualification_chat_messages
from ych_bot.qualification.safety import QualificationLiveGuards, evaluate_qualification_pre_attempt
from ych_bot.qualification.scoring import (
    QualificationModelOutput,
    evaluate_qualification_case,
    parse_stats_payload,
)


class _QualificationStore(Protocol):
    async def qualification_run(self, run_id: str) -> QualificationRun | None: ...

    async def qualification_preview(self, preview_id: str) -> QualificationPreview | None: ...

    async def qualification_cases(self, run_id: str) -> tuple[QualificationCaseResult, ...]: ...

    async def transition_qualification_run(self, **kwargs: object) -> QualificationRun | None: ...

    async def claim_qualification_case(
        self, **kwargs: object
    ) -> QualificationCaseResult | None: ...

    async def transition_qualification_case(
        self, **kwargs: object
    ) -> QualificationCaseResult | None: ...

    async def mark_qualification_case_provider_attempt(
        self, **kwargs: object
    ) -> QualificationCaseResult | None: ...

    async def reconcile_qualification_leases(self, **kwargs: object) -> tuple[str, ...]: ...

    async def block_qualification_run(self, **kwargs: object) -> QualificationRun | None: ...

    async def record_qualification_decision(self, **kwargs: object) -> object: ...

    async def record_qualification_cost_evidence(self, **kwargs: object) -> object: ...

    async def record_qualification_artifact(self, **kwargs: object) -> object: ...

    async def record_qualification_check_results(self, **kwargs: object) -> None: ...


class _ChatGateway(Protocol):
    async def generate(self, request: ChatGenerationRequest) -> ChatGenerationResult: ...


class _ImageGateway(Protocol):
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...


@dataclass
class _Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    images: int = 0


def _usage_from_cases(cases: tuple[QualificationCaseResult, ...]) -> _Usage:
    usage = _Usage()
    counted = {
        QualificationCaseState.PASSED,
        QualificationCaseState.FAILED,
        QualificationCaseState.INCONCLUSIVE,
    }
    for case in cases:
        attempted = case.state in counted or bool(case.evidence.provider_request_id)
        if not attempted:
            continue
        usage.requests += 1
        usage.input_tokens += case.evidence.input_tokens or 0
        usage.output_tokens += case.evidence.output_tokens or 0
        usage.images += case.evidence.image_count
    return usage


def _ceiling_exhausted(
    ceilings: QualificationCeilings,
    usage: _Usage,
    *,
    capability: QualificationCapability,
) -> bool:
    if usage.requests >= ceilings.max_requests:
        return True
    if ceilings.max_input_tokens is not None and usage.input_tokens >= ceilings.max_input_tokens:
        return True
    if ceilings.max_output_tokens is not None and usage.output_tokens >= ceilings.max_output_tokens:
        return True
    return (
        capability is QualificationCapability.IMAGE
        and ceilings.max_images is not None
        and usage.images >= ceilings.max_images
    )


class QualificationRunner:
    def __init__(
        self,
        *,
        repository: _QualificationStore,
        catalog: QualificationCatalog,
        chat_gateway: _ChatGateway,
        vision_gateway: _ChatGateway,
        stats_gateway: _ChatGateway,
        image_gateway: _ImageGateway,
        live_guards: QualificationLiveGuards | None = None,
        image_store: QualificationImageStore | None = None,
    ) -> None:
        self._repository = repository
        self._catalog = catalog
        self._chat_gateway = chat_gateway
        self._vision_gateway = vision_gateway
        self._stats_gateway = stats_gateway
        self._image_gateway = image_gateway
        self._live_guards = live_guards
        self._image_store = image_store

    async def execute_fake_run(self, run_id: str, *, now: datetime) -> QualificationRun:
        return await self._execute_run(
            run_id, now=now, expected_mode=QualificationExecutionMode.FAKE
        )

    async def execute_controlled_live_run(self, run_id: str, *, now: datetime) -> QualificationRun:
        return await self._execute_run(
            run_id, now=now, expected_mode=QualificationExecutionMode.CONTROLLED_LIVE
        )

    async def _execute_run(
        self,
        run_id: str,
        *,
        now: datetime,
        expected_mode: QualificationExecutionMode,
    ) -> QualificationRun:
        await self._repository.reconcile_qualification_leases(now=now)
        run = await self._repository.qualification_run(run_id)
        if run is None:
            raise ValueError("unknown qualification run")
        if run.execution_mode is not expected_mode:
            raise ValueError("qualification runner execution mode mismatch")
        if run.state.terminal:
            return run
        preview = await self._repository.qualification_preview(run.preview_id)
        if preview is None:
            raise ValueError("missing qualification preview")
        if now > preview.expires_at:
            return await self._block_and_record(
                run, run_id, QualificationReasonCode.PREVIEW_EXPIRED, now
            )
        if run.state is QualificationRunState.PREPARED:
            running = await self._repository.transition_qualification_run(
                run_id=run_id,
                from_state=QualificationRunState.PREPARED,
                to_state=QualificationRunState.RUNNING,
                now=now,
            )
            if running is None:
                raise ValueError("unable to start qualification run")
            run = running
        cases = await self._repository.qualification_cases(run_id)
        usage = _usage_from_cases(cases)
        check_results: list[QualificationCheckResult] = []
        for case in cases:
            if case.state is not QualificationCaseState.PENDING:
                continue
            current = await self._repository.qualification_run(run_id)
            if current is None or current.state.terminal:
                return current or run
            if now > preview.expires_at:
                return await self._block_and_record(
                    run, run_id, QualificationReasonCode.PREVIEW_EXPIRED, now
                )
            if _ceiling_exhausted(preview.ceilings, usage, capability=run.capability):
                return await self._block_and_record(
                    run, run_id, QualificationReasonCode.CEILING_EXHAUSTED, now
                )
            if expected_mode is QualificationExecutionMode.CONTROLLED_LIVE:
                if self._live_guards is None:
                    return await self._block_and_record(
                        run, run_id, QualificationReasonCode.READINESS_STALE, now
                    )
                blocked = evaluate_qualification_pre_attempt(
                    run_revision=run.route_revision,
                    guards=self._live_guards,
                )
                if blocked is not None:
                    return await self._block_and_record(run, run_id, blocked, now)
            lease_token = f"{expected_mode.value}-{case.fixture_id}"
            claimed = await self._repository.claim_qualification_case(
                run_id=run_id,
                fixture_id=case.fixture_id,
                lease_token=lease_token,
                ttl_seconds=60,
                now=now,
            )
            if claimed is None:
                continue
            await self._repository.mark_qualification_case_provider_attempt(
                case_id=claimed.case_id,
                lease_token=lease_token,
                provider_request_id=f"fake-{run.capability.value}-{claimed.case_id}",
                now=now,
            )
            outcome = await self._execute_claimed_case(
                run=run,
                claimed=claimed,
                lease_token=lease_token,
                now=now,
            )
            if outcome.block_run is not None:
                return await self._block_and_record(run, run_id, outcome.block_run, now)
            check_results.extend(outcome.check_results)
            usage.requests += 1
            usage.input_tokens += outcome.input_tokens
            usage.output_tokens += outcome.output_tokens
            usage.images += outcome.image_count
        finished = await self._finish_run(
            run_id, suite=run.suite, check_results=check_results, now=now
        )
        await self._record_cost_evidence(run_id, preview=preview, now=now)
        return finished

    async def _execute_claimed_case(
        self,
        *,
        run: QualificationRun,
        claimed: QualificationCaseResult,
        lease_token: str,
        now: datetime,
    ) -> _CaseOutcome:
        fixture = self._catalog.resolve_fixture(claimed.fixture_id)
        try:
            output, input_tokens, output_tokens, image_count = await self._generate(
                run, claimed, now=now
            )
        except TimeoutError:
            await self._finish_case(
                claimed.case_id,
                lease_token,
                QualificationCaseState.FAILED,
                QualificationReasonCode.TIMEOUT,
                now=now,
            )
            return _CaseOutcome()
        except ModelRequestError:
            await self._finish_case(
                claimed.case_id,
                lease_token,
                QualificationCaseState.FAILED,
                QualificationReasonCode.RETRYABLE_PROVIDER_ERROR,
                now=now,
            )
            return _CaseOutcome()
        except ModelResponseError:
            await self._finish_case(
                claimed.case_id,
                lease_token,
                QualificationCaseState.FAILED,
                QualificationReasonCode.INVALID_RESPONSE,
                now=now,
            )
            return _CaseOutcome()
        except ModelBudgetExceededError:
            await self._finish_case(
                claimed.case_id,
                lease_token,
                QualificationCaseState.BLOCKED,
                QualificationReasonCode.QUOTA_EXHAUSTED,
                now=now,
            )
            return _CaseOutcome(block_run=QualificationReasonCode.QUOTA_EXHAUSTED)
        except ModelCircuitOpenError:
            await self._finish_case(
                claimed.case_id,
                lease_token,
                QualificationCaseState.BLOCKED,
                QualificationReasonCode.CIRCUIT_OPEN,
                now=now,
            )
            return _CaseOutcome(block_run=QualificationReasonCode.CIRCUIT_OPEN)
        except ReadinessBlockedError:
            await self._finish_case(
                claimed.case_id,
                lease_token,
                QualificationCaseState.BLOCKED,
                QualificationReasonCode.READINESS_STALE,
                now=now,
            )
            return _CaseOutcome(block_run=QualificationReasonCode.READINESS_STALE)
        except FakeAmbiguousInterruption:
            await self._finish_case(
                claimed.case_id,
                lease_token,
                QualificationCaseState.INCONCLUSIVE,
                QualificationReasonCode.AMBIGUOUS_INTERRUPTION,
                now=now,
            )
            return _CaseOutcome()
        scored = evaluate_qualification_case(suite=run.suite, fixture=fixture, output=output)
        blocking_passed = all(item.passed for item in scored if item.kind.value == "blocking")
        terminal = (
            QualificationCaseState.PASSED if blocking_passed else QualificationCaseState.FAILED
        )
        await self._finish_case(
            claimed.case_id,
            lease_token,
            terminal,
            None,
            now=now,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            image_count=image_count,
            response_hash=_response_hash(output),
            check_results=scored,
        )
        return _CaseOutcome(
            check_results=scored,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            image_count=image_count,
        )

    async def _generate(
        self, run: QualificationRun, claimed: QualificationCaseResult, *, now: datetime
    ) -> tuple[QualificationModelOutput, int, int, int]:
        payload = self._catalog.case_input(claimed.fixture_id)
        evidence_fields = run.suite.required_evidence_fields
        if run.capability is QualificationCapability.IMAGE:
            generated = await self._image_gateway.generate(
                ImageGenerationRequest(
                    prompt=payload["prompt"],
                    request_id=claimed.case_id,
                )
            )
            if self._image_store is not None:
                try:
                    stored = self._image_store.ingest(
                        run_id=run.run_id,
                        case_id=claimed.case_id,
                        artifacts=generated.artifacts,
                    )
                except ValueError as exc:
                    raise ModelResponseError("qualification image artifact was rejected") from exc
                await self._repository.record_qualification_artifact(
                    artifact_id=stored.artifact_id,
                    case_id=claimed.case_id,
                    relative_path=stored.relative_path,
                    content_sha256=stored.content_sha256,
                    mime_type=stored.mime_type,
                    size_bytes=stored.size_bytes,
                    pixel_width=stored.pixel_width,
                    pixel_height=stored.pixel_height,
                    now=now,
                )
                return (
                    QualificationModelOutput(
                        mime_type=stored.mime_type,
                        artifact_count=1,
                        artifact_relative_path=stored.relative_path,
                        artifact_bytes=stored.size_bytes,
                        pixel_width=stored.pixel_width,
                        pixel_height=stored.pixel_height,
                        evidence_fields=evidence_fields,
                    ),
                    0,
                    0,
                    1,
                )
            artifact = generated.artifacts[0] if generated.artifacts else None
            return (
                QualificationModelOutput(
                    mime_type="image/png",
                    artifact_count=len(generated.artifacts),
                    artifact_relative_path=artifact,
                    evidence_fields=evidence_fields,
                ),
                0,
                0,
                len(generated.artifacts),
            )
        if run.capability is QualificationCapability.CHAT:
            gateway = self._chat_gateway
            content = payload["text"]
            image_urls: tuple[str, ...] = ()
        elif run.capability is QualificationCapability.VISION:
            gateway = self._vision_gateway
            content = payload["text"]
            image_id = payload.get("image_id", claimed.fixture_id)
            image_urls = (_catalog_image_data_uri(self._catalog, image_id),)
        elif run.capability is QualificationCapability.STATS:
            gateway = self._stats_gateway
            content = payload["text"]
            aggregates = payload.get("aggregates")
            if aggregates:
                content = f"{content}\n{aggregates}"
            content = f"{content}\n只返回一个 JSON 对象，不要 Markdown。"
            image_urls = ()
        else:
            raise ValueError("unsupported qualification capability")
        generated = await gateway.generate(
            ChatGenerationRequest(
                request_id=claimed.case_id,
                messages=qualification_chat_messages(content, image_urls=image_urls),
                temperature=0.2,
                max_output_tokens=256 if run.capability is QualificationCapability.STATS else 1000,
            )
        )
        stats_payload = None
        if run.capability is QualificationCapability.STATS:
            stats_payload = parse_stats_payload(generated.text)
        return (
            QualificationModelOutput(
                text=generated.text,
                stats_payload=stats_payload,
                evidence_fields=evidence_fields,
            ),
            generated.input_tokens or 0,
            generated.output_tokens or 0,
            0,
        )

    async def _finish_case(
        self,
        case_id: str,
        lease_token: str,
        to_state: QualificationCaseState,
        reason_code: QualificationReasonCode | None,
        *,
        now: datetime,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        image_count: int | None = None,
        response_hash: str | None = None,
        check_results: tuple[QualificationCheckResult, ...] = (),
    ) -> None:
        await self._repository.transition_qualification_case(
            case_id=case_id,
            from_state=QualificationCaseState.RUNNING,
            to_state=to_state,
            lease_token=lease_token,
            now=now,
            reason_code=reason_code,
            latency_ms=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            image_count=image_count,
            response_hash=response_hash,
        )
        if check_results:
            await self._repository.record_qualification_check_results(
                case_id=case_id,
                check_results=check_results,
            )

    async def _finish_run(
        self,
        run_id: str,
        *,
        suite: QualificationSuite,
        check_results: list[QualificationCheckResult],
        now: datetime,
    ) -> QualificationRun:
        cases = await self._repository.qualification_cases(run_id)
        if any(item.state is QualificationCaseState.INCONCLUSIVE for item in cases):
            acceptance = QualificationRunState.INCONCLUSIVE
        elif any(item.state is QualificationCaseState.BLOCKED for item in cases):
            acceptance = QualificationRunState.BLOCKED
        elif any(item.state is QualificationCaseState.FAILED for item in cases):
            acceptance = QualificationRunState.FAILED
        else:
            acceptance = derive_qualification_acceptance(
                suite=suite,
                check_results=tuple(check_results),
                timeout_rate=Decimal("0"),
                error_rate=Decimal("0"),
            )
        finished = await self._repository.transition_qualification_run(
            run_id=run_id,
            from_state=QualificationRunState.RUNNING,
            to_state=acceptance,
            now=now,
        )
        if finished is None:
            current = await self._repository.qualification_run(run_id)
            if current is not None and current.state.terminal:
                await self._record_decision(current, now=now)
                return current
            raise ValueError("unable to finish qualification run")
        blockers = ()
        if acceptance is QualificationRunState.INCONCLUSIVE:
            blockers = (QualificationReasonCode.AMBIGUOUS_INTERRUPTION,)
        elif acceptance is QualificationRunState.FAILED:
            blockers = _failed_blocker_codes(tuple(check_results)) or (
                QualificationReasonCode.ADVISORY_THRESHOLD_UNMET,
            )
        await self._record_decision(finished, now=now, blocker_codes=blockers)
        return finished

    async def _record_cost_evidence(
        self,
        run_id: str,
        *,
        preview: QualificationPreview,
        now: datetime,
    ) -> None:
        cases = await self._repository.qualification_cases(run_id)
        usage = _usage_from_cases(cases)
        await self._repository.record_qualification_cost_evidence(
            run_id=run_id,
            price_catalog_revision=preview.route_revision.price_catalog_revision,
            planned_max_cost=preview.ceilings.conservative_max_cost,
            observed_cost=None,
            currency="CNY",
            request_count=usage.requests,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            image_count=usage.images,
            now=now,
        )

    async def _block_and_record(
        self,
        run: QualificationRun,
        run_id: str,
        reason_code: QualificationReasonCode,
        now: datetime,
    ) -> QualificationRun:
        blocked = await self._repository.block_qualification_run(
            run_id, reason_code=reason_code, now=now
        )
        if blocked is None:
            current = await self._repository.qualification_run(run_id)
            if current is not None and current.state.terminal:
                await self._record_decision(current, now=now, blocker_codes=(reason_code,))
                return current
            raise ValueError("unable to block qualification run")
        await self._record_decision(blocked, now=now, blocker_codes=(reason_code,))
        return blocked

    async def _record_decision(
        self,
        run: QualificationRun,
        *,
        now: datetime,
        blocker_codes: tuple[QualificationReasonCode, ...] = (),
    ) -> None:
        status = _decision_status(run.state)
        codes = () if status is QualificationDecisionStatus.PASSED else blocker_codes
        if status is QualificationDecisionStatus.BLOCKED and not codes:
            codes = (QualificationReasonCode.DEFAULT_DENIED,)
        if status is QualificationDecisionStatus.UNQUALIFIED:
            return
        await self._repository.record_qualification_decision(
            capability=run.capability,
            route_revision=run.route_revision,
            suite_version=run.suite.version,
            status=status,
            run_id=run.run_id,
            evaluated_at=now,
            blocker_codes=codes,
        )


@dataclass(frozen=True, slots=True)
class _CaseOutcome:
    check_results: tuple[QualificationCheckResult, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    image_count: int = 0
    block_run: QualificationReasonCode | None = None


def _response_hash(output: QualificationModelOutput) -> str:
    payload = output.text or output.artifact_relative_path or ""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _catalog_image_data_uri(catalog: QualificationCatalog, image_id: str) -> str:
    payload = catalog.case_image(image_id)
    encoded = base64.standard_b64encode(payload).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _failed_blocker_codes(
    check_results: tuple[QualificationCheckResult, ...],
) -> tuple[QualificationReasonCode, ...]:
    codes: list[QualificationReasonCode] = []
    seen: set[QualificationReasonCode] = set()
    for item in check_results:
        if item.passed or item.reason_code is None or item.reason_code in seen:
            continue
        seen.add(item.reason_code)
        codes.append(item.reason_code)
    return tuple(codes)


def _decision_status(state: QualificationRunState) -> QualificationDecisionStatus:
    if state is QualificationRunState.PASSED:
        return QualificationDecisionStatus.PASSED
    if state is QualificationRunState.FAILED:
        return QualificationDecisionStatus.FAILED
    if state in {
        QualificationRunState.BLOCKED,
        QualificationRunState.INCONCLUSIVE,
        QualificationRunState.CANCELLED,
    }:
        return QualificationDecisionStatus.BLOCKED
    return QualificationDecisionStatus.UNQUALIFIED
