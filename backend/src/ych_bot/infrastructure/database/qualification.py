"""Persistence for controlled model-route qualification."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

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
    QualificationPreview,
    QualificationPreviewState,
    QualificationReasonCode,
    QualificationRouteRevision,
    QualificationRun,
    QualificationRunState,
    QualificationSuite,
    can_transition_qualification_case,
    can_transition_qualification_preview,
    can_transition_qualification_run,
)
from ych_bot.domain.readiness import require_aware_utc
from ych_bot.qualification.artifacts import QualificationStoredArtifact


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _iso(value: datetime, field_name: str) -> str:
    return require_aware_utc(value, field_name).isoformat()


def _utc_now(now: datetime | None = None) -> datetime:
    return require_aware_utc(now or datetime.now(UTC), "now")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _suite_payload(suite: QualificationSuite) -> dict[str, Any]:
    return {
        "suite_id": suite.suite_id,
        "capability": suite.capability.value,
        "version": suite.version,
        "fixture_ids": list(suite.fixture_ids),
        "blocking_check_codes": list(suite.blocking_check_codes),
        "advisory_checks": [[code, str(weight)] for code, weight in suite.advisory_checks],
        "minimum_advisory_score": str(suite.minimum_advisory_score),
        "max_timeout_rate": str(suite.max_timeout_rate),
        "max_error_rate": str(suite.max_error_rate),
        "required_evidence_fields": list(suite.required_evidence_fields),
    }


def _suite_from_payload(payload: dict[str, Any]) -> QualificationSuite:
    return QualificationSuite(
        suite_id=str(payload["suite_id"]),
        capability=QualificationCapability(payload["capability"]),
        version=str(payload["version"]),
        fixture_ids=tuple(str(item) for item in payload["fixture_ids"]),
        blocking_check_codes=tuple(str(item) for item in payload["blocking_check_codes"]),
        advisory_checks=tuple(
            (str(code), Decimal(str(weight))) for code, weight in payload["advisory_checks"]
        ),
        minimum_advisory_score=Decimal(str(payload["minimum_advisory_score"])),
        max_timeout_rate=Decimal(str(payload["max_timeout_rate"])),
        max_error_rate=Decimal(str(payload["max_error_rate"])),
        required_evidence_fields=tuple(str(item) for item in payload["required_evidence_fields"]),
    )


def _evidence_from_row(row: sqlite3.Row) -> QualificationEvidence:
    return QualificationEvidence(
        latency_ms=int(row["latency_ms"] or 0),
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        image_count=int(row["image_count"] or 0),
        cost_amount=None,
        cost_currency=None,
        provider_request_id=row["provider_request_id"],
        response_hash=row["response_hash"],
        artifact_id=None,
    )


def _unconfigured_revision(capability: QualificationCapability) -> QualificationRouteRevision:
    return QualificationRouteRevision(
        capability=capability,
        provider_protocol="unconfigured",
        sanitized_base_host="unconfigured.local",
        model_identifier="unconfigured",
        generation_settings=(("mode", "denied"),),
        suite_version="none",
        price_catalog_revision="none",
        protection_policy_revision="none",
    )


def _route_from_row(row: sqlite3.Row) -> QualificationRouteRevision:
    settings = json.loads(row["generation_settings_json"])
    return QualificationRouteRevision(
        capability=QualificationCapability(row["capability"]),
        provider_protocol=row["provider_protocol"],
        sanitized_base_host=row["sanitized_base_host"],
        model_identifier=row["model_identifier"],
        generation_settings=tuple((str(key), str(value)) for key, value in settings),
        suite_version=row["suite_version"],
        price_catalog_revision=row["price_catalog_revision"],
        protection_policy_revision=row["protection_policy_revision"],
    )


def _preview_from_rows(preview: sqlite3.Row, revision: sqlite3.Row) -> QualificationPreview:
    ceilings = QualificationCeilings(
        max_requests=int(preview["max_requests"]),
        max_input_tokens=preview["max_input_tokens"],
        max_output_tokens=preview["max_output_tokens"],
        max_images=preview["max_images"],
        conservative_max_cost=(
            None
            if preview["conservative_max_cost"] is None
            else Decimal(str(preview["conservative_max_cost"]))
        ),
    )
    return QualificationPreview(
        preview_id=preview["id"],
        confirmation_handle_hash=preview["confirmation_handle_hash"],
        actor_id=preview["actor_id"],
        process_instance_id=preview["process_instance_id"],
        capability=QualificationCapability(preview["capability"]),
        route_revision=_route_from_row(revision),
        suite=_suite_from_payload(json.loads(preview["suite_snapshot_json"])),
        policy_revision=preview["policy_revision"],
        fixture_ids=tuple(str(item) for item in json.loads(preview["fixture_ids_json"])),
        ceilings=ceilings,
        execution_mode=QualificationExecutionMode(preview["execution_mode"]),
        expires_at=datetime.fromisoformat(preview["expires_at"]),
        state=QualificationPreviewState(preview["state"]),
        revision=int(preview["revision"]),
        correlation_id=preview["correlation_id"],
    )


def _run_from_rows(run: sqlite3.Row, revision: sqlite3.Row) -> QualificationRun:
    lease_expires = run["lease_expires_at"]
    return QualificationRun(
        run_id=run["id"],
        preview_id=run["preview_id"],
        actor_id=run["actor_id"],
        process_instance_id=run["process_instance_id"],
        capability=QualificationCapability(run["capability"]),
        route_revision=_route_from_row(revision),
        suite=_suite_from_payload(json.loads(run["suite_snapshot_json"])),
        execution_mode=QualificationExecutionMode(run["execution_mode"]),
        state=QualificationRunState(run["state"]),
        idempotency_key=run["idempotency_key"],
        created_at=datetime.fromisoformat(run["created_at"]),
        lease_token=run["lease_token"] or "",
        lease_expires_at=None if lease_expires is None else datetime.fromisoformat(lease_expires),
        correlation_id=run["correlation_id"],
    )


def _check_from_row(row: sqlite3.Row) -> QualificationCheckResult:
    score = row["score"]
    reason = row["reason_code"]
    passed = row["passed"]
    return QualificationCheckResult(
        check_code=row["check_code"],
        kind=QualificationCheckKind(row["kind"]),
        passed=None if passed is None else bool(passed),
        score=None if score in (None, "") else Decimal(score),
        reason_code=None if reason in (None, "") else QualificationReasonCode(reason),
    )


def _checks_for_case(
    connection: sqlite3.Connection, case_id: str
) -> tuple[QualificationCheckResult, ...]:
    rows = connection.execute(
        """
        SELECT * FROM qualification_check_results
        WHERE case_id = ?
        ORDER BY check_code
        """,
        (case_id,),
    ).fetchall()
    return tuple(_check_from_row(item) for item in rows)


def _case_from_row(
    row: sqlite3.Row,
    check_results: tuple[QualificationCheckResult, ...] = (),
) -> QualificationCaseResult:
    started = row["started_at"]
    finished = row["finished_at"]
    lease_expires = row["lease_expires_at"]
    reason = row["reason_code"]
    return QualificationCaseResult(
        case_id=row["id"],
        run_id=row["run_id"],
        fixture_id=row["fixture_id"],
        fixture_hash=row["fixture_hash"],
        input_hash=row["input_hash"],
        state=QualificationCaseState(row["state"]),
        idempotency_key=row["idempotency_key"],
        check_results=check_results,
        evidence=_evidence_from_row(row),
        started_at=None if started is None else datetime.fromisoformat(started),
        finished_at=None if finished is None else datetime.fromisoformat(finished),
        reason_code=None if reason in (None, "") else QualificationReasonCode(reason),
        lease_token=row["lease_token"] or "",
        lease_expires_at=None if lease_expires is None else datetime.fromisoformat(lease_expires),
    )


def _decision_from_row(row: sqlite3.Row, revision: sqlite3.Row) -> QualificationDecision:
    blockers = tuple(
        QualificationReasonCode(item) for item in json.loads(row["blocker_codes_json"])
    )
    score = row["advisory_score"]
    return QualificationDecision(
        decision_id=row["id"],
        capability=QualificationCapability(row["capability"]),
        route_revision=_route_from_row(revision),
        suite_version=row["suite_version"],
        status=QualificationDecisionStatus(row["status"]),
        run_id=row["run_id"],
        evaluated_at=datetime.fromisoformat(row["evaluated_at"]),
        blocker_codes=blockers,
        advisory_score=None if score is None else Decimal(str(score)),
    )


def _default_decision(capability: QualificationCapability, now: datetime) -> QualificationDecision:
    return QualificationDecision(
        decision_id=f"unqualified-{capability.value}",
        capability=capability,
        route_revision=_unconfigured_revision(capability),
        suite_version="none",
        status=QualificationDecisionStatus.UNQUALIFIED,
        run_id=None,
        evaluated_at=now,
        blocker_codes=(QualificationReasonCode.DEFAULT_DENIED,),
    )


class QualificationRepositoryMixin:
    async def save_qualification_route_revision(
        self, revision: QualificationRouteRevision, *, now: datetime | None = None
    ) -> QualificationRouteRevision:
        return await asyncio.to_thread(
            self._save_qualification_route_revision_sync, revision, _utc_now(now)
        )

    def _save_qualification_route_revision_sync(
        self, revision: QualificationRouteRevision, now: datetime
    ) -> QualificationRouteRevision:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO qualification_route_revisions(
                    fingerprint, capability, provider_protocol, sanitized_base_host,
                    model_identifier, generation_settings_json, suite_version,
                    price_catalog_revision, protection_policy_revision, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision.fingerprint,
                    revision.capability.value,
                    revision.provider_protocol,
                    revision.sanitized_base_host,
                    revision.model_identifier,
                    _json(list(revision.generation_settings)),
                    revision.suite_version,
                    revision.price_catalog_revision,
                    revision.protection_policy_revision,
                    _iso(now, "now"),
                ),
            )
            row = connection.execute(
                "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                (revision.fingerprint,),
            ).fetchone()
        return _route_from_row(row)

    async def create_qualification_preview(
        self, preview: QualificationPreview, *, now: datetime | None = None
    ) -> QualificationPreview:
        return await asyncio.to_thread(
            self._create_qualification_preview_sync, preview, _utc_now(now)
        )

    def _create_qualification_preview_sync(
        self, preview: QualificationPreview, now: datetime
    ) -> QualificationPreview:
        if preview.state is not QualificationPreviewState.READY:
            raise ValueError("new qualification previews must start ready")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            revision = connection.execute(
                "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                (preview.route_revision.fingerprint,),
            ).fetchone()
            if revision is None:
                raise ValueError("route revision snapshot is required before preview creation")
            connection.execute(
                """
                INSERT INTO qualification_previews(
                    id, confirmation_handle_hash, actor_id, process_instance_id, capability,
                    route_fingerprint, suite_id, suite_version, suite_snapshot_json,
                    policy_revision, fixture_ids_json, max_requests, max_input_tokens,
                    max_output_tokens, max_images, conservative_max_cost, execution_mode,
                    state, revision, correlation_id, created_at, expires_at
                ) VALUES(
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?
                )
                """,
                (
                    preview.preview_id,
                    preview.confirmation_handle_hash,
                    preview.actor_id,
                    preview.process_instance_id,
                    preview.capability.value,
                    preview.route_revision.fingerprint,
                    preview.suite.suite_id,
                    preview.suite.version,
                    _json(_suite_payload(preview.suite)),
                    preview.policy_revision,
                    _json(preview.fixture_ids),
                    preview.ceilings.max_requests,
                    preview.ceilings.max_input_tokens,
                    preview.ceilings.max_output_tokens,
                    preview.ceilings.max_images,
                    None
                    if preview.ceilings.conservative_max_cost is None
                    else str(preview.ceilings.conservative_max_cost),
                    preview.execution_mode.value,
                    preview.revision,
                    preview.correlation_id,
                    _iso(now, "now"),
                    _iso(preview.expires_at, "expires_at"),
                ),
            )
            stored = connection.execute(
                "SELECT * FROM qualification_previews WHERE id = ?", (preview.preview_id,)
            ).fetchone()
        return _preview_from_rows(stored, revision)

    async def qualification_preview(self, preview_id: str) -> QualificationPreview | None:
        return await asyncio.to_thread(self._qualification_preview_sync, preview_id)

    def _qualification_preview_sync(self, preview_id: str) -> QualificationPreview | None:
        with self._connect() as connection:
            preview = connection.execute(
                "SELECT * FROM qualification_previews WHERE id = ?", (preview_id,)
            ).fetchone()
            if preview is None:
                return None
            revision = connection.execute(
                "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                (preview["route_fingerprint"],),
            ).fetchone()
        return _preview_from_rows(preview, revision)

    async def qualification_preview_by_handle_hash(
        self, confirmation_handle_hash: str
    ) -> QualificationPreview | None:
        return await asyncio.to_thread(
            self._qualification_preview_by_handle_hash_sync, confirmation_handle_hash
        )

    def _qualification_preview_by_handle_hash_sync(
        self, confirmation_handle_hash: str
    ) -> QualificationPreview | None:
        with self._connect() as connection:
            preview = connection.execute(
                "SELECT * FROM qualification_previews WHERE confirmation_handle_hash = ?",
                (confirmation_handle_hash,),
            ).fetchone()
            if preview is None:
                return None
            revision = connection.execute(
                "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                (preview["route_fingerprint"],),
            ).fetchone()
        return _preview_from_rows(preview, revision)

    async def consume_qualification_preview(
        self,
        *,
        confirmation_handle_hash: str,
        actor_id: str,
        process_instance_id: str,
        route_fingerprint: str,
        suite_id: str,
        suite_version: str,
        policy_revision: str,
        now: datetime | None = None,
    ) -> QualificationPreview | None:
        return await asyncio.to_thread(
            self._consume_qualification_preview_sync,
            confirmation_handle_hash,
            actor_id,
            process_instance_id,
            route_fingerprint,
            suite_id,
            suite_version,
            policy_revision,
            _utc_now(now),
        )

    def _consume_qualification_preview_sync(
        self,
        confirmation_handle_hash: str,
        actor_id: str,
        process_instance_id: str,
        route_fingerprint: str,
        suite_id: str,
        suite_version: str,
        policy_revision: str,
        now: datetime,
    ) -> QualificationPreview | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM qualification_previews WHERE confirmation_handle_hash = ?",
                (confirmation_handle_hash,),
            ).fetchone()
            if row is None or row["state"] != QualificationPreviewState.READY.value:
                return None
            if datetime.fromisoformat(row["expires_at"]) < now:
                if can_transition_qualification_preview(
                    QualificationPreviewState.READY, QualificationPreviewState.EXPIRED
                ):
                    connection.execute(
                        """
                        UPDATE qualification_previews
                        SET state = 'expired', revision = revision + 1
                        WHERE id = ? AND state = 'ready'
                        """,
                        (row["id"],),
                    )
                return None
            if (
                row["actor_id"] != actor_id
                or row["process_instance_id"] != process_instance_id
                or row["route_fingerprint"] != route_fingerprint
                or row["suite_id"] != suite_id
                or row["suite_version"] != suite_version
                or row["policy_revision"] != policy_revision
            ):
                return None
            cursor = connection.execute(
                """
                UPDATE qualification_previews
                SET state = 'consumed', revision = revision + 1, consumed_at = ?
                WHERE id = ? AND state = 'ready' AND confirmation_handle_hash = ?
                """,
                (_iso(now, "now"), row["id"], confirmation_handle_hash),
            )
            if cursor.rowcount != 1:
                return None
            preview = connection.execute(
                "SELECT * FROM qualification_previews WHERE id = ?", (row["id"],)
            ).fetchone()
            revision = connection.execute(
                "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                (preview["route_fingerprint"],),
            ).fetchone()
        return _preview_from_rows(preview, revision)

    async def create_qualification_run(
        self, run: QualificationRun, *, now: datetime | None = None
    ) -> QualificationRun:
        return await asyncio.to_thread(self._create_qualification_run_sync, run, _utc_now(now))

    def _create_qualification_run_sync(
        self, run: QualificationRun, now: datetime
    ) -> QualificationRun:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM qualification_runs WHERE idempotency_key = ?",
                (run.idempotency_key,),
            ).fetchone()
            if existing is not None:
                revision = connection.execute(
                    "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                    (existing["route_fingerprint"],),
                ).fetchone()
                return _run_from_rows(existing, revision)
            preview = connection.execute(
                "SELECT * FROM qualification_previews WHERE id = ?", (run.preview_id,)
            ).fetchone()
            if preview is None or preview["state"] != QualificationPreviewState.CONSUMED.value:
                raise ValueError("qualification runs require a consumed preview")
            connection.execute(
                """
                INSERT INTO qualification_runs(
                    id, preview_id, actor_id, process_instance_id, capability,
                    route_fingerprint, suite_id, suite_version, suite_snapshot_json,
                    execution_mode, state, idempotency_key, lease_token, correlation_id,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.preview_id,
                    run.actor_id,
                    run.process_instance_id,
                    run.capability.value,
                    run.route_revision.fingerprint,
                    run.suite.suite_id,
                    run.suite.version,
                    _json(_suite_payload(run.suite)),
                    run.execution_mode.value,
                    QualificationRunState.PREPARED.value,
                    run.idempotency_key,
                    run.correlation_id,
                    _iso(now, "now"),
                    _iso(now, "now"),
                ),
            )
            for fixture_id in json.loads(preview["fixture_ids_json"]):
                connection.execute(
                    """
                    INSERT INTO qualification_cases(
                        id, run_id, fixture_id, fixture_hash, input_hash, state,
                        idempotency_key, image_count
                    ) VALUES(?, ?, ?, ?, ?, 'pending', ?, 0)
                    """,
                    (
                        str(uuid4()),
                        run.run_id,
                        fixture_id,
                        _digest(str(fixture_id)),
                        _digest(f"{fixture_id}:input"),
                        f"{run.run_id}:{fixture_id}",
                    ),
                )
            stored = connection.execute(
                "SELECT * FROM qualification_runs WHERE id = ?", (run.run_id,)
            ).fetchone()
            revision = connection.execute(
                "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                (stored["route_fingerprint"],),
            ).fetchone()
        return _run_from_rows(stored, revision)

    async def qualification_cases(self, run_id: str) -> tuple[QualificationCaseResult, ...]:
        return await asyncio.to_thread(self._qualification_cases_sync, run_id)

    def _qualification_cases_sync(self, run_id: str) -> tuple[QualificationCaseResult, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM qualification_cases
                WHERE run_id = ?
                ORDER BY fixture_id
                """,
                (run_id,),
            ).fetchall()
            return tuple(
                _case_from_row(row, _checks_for_case(connection, row["id"])) for row in rows
            )

    async def record_qualification_check_results(
        self,
        *,
        case_id: str,
        check_results: tuple[QualificationCheckResult, ...],
    ) -> None:
        await asyncio.to_thread(
            self._record_qualification_check_results_sync,
            case_id,
            check_results,
        )

    def _record_qualification_check_results_sync(
        self,
        case_id: str,
        check_results: tuple[QualificationCheckResult, ...],
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM qualification_check_results WHERE case_id = ?",
                (case_id,),
            )
            connection.executemany(
                """
                INSERT INTO qualification_check_results(
                    case_id, check_code, kind, passed, score, reason_code
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        case_id,
                        item.check_code,
                        item.kind.value,
                        None if item.passed is None else int(item.passed),
                        None if item.score is None else str(item.score),
                        None if item.reason_code is None else item.reason_code.value,
                    )
                    for item in check_results
                ],
            )

    async def qualification_run(self, run_id: str) -> QualificationRun | None:
        return await asyncio.to_thread(self._qualification_run_sync, run_id)

    def _qualification_run_sync(self, run_id: str) -> QualificationRun | None:
        with self._connect() as connection:
            stored = connection.execute(
                "SELECT * FROM qualification_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if stored is None:
                return None
            revision = connection.execute(
                "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                (stored["route_fingerprint"],),
            ).fetchone()
        return _run_from_rows(stored, revision)

    async def transition_qualification_run(
        self,
        *,
        run_id: str,
        from_state: QualificationRunState,
        to_state: QualificationRunState,
        now: datetime | None = None,
    ) -> QualificationRun | None:
        return await asyncio.to_thread(
            self._transition_qualification_run_sync,
            run_id,
            from_state,
            to_state,
            _utc_now(now),
        )

    def _transition_qualification_run_sync(
        self,
        run_id: str,
        from_state: QualificationRunState,
        to_state: QualificationRunState,
        now: datetime,
    ) -> QualificationRun | None:
        if not can_transition_qualification_run(from_state, to_state):
            return None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE qualification_runs
                SET state = ?, updated_at = ?
                WHERE id = ? AND state = ?
                """,
                (to_state.value, _iso(now, "now"), run_id, from_state.value),
            )
            if cursor.rowcount != 1:
                return None
            stored = connection.execute(
                "SELECT * FROM qualification_runs WHERE id = ?", (run_id,)
            ).fetchone()
            revision = connection.execute(
                "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                (stored["route_fingerprint"],),
            ).fetchone()
        return _run_from_rows(stored, revision)

    async def claim_qualification_case(
        self,
        *,
        run_id: str,
        fixture_id: str,
        lease_token: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> QualificationCaseResult | None:
        if ttl_seconds < 1 or not lease_token:
            raise ValueError("case claims require a lease token and positive ttl")
        return await asyncio.to_thread(
            self._claim_qualification_case_sync,
            run_id,
            fixture_id,
            lease_token,
            ttl_seconds,
            _utc_now(now),
        )

    def _claim_qualification_case_sync(
        self,
        run_id: str,
        fixture_id: str,
        lease_token: str,
        ttl_seconds: int,
        now: datetime,
    ) -> QualificationCaseResult | None:
        expires_at = now + timedelta(seconds=ttl_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT * FROM qualification_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None or run["state"] != QualificationRunState.RUNNING.value:
                return None
            case = connection.execute(
                """
                SELECT * FROM qualification_cases
                WHERE run_id = ? AND fixture_id = ?
                """,
                (run_id, fixture_id),
            ).fetchone()
            if case is None:
                return None
            state = QualificationCaseState(case["state"])
            lease_expires = case["lease_expires_at"]
            expired = lease_expires is not None and datetime.fromisoformat(lease_expires) <= now
            if not (
                state is QualificationCaseState.PENDING
                or (state is QualificationCaseState.RUNNING and expired)
            ):
                return None
            cursor = connection.execute(
                """
                UPDATE qualification_cases
                SET state = 'running',
                    lease_token = ?,
                    lease_expires_at = ?,
                    started_at = COALESCE(started_at, ?)
                WHERE id = ?
                  AND state IN ('pending', 'running')
                """,
                (
                    lease_token,
                    _iso(expires_at, "lease_expires_at"),
                    _iso(now, "now"),
                    case["id"],
                ),
            )
            if cursor.rowcount != 1:
                return None
            updated = connection.execute(
                "SELECT * FROM qualification_cases WHERE id = ?", (case["id"],)
            ).fetchone()
        return _case_from_row(updated)

    async def transition_qualification_case(
        self,
        *,
        case_id: str,
        from_state: QualificationCaseState,
        to_state: QualificationCaseState,
        lease_token: str,
        now: datetime | None = None,
        reason_code: QualificationReasonCode | None = None,
        latency_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        image_count: int | None = None,
        response_hash: str | None = None,
    ) -> QualificationCaseResult | None:
        return await asyncio.to_thread(
            self._transition_qualification_case_sync,
            case_id,
            from_state,
            to_state,
            lease_token,
            _utc_now(now),
            reason_code,
            latency_ms,
            input_tokens,
            output_tokens,
            image_count,
            response_hash,
        )

    def _transition_qualification_case_sync(
        self,
        case_id: str,
        from_state: QualificationCaseState,
        to_state: QualificationCaseState,
        lease_token: str,
        now: datetime,
        reason_code: QualificationReasonCode | None,
        latency_ms: int | None,
        input_tokens: int | None,
        output_tokens: int | None,
        image_count: int | None,
        response_hash: str | None,
    ) -> QualificationCaseResult | None:
        if not can_transition_qualification_case(from_state, to_state):
            return None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            case = connection.execute(
                "SELECT * FROM qualification_cases WHERE id = ?", (case_id,)
            ).fetchone()
            if case is None or case["state"] != from_state.value:
                return None
            lease_expires = case["lease_expires_at"]
            if (
                case["lease_token"] != lease_token
                or lease_expires is None
                or datetime.fromisoformat(lease_expires) <= now
            ):
                return None
            finished = _iso(now, "now") if to_state.terminal else None
            cursor = connection.execute(
                """
                UPDATE qualification_cases
                SET state = ?,
                    finished_at = COALESCE(?, finished_at),
                    reason_code = COALESCE(?, reason_code),
                    latency_ms = COALESCE(?, latency_ms),
                    input_tokens = COALESCE(?, input_tokens),
                    output_tokens = COALESCE(?, output_tokens),
                    image_count = COALESCE(?, image_count),
                    response_hash = COALESCE(?, response_hash)
                WHERE id = ? AND state = ? AND lease_token = ?
                """,
                (
                    to_state.value,
                    finished,
                    None if reason_code is None else reason_code.value,
                    latency_ms,
                    input_tokens,
                    output_tokens,
                    image_count,
                    response_hash,
                    case_id,
                    from_state.value,
                    lease_token,
                ),
            )
            if cursor.rowcount != 1:
                return None
            updated = connection.execute(
                "SELECT * FROM qualification_cases WHERE id = ?", (case_id,)
            ).fetchone()
        return _case_from_row(updated)

    async def mark_qualification_case_provider_attempt(
        self,
        *,
        case_id: str,
        lease_token: str,
        provider_request_id: str,
        now: datetime | None = None,
    ) -> QualificationCaseResult | None:
        return await asyncio.to_thread(
            self._mark_qualification_case_provider_attempt_sync,
            case_id,
            lease_token,
            provider_request_id,
            _utc_now(now),
        )

    def _mark_qualification_case_provider_attempt_sync(
        self,
        case_id: str,
        lease_token: str,
        provider_request_id: str,
        now: datetime,
    ) -> QualificationCaseResult | None:
        del now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE qualification_cases
                SET provider_request_id = ?
                WHERE id = ? AND state = 'running' AND lease_token = ?
                """,
                (provider_request_id, case_id, lease_token),
            )
            if cursor.rowcount != 1:
                return None
            updated = connection.execute(
                "SELECT * FROM qualification_cases WHERE id = ?", (case_id,)
            ).fetchone()
        return _case_from_row(updated)

    async def reconcile_qualification_leases(
        self, *, now: datetime | None = None
    ) -> tuple[str, ...]:
        return await asyncio.to_thread(self._reconcile_qualification_leases_sync, _utc_now(now))

    def _reconcile_qualification_leases_sync(self, now: datetime) -> tuple[str, ...]:
        marked: list[str] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM qualification_cases
                WHERE state = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                  AND provider_request_id IS NOT NULL
                  AND provider_request_id != ''
                """,
                (_iso(now, "now"),),
            ).fetchall()
            for row in rows:
                cursor = connection.execute(
                    """
                    UPDATE qualification_cases
                    SET state = 'inconclusive',
                        reason_code = ?,
                        lease_token = '',
                        finished_at = ?
                    WHERE id = ? AND state = 'running'
                    """,
                    (
                        QualificationReasonCode.AMBIGUOUS_INTERRUPTION.value,
                        _iso(now, "now"),
                        row["id"],
                    ),
                )
                if cursor.rowcount == 1:
                    marked.append(str(row["id"]))
        return tuple(marked)

    async def cancel_qualification_run(
        self, run_id: str, *, now: datetime | None = None
    ) -> QualificationRun | None:
        return await asyncio.to_thread(self._cancel_qualification_run_sync, run_id, _utc_now(now))

    def _cancel_qualification_run_sync(self, run_id: str, now: datetime) -> QualificationRun | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT * FROM qualification_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                return None
            current = QualificationRunState(run["state"])
            if not can_transition_qualification_run(current, QualificationRunState.CANCELLED):
                return None
            cursor = connection.execute(
                """
                UPDATE qualification_runs
                SET state = 'cancelled', updated_at = ?
                WHERE id = ? AND state = ?
                """,
                (_iso(now, "now"), run_id, current.value),
            )
            if cursor.rowcount != 1:
                return None
            connection.execute(
                """
                UPDATE qualification_cases
                SET state = 'cancelled', finished_at = COALESCE(finished_at, ?)
                WHERE run_id = ? AND state = 'pending'
                """,
                (_iso(now, "now"), run_id),
            )
            stored = connection.execute(
                "SELECT * FROM qualification_runs WHERE id = ?", (run_id,)
            ).fetchone()
            revision = connection.execute(
                "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                (stored["route_fingerprint"],),
            ).fetchone()
        return _run_from_rows(stored, revision)

    async def block_qualification_run(
        self,
        run_id: str,
        *,
        reason_code: QualificationReasonCode,
        now: datetime | None = None,
    ) -> QualificationRun | None:
        return await asyncio.to_thread(
            self._block_qualification_run_sync, run_id, reason_code, _utc_now(now)
        )

    def _block_qualification_run_sync(
        self,
        run_id: str,
        reason_code: QualificationReasonCode,
        now: datetime,
    ) -> QualificationRun | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT * FROM qualification_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                return None
            current = QualificationRunState(run["state"])
            if not can_transition_qualification_run(current, QualificationRunState.BLOCKED):
                return None
            cursor = connection.execute(
                """
                UPDATE qualification_runs
                SET state = 'blocked', updated_at = ?
                WHERE id = ? AND state = ?
                """,
                (_iso(now, "now"), run_id, current.value),
            )
            if cursor.rowcount != 1:
                return None
            connection.execute(
                """
                UPDATE qualification_cases
                SET state = 'blocked',
                    reason_code = COALESCE(reason_code, ?),
                    finished_at = COALESCE(finished_at, ?)
                WHERE run_id = ? AND state = 'pending'
                """,
                (reason_code.value, _iso(now, "now"), run_id),
            )
            stored = connection.execute(
                "SELECT * FROM qualification_runs WHERE id = ?", (run_id,)
            ).fetchone()
            revision = connection.execute(
                "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                (stored["route_fingerprint"],),
            ).fetchone()
        return _run_from_rows(stored, revision)

    async def record_qualification_decision(
        self,
        *,
        capability: QualificationCapability,
        route_revision: QualificationRouteRevision,
        suite_version: str,
        status: QualificationDecisionStatus,
        run_id: str | None,
        evaluated_at: datetime,
        advisory_score: Decimal | None = None,
        blocker_codes: tuple[QualificationReasonCode, ...] = (),
    ) -> QualificationDecision:
        return await asyncio.to_thread(
            self._record_qualification_decision_sync,
            capability,
            route_revision,
            suite_version,
            status,
            run_id,
            evaluated_at,
            advisory_score,
            blocker_codes,
        )

    def _record_qualification_decision_sync(
        self,
        capability: QualificationCapability,
        route_revision: QualificationRouteRevision,
        suite_version: str,
        status: QualificationDecisionStatus,
        run_id: str | None,
        evaluated_at: datetime,
        advisory_score: Decimal | None,
        blocker_codes: tuple[QualificationReasonCode, ...],
    ) -> QualificationDecision:
        decision_id = f"decision-{capability.value}-{route_revision.fingerprint[:12]}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO qualification_decisions(
                    id, capability, route_fingerprint, suite_version, status, run_id,
                    advisory_score, blocker_codes_json, evaluated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(capability, route_fingerprint, suite_version) DO UPDATE SET
                    status = excluded.status,
                    run_id = excluded.run_id,
                    advisory_score = excluded.advisory_score,
                    blocker_codes_json = excluded.blocker_codes_json,
                    evaluated_at = excluded.evaluated_at
                """,
                (
                    decision_id,
                    capability.value,
                    route_revision.fingerprint,
                    suite_version,
                    status.value,
                    run_id,
                    None if advisory_score is None else str(advisory_score),
                    _json([item.value for item in blocker_codes]),
                    _iso(evaluated_at, "evaluated_at"),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM qualification_decisions
                WHERE capability = ? AND route_fingerprint = ? AND suite_version = ?
                """,
                (capability.value, route_revision.fingerprint, suite_version),
            ).fetchone()
            revision = connection.execute(
                "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                (route_revision.fingerprint,),
            ).fetchone()
        return _decision_from_row(row, revision)

    async def qualification_decision(
        self, capability: QualificationCapability, *, now: datetime | None = None
    ) -> QualificationDecision:
        return await asyncio.to_thread(self._qualification_decision_sync, capability, _utc_now(now))

    def _qualification_decision_sync(
        self, capability: QualificationCapability, now: datetime
    ) -> QualificationDecision:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM qualification_decisions
                WHERE capability = ?
                ORDER BY evaluated_at DESC, id DESC
                LIMIT 1
                """,
                (capability.value,),
            ).fetchone()
            if row is None:
                return _default_decision(capability, now)
            revision = connection.execute(
                "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                (row["route_fingerprint"],),
            ).fetchone()
        return _decision_from_row(row, revision)

    async def refresh_stale_qualification_decisions(
        self,
        *,
        current_revision: QualificationRouteRevision,
        current_suite_version: str,
        now: datetime | None = None,
    ) -> QualificationDecision:
        return await asyncio.to_thread(
            self._refresh_stale_qualification_decisions_sync,
            current_revision,
            current_suite_version,
            _utc_now(now),
        )

    def _refresh_stale_qualification_decisions_sync(
        self,
        current_revision: QualificationRouteRevision,
        current_suite_version: str,
        now: datetime,
    ) -> QualificationDecision:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE qualification_decisions
                SET status = 'stale',
                    blocker_codes_json = ?,
                    evaluated_at = ?
                WHERE capability = ?
                  AND status = 'passed'
                  AND (
                    route_fingerprint != ?
                    OR suite_version != ?
                    OR route_fingerprint IN (
                        SELECT fingerprint FROM qualification_route_revisions
                        WHERE protection_policy_revision != ?
                           OR price_catalog_revision != ?
                    )
                  )
                """,
                (
                    _json([QualificationReasonCode.STALE_ROUTE_REVISION.value]),
                    _iso(now, "now"),
                    current_revision.capability.value,
                    current_revision.fingerprint,
                    current_suite_version,
                    current_revision.protection_policy_revision,
                    current_revision.price_catalog_revision,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM qualification_decisions
                WHERE capability = ?
                ORDER BY evaluated_at DESC, id DESC
                LIMIT 1
                """,
                (current_revision.capability.value,),
            ).fetchone()
            if row is None:
                return _default_decision(current_revision.capability, now)
            revision = connection.execute(
                "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                (row["route_fingerprint"],),
            ).fetchone()
        return _decision_from_row(row, revision)

    async def record_qualification_cost_evidence(
        self,
        *,
        run_id: str,
        price_catalog_revision: str,
        planned_max_cost: Decimal | None,
        observed_cost: Decimal | None,
        currency: str,
        request_count: int,
        input_tokens: int,
        output_tokens: int,
        image_count: int,
        now: datetime | None = None,
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            self._record_qualification_cost_evidence_sync,
            run_id,
            price_catalog_revision,
            planned_max_cost,
            observed_cost,
            currency,
            request_count,
            input_tokens,
            output_tokens,
            image_count,
            _utc_now(now),
        )

    def _record_qualification_cost_evidence_sync(
        self,
        run_id: str,
        price_catalog_revision: str,
        planned_max_cost: Decimal | None,
        observed_cost: Decimal | None,
        currency: str,
        request_count: int,
        input_tokens: int,
        output_tokens: int,
        image_count: int,
        now: datetime,
    ) -> dict[str, object]:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO qualification_cost_evidence(
                    run_id, price_catalog_revision, planned_max_cost, observed_cost,
                    currency, request_count, input_tokens, output_tokens, image_count,
                    updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    price_catalog_revision = excluded.price_catalog_revision,
                    planned_max_cost = excluded.planned_max_cost,
                    observed_cost = excluded.observed_cost,
                    currency = excluded.currency,
                    request_count = excluded.request_count,
                    input_tokens = excluded.input_tokens,
                    output_tokens = excluded.output_tokens,
                    image_count = excluded.image_count,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    price_catalog_revision,
                    None if planned_max_cost is None else str(planned_max_cost),
                    None if observed_cost is None else str(observed_cost),
                    currency,
                    request_count,
                    input_tokens,
                    output_tokens,
                    image_count,
                    _iso(now, "now"),
                ),
            )
        return {
            "run_id": run_id,
            "price_catalog_revision": price_catalog_revision,
            "planned_max_cost": None if planned_max_cost is None else str(planned_max_cost),
            "currency": currency,
        }

    async def qualification_cost_evidence(self, run_id: str) -> dict[str, object] | None:
        return await asyncio.to_thread(self._qualification_cost_evidence_sync, run_id)

    def _qualification_cost_evidence_sync(self, run_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM qualification_cost_evidence WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "price_catalog_revision": row["price_catalog_revision"],
            "planned_max_cost": row["planned_max_cost"],
            "currency": row["currency"],
            "request_count": row["request_count"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "image_count": row["image_count"],
        }

    async def record_qualification_artifact(
        self,
        *,
        artifact_id: str,
        case_id: str,
        relative_path: str,
        content_sha256: str,
        mime_type: str,
        size_bytes: int,
        pixel_width: int | None,
        pixel_height: int | None,
        now: datetime | None = None,
    ) -> QualificationStoredArtifact:
        return await asyncio.to_thread(
            self._record_qualification_artifact_sync,
            artifact_id,
            case_id,
            relative_path,
            content_sha256,
            mime_type,
            size_bytes,
            pixel_width,
            pixel_height,
            _utc_now(now),
        )

    def _record_qualification_artifact_sync(
        self,
        artifact_id: str,
        case_id: str,
        relative_path: str,
        content_sha256: str,
        mime_type: str,
        size_bytes: int,
        pixel_width: int | None,
        pixel_height: int | None,
        now: datetime,
    ) -> QualificationStoredArtifact:
        if not relative_path.startswith("qualification/") or ".." in relative_path.split("/"):
            raise ValueError("qualification image path is not confined")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO qualification_artifacts(
                    id, case_id, relative_path, content_sha256, mime_type, size_bytes,
                    pixel_width, pixel_height, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    case_id,
                    relative_path,
                    content_sha256,
                    mime_type,
                    size_bytes,
                    pixel_width,
                    pixel_height,
                    _iso(now, "now"),
                ),
            )
        return QualificationStoredArtifact(
            artifact_id=artifact_id,
            case_id=case_id,
            relative_path=relative_path,
            content_sha256=content_sha256,
            mime_type=mime_type,
            size_bytes=size_bytes,
            pixel_width=pixel_width,
            pixel_height=pixel_height,
        )

    async def list_qualification_runs(
        self,
        *,
        capability: QualificationCapability | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[tuple[QualificationRun, ...], int]:
        return await asyncio.to_thread(
            self._list_qualification_runs_sync, capability, limit, offset
        )

    def _list_qualification_runs_sync(
        self,
        capability: QualificationCapability | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[QualificationRun, ...], int]:
        where = ""
        parameters: list[Any] = []
        if capability is not None:
            where = "WHERE qualification_runs.capability = ?"
            parameters.append(capability.value)
        with self._connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM qualification_runs {where}",
                    parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT qualification_runs.*
                FROM qualification_runs
                {where}
                ORDER BY qualification_runs.created_at DESC, qualification_runs.id DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
            runs: list[QualificationRun] = []
            for row in rows:
                revision = connection.execute(
                    "SELECT * FROM qualification_route_revisions WHERE fingerprint = ?",
                    (row["route_fingerprint"],),
                ).fetchone()
                runs.append(_run_from_rows(row, revision))
        return tuple(runs), total

    async def qualification_artifacts(self, run_id: str) -> tuple[QualificationStoredArtifact, ...]:
        return await asyncio.to_thread(self._qualification_artifacts_sync, run_id)

    def _qualification_artifacts_sync(self, run_id: str) -> tuple[QualificationStoredArtifact, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT artifact.*
                FROM qualification_artifacts AS artifact
                JOIN qualification_cases AS case_row ON case_row.id = artifact.case_id
                WHERE case_row.run_id = ?
                ORDER BY artifact.created_at, artifact.id
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            QualificationStoredArtifact(
                artifact_id=row["id"],
                case_id=row["case_id"],
                relative_path=row["relative_path"],
                content_sha256=row["content_sha256"],
                mime_type=row["mime_type"],
                size_bytes=int(row["size_bytes"]),
                pixel_width=row["pixel_width"],
                pixel_height=row["pixel_height"],
            )
            for row in rows
        )
