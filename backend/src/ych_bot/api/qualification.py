"""Authenticated model-route qualification HTTP surface."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ych_bot.application.qualification import (
    QualificationApiService,
    QualificationPlanningError,
)
from ych_bot.domain.model_qualification import QualificationCapability, QualificationReasonCode


class QualificationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    capability: QualificationCapability
    fixture_ids: list[str] | None = None
    max_input_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    max_images: int | None = Field(default=None, ge=1)


class QualificationConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    confirmation_handle: str = Field(min_length=8, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)


def _planning_http_exception(exc: QualificationPlanningError) -> HTTPException:
    status_code = 404 if str(exc) == "run not found" else 409
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.reason_code.value, "message": str(exc)},
    )


def register_qualification_routes(
    app: FastAPI,
    *,
    require_admin: Callable[[str], Awaitable[str]],
    service: QualificationApiService,
) -> None:
    @app.get("/api/v1/qualification/suites")
    async def qualification_suites(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        return service.suite_metadata()

    @app.get("/api/v1/qualification/routes")
    async def qualification_routes(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        return service.current_routes()

    @app.get("/api/v1/qualification/decisions")
    async def qualification_decisions(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        return await service.current_decisions()

    @app.post("/api/v1/qualification/previews")
    async def qualification_previews(
        body: QualificationPreviewRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        token = await require_admin(authorization)
        actor_id = hashlib.sha256(token.encode("utf-8")).hexdigest()
        try:
            return await service.preview_controlled_live(
                actor_id=actor_id,
                payload=body.model_dump(exclude_none=False),
            )
        except QualificationPlanningError as exc:
            raise _planning_http_exception(exc) from exc

    @app.post("/api/v1/qualification/confirmations")
    async def qualification_confirmations(
        body: QualificationConfirmRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        token = await require_admin(authorization)
        actor_id = hashlib.sha256(token.encode("utf-8")).hexdigest()
        try:
            return await service.confirm_controlled_live(
                actor_id=actor_id,
                payload=body.model_dump(exclude_none=False),
            )
        except QualificationPlanningError as exc:
            raise _planning_http_exception(exc) from exc

    @app.post("/api/v1/qualification/runs/{run_id}/cancel")
    async def cancel_qualification_run(
        run_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await service.cancel_run(run_id)
        except QualificationPlanningError as exc:
            raise _planning_http_exception(exc) from exc

    @app.get("/api/v1/qualification/runs")
    async def list_qualification_runs(
        capability: str | None = None,
        limit: int = 20,
        offset: int = 0,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await service.list_runs(capability=capability, limit=limit, offset=offset)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": QualificationReasonCode.DEFAULT_DENIED.value,
                    "message": str(exc),
                },
            ) from exc

    @app.get("/api/v1/qualification/runs/{run_id}")
    async def qualification_run_detail(
        run_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        detail = await service.run_detail(run_id)
        if detail is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": QualificationReasonCode.DEFAULT_DENIED.value,
                    "message": "run not found",
                },
            )
        return detail
