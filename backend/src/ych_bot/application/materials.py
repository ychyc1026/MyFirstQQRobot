"""Proactive materials with AI claim verification."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ych_bot.domain.modeling import ChatGenerationRequest, ChatModelGateway, ModelMessage, ModelRole
from ych_bot.infrastructure.database import SQLiteRepository

from .search import WebSearchClient


class MaterialError(ValueError):
    """Raised when a material request is invalid."""


class MaterialService:
    def __init__(
        self,
        repository: SQLiteRepository,
        gateway: ChatModelGateway,
        search: WebSearchClient,
        *,
        owner_qq: str,
        bot_qq: str,
        model_route: str,
        enabled: bool,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._search = search
        self._owner_qq = owner_qq
        self._bot_qq = bot_qq
        self._model_route = model_route
        self._enabled = enabled

    async def list_items(self) -> list[dict[str, Any]]:
        return await self._repository.proactive_materials()

    async def create(
        self,
        *,
        title: str,
        content: str,
        uploader_claim: str,
        file_name: str = "",
        target_qq: str = "",
    ) -> dict[str, Any]:
        title = title.strip()
        content = content.strip()
        claim = uploader_claim.strip()
        if not title:
            raise MaterialError("标题不能为空")
        if not content:
            raise MaterialError("材料正文不能为空")
        if not claim:
            raise MaterialError("请说明这是什么，模型会核对")
        if len(content) > 20_000:
            raise MaterialError("材料正文最多 2 万字")
        target_qq = target_qq.strip()
        if target_qq and (not target_qq.isdigit() or not (5 <= len(target_qq) <= 20)):
            raise MaterialError("target_qq must contain 5 to 20 digits")
        created = await self._repository.create_proactive_material(
            title=title,
            content=content,
            uploader_claim=claim,
            file_name=file_name,
            created_by=self._owner_qq,
            now=datetime.now(UTC),
        )
        return await self._verify(created, target_qq=target_qq)

    async def set_enabled(self, material_id: str, *, enabled: bool) -> dict[str, Any]:
        items = await self._repository.proactive_materials()
        current = next((item for item in items if item["id"] == material_id), None)
        if current is None:
            raise MaterialError("找不到这条材料")
        if enabled and current.get("ai_verdict") != "match":
            raise MaterialError("只有核对相符的材料才能启用")
        await self._repository.set_material_enabled(
            material_id,
            enabled=enabled,
            now=datetime.now(UTC),
        )
        current["enabled"] = int(enabled)
        return current

    async def _verify(self, material: dict[str, Any], *, target_qq: str) -> dict[str, Any]:
        if not self._enabled:
            updated = await self._repository.update_material_verdict(
                material["id"],
                verdict="mismatch",
                reason="统计或对话模型关闭，无法核对",
                enabled=False,
                now=datetime.now(UTC),
            )
            return updated or material
        search_notes = ""
        if self._search.enabled:
            search_notes = await self._search.search(
                f"{material['title']} {material['uploader_claim']}"
            )
        prompt = (
            f"标题：{material['title']}\n"
            f"上传者说明：{material['uploader_claim']}\n"
            f"材料正文：{material['content'][:4000]}\n"
            f"检索摘录：{search_notes[:1500] or '无'}\n"
            "判断说明是否与材料相符。第一行只输出 MATCH 或 MISMATCH，第二行给简短理由。"
        )
        result = await self._gateway.generate(
            ChatGenerationRequest(
                request_id=str(uuid4()),
                messages=(
                    ModelMessage(
                        role=ModelRole.SYSTEM,
                        content="你核对材料说明，不能盲从上传者。说明不实就判定 MISMATCH。",
                    ),
                    ModelMessage(role=ModelRole.USER, content=prompt),
                ),
                max_output_tokens=200,
                temperature=0.1,
            )
        )
        actor = target_qq.strip() or self._owner_qq
        await self._repository.record_usage_run(
            run_id=str(uuid4()),
            source_message_id=f"material:{material['id']}",
            conversation_key=f"{self._bot_qq}:private:{actor}",
            actor_qq=actor,
            bot_qq=self._bot_qq,
            mode="material_verify",
            model_route=self._model_route or "chat",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        first_line = (
            (result.text or "").strip().splitlines()[0].strip().upper() if result.text else ""
        )
        reason = "\n".join((result.text or "").splitlines()[1:]).strip()[:500]
        match = first_line == "MATCH"
        updated = await self._repository.update_material_verdict(
            material["id"],
            verdict="match" if match else "mismatch",
            reason=reason or ("matched" if match else "claim_mismatch"),
            enabled=match,
            now=datetime.now(UTC),
        )
        return updated or material
