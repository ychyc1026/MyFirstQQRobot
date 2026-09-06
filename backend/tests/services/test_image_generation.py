from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from ych_bot.application import ImageGenerationService, ImageGenerationTaskError
from ych_bot.application.control import OwnerControlService
from ych_bot.domain.control import ControlSource, OwnerCommand, OwnerCommandKind
from ych_bot.domain.images import ImageIntendedUse, ImageReviewResult, ImageTaskStatus
from ych_bot.domain.modeling import ImageGenerationRequest, ImageGenerationResult
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.napcat.client import NapCatClient

OWNER_QQ = "2000000001"
BOT_QQ = "2000000002"
PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42Y"
    "AAAAASUVORK5CYII="
)


class FakeImageReviewEngine:
    def __init__(self, verdict: str = "allow") -> None:
        self.verdict = verdict
        self.calls: list[str] = []

    def review(
        self,
        *,
        content: bytes,
        media_type: str,
        content_sha256: str,
    ) -> ImageReviewResult:
        del content, media_type
        self.calls.append(content_sha256)
        return ImageReviewResult(verdict=self.verdict, labels=("fake",), engine="fake")


class FakeImageGateway:
    def __init__(self, artifacts: tuple[str, ...]) -> None:
        self.artifacts = artifacts
        self.requests: list[ImageGenerationRequest] = []

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        self.requests.append(request)
        return ImageGenerationResult(
            artifacts=self.artifacts,
            provider_request_id="provider-image-request",
        )

    async def close(self) -> None:
        return None


def service(
    repository: SQLiteRepository,
    gateway: FakeImageGateway,
    project_root: Path,
    *,
    enabled: bool = True,
    review_enabled: bool = False,
    review_engine: FakeImageReviewEngine | None = None,
    orphan_scan_enabled: bool = False,
) -> ImageGenerationService:
    return ImageGenerationService(
        repository,
        gateway,
        project_root=project_root,
        owner_qq=OWNER_QQ,
        model_route="fake-image-model",
        enabled=enabled,
        max_artifact_megabytes=2,
        max_pixels=1_000_000,
        max_artifacts=2,
        review_enabled=review_enabled,
        review_engine=review_engine,
        orphan_scan_enabled=orphan_scan_enabled,
    )


def control_command(kind: OwnerCommandKind, arguments: dict[str, str]) -> OwnerCommand:
    command_id = str(uuid4())
    return OwnerCommand(
        id=command_id,
        kind=kind,
        actor_qq=OWNER_QQ,
        source_message_id=f"image-test:{command_id}",
        arguments=arguments,
        source=ControlSource.DASHBOARD,
    )


def no_network_transport(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected NapCat request: {request.url}")


@pytest.mark.asyncio
async def test_image_preview_is_local_reported_and_approved_without_outbox(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "image.sqlite3")
    await repository.initialize()
    gateway = FakeImageGateway((PNG_DATA_URI,))
    image_service = service(repository, gateway, tmp_path)

    created = await image_service.create_task(
        prompt="A restrained YCH dashboard illustration",
        intended_use=ImageIntendedUse.QZONE_POST,
        requested_by=OWNER_QQ,
        request_source="dashboard",
    )
    assert created["status"] == "draft"

    generated = await image_service.generate(created["id"])

    assert generated["status"] == "pending_approval"
    assert generated["provider_request_id"] == "provider-image-request"
    assert len(generated["artifacts"]) == 1
    artifact = generated["artifacts"][0]
    assert (artifact["width"], artifact["height"], artifact["media_type"]) == (
        1,
        1,
        "image/png",
    )
    artifact_path, media_type = await image_service.artifact_file(artifact["id"])
    assert artifact_path.is_file()
    assert tmp_path.resolve() in artifact_path.resolve().parents
    assert media_type == "image/png"

    approvals = await repository.pending_approvals()
    reports = await repository.owner_reports()
    assert approvals[0]["request_type"] == "image_generation.use"
    assert approvals[0]["subject_id"] == created["id"]
    assert reports[0]["severity"] == "action_required"
    assert reports[0]["related_id"] == created["id"]
    assert approvals[0]["approval_code"] in reports[0]["body"]
    assert (await repository.counts())["outbox"] == 0

    napcat = NapCatClient(
        "http://napcat.test",
        transport=httpx.MockTransport(no_network_transport),
    )
    controls = OwnerControlService(
        repository,
        napcat,
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        image_generation_service=image_service,
    )
    try:
        decision = await controls.execute(
            control_command(
                OwnerCommandKind.APPROVE,
                {"approval_code": approvals[0]["approval_code"]},
            )
        )
    finally:
        await napcat.close()

    assert decision.data["approved"] is True
    approved = await image_service.task(created["id"])
    assert approved["status"] == "approved"
    assert approved["approved_by"] == OWNER_QQ
    assert (await repository.owner_reports())[0]["status"] == "acknowledged"
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_remote_url_is_rejected_without_download_or_preview(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "remote.sqlite3")
    await repository.initialize()
    gateway = FakeImageGateway(("https://untrusted.example/image.png",))
    image_service = service(repository, gateway, tmp_path)
    created = await image_service.create_task(
        prompt="do not fetch remote artifacts",
        intended_use=ImageIntendedUse.GENERAL,
        requested_by=OWNER_QQ,
        request_source="owner_qq",
    )

    with pytest.raises(ImageGenerationTaskError, match="not downloaded automatically"):
        await image_service.generate(created["id"])

    failed = await image_service.task(created["id"])
    assert failed["status"] == "failed"
    assert failed["artifacts"] == []
    assert await repository.pending_approvals() == []
    assert await repository.owner_reports() == []
    task_directory = tmp_path / "storage" / "generated" / "images" / created["id"]
    assert not task_directory.exists()


@pytest.mark.asyncio
async def test_disabled_image_service_only_stages_task(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "disabled.sqlite3")
    await repository.initialize()
    gateway = FakeImageGateway((PNG_DATA_URI,))
    image_service = service(repository, gateway, tmp_path, enabled=False)
    created = await image_service.create_task(
        prompt="wait for configuration",
        intended_use=ImageIntendedUse.GENERAL,
        requested_by=OWNER_QQ,
        request_source="dashboard",
    )

    assert created["status"] == "awaiting_model_config"
    with pytest.raises(ImageGenerationTaskError, match="route is disabled"):
        await image_service.generate(created["id"])
    assert gateway.requests == []
    assert (await image_service.task(created["id"]))["status"] == "awaiting_model_config"


@pytest.mark.asyncio
async def test_rejecting_image_approval_updates_task(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "reject.sqlite3")
    await repository.initialize()
    image_service = service(repository, FakeImageGateway((PNG_DATA_URI,)), tmp_path)
    created = await image_service.create_task(
        prompt="reject me",
        intended_use=ImageIntendedUse.PRIVATE_REPLY,
        requested_by=OWNER_QQ,
        request_source="dashboard",
    )
    generated = await image_service.generate(created["id"])
    napcat = NapCatClient(
        "http://napcat.test",
        transport=httpx.MockTransport(no_network_transport),
    )
    controls = OwnerControlService(
        repository,
        napcat,
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        image_generation_service=image_service,
    )
    try:
        rejected = await controls.execute(
            control_command(
                OwnerCommandKind.REJECT,
                {"approval_code": generated["approval_code"]},
            )
        )
    finally:
        await napcat.close()

    assert rejected.data["rejected"] is True
    assert (await image_service.task(created["id"]))["status"] == "rejected"
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_owner_commands_create_generate_and_acknowledge_image_report(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "owner-image.sqlite3")
    await repository.initialize()
    gateway = FakeImageGateway((PNG_DATA_URI,))
    image_service = service(repository, gateway, tmp_path)
    napcat = NapCatClient(
        "http://napcat.test",
        transport=httpx.MockTransport(no_network_transport),
    )
    controls = OwnerControlService(
        repository,
        napcat,
        owner_qq=OWNER_QQ,
        bot_qq=BOT_QQ,
        image_generation_service=image_service,
    )
    try:
        created = await controls.execute(
            control_command(
                OwnerCommandKind.IMAGE_CREATE,
                {"prompt": "owner-controlled YCH image"},
            )
        )
        generated = await controls.execute(
            control_command(
                OwnerCommandKind.IMAGE_GENERATE,
                {"task_id": created.data["id"]},
            )
        )
        report = (await repository.owner_reports())[0]
        acknowledged = await controls.execute(
            control_command(
                OwnerCommandKind.REPORT_ACK,
                {"report_id": report["id"]},
            )
        )
    finally:
        await napcat.close()

    assert created.status == "completed"
    assert generated.status == "pending_approval"
    assert generated.data["status"] == "pending_approval"
    assert acknowledged.data["acknowledged"] is True
    assert (await repository.owner_reports())[0]["status"] == "acknowledged"
    assert len(gateway.requests) == 1
    assert (await repository.counts())["outbox"] == 0


@pytest.mark.asyncio
async def test_image_review_stays_off_unless_fake_engine_allows_or_blocks(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "image-review.sqlite3")
    await repository.initialize()
    skipped = FakeImageReviewEngine("block")
    allowed = FakeImageReviewEngine("allow")
    blocked = FakeImageReviewEngine("block")

    off_service = service(
        repository,
        FakeImageGateway((PNG_DATA_URI,)),
        tmp_path,
        review_engine=skipped,
    )
    off_task = await off_service.create_task(
        prompt="no review by default",
        intended_use=ImageIntendedUse.GENERAL,
        requested_by=OWNER_QQ,
        request_source="dashboard",
    )
    off_generated = await off_service.generate(off_task["id"])
    assert off_generated["status"] == "pending_approval"
    assert off_generated["reviews"] == []
    assert skipped.calls == []

    allow_service = service(
        repository,
        FakeImageGateway((PNG_DATA_URI,)),
        tmp_path,
        review_enabled=True,
        review_engine=allowed,
    )
    allow_task = await allow_service.create_task(
        prompt="review allow",
        intended_use=ImageIntendedUse.GENERAL,
        requested_by=OWNER_QQ,
        request_source="dashboard",
    )
    allow_generated = await allow_service.generate(allow_task["id"])
    assert allow_generated["status"] == "pending_approval"
    assert allow_generated["reviews"][0]["verdict"] == "allow"
    assert allowed.calls

    block_service = service(
        repository,
        FakeImageGateway((PNG_DATA_URI,)),
        tmp_path,
        review_enabled=True,
        review_engine=blocked,
    )
    block_task = await block_service.create_task(
        prompt="review block",
        intended_use=ImageIntendedUse.GENERAL,
        requested_by=OWNER_QQ,
        request_source="dashboard",
    )
    block_generated = await block_service.generate(block_task["id"])
    assert block_generated["status"] == "review_blocked"
    assert block_generated["approval_code"] is None
    assert block_generated["reviews"][0]["verdict"] == "block"
    reports = await repository.owner_reports()
    assert any(
        item["severity"] == "info" and item["related_id"] == block_task["id"] for item in reports
    )
    pending = await repository.pending_approvals()
    assert pending != []
    assert all(item["subject_id"] != block_task["id"] for item in pending)


@pytest.mark.asyncio
async def test_expired_image_approval_can_be_renewed(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "image-renew.sqlite3")
    await repository.initialize()
    image_service = service(repository, FakeImageGateway((PNG_DATA_URI,)), tmp_path)
    created = await image_service.create_task(
        prompt="renew after expiry",
        intended_use=ImageIntendedUse.GENERAL,
        requested_by=OWNER_QQ,
        request_source="dashboard",
    )
    generated = await image_service.generate(created["id"])
    old_code = generated["approval_code"]
    await repository.decide_image_task(
        task_id=created["id"],
        status=ImageTaskStatus.APPROVAL_EXPIRED,
        actor_qq=None,
    )

    renewed = await image_service.renew_approval(created["id"])
    assert renewed["status"] == "pending_approval"
    assert renewed["approval_code"] != old_code
    pending = await repository.pending_approvals()
    assert [item["approval_code"] for item in pending] == [renewed["approval_code"]]

    with pytest.raises(ImageGenerationTaskError, match="not expired"):
        await image_service.renew_approval(created["id"])


@pytest.mark.asyncio
async def test_orphan_scan_reports_untracked_and_missing_files(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "image-orphan.sqlite3")
    await repository.initialize()
    image_service = service(
        repository,
        FakeImageGateway((PNG_DATA_URI,)),
        tmp_path,
        orphan_scan_enabled=True,
    )
    created = await image_service.create_task(
        prompt="scan orphans",
        intended_use=ImageIntendedUse.GENERAL,
        requested_by=OWNER_QQ,
        request_source="dashboard",
    )
    generated = await image_service.generate(created["id"])
    artifact = await repository.image_artifact(generated["artifacts"][0]["id"])
    assert artifact is not None
    stored = tmp_path / artifact["storage_path"]
    stored.unlink()
    orphan = tmp_path / "storage" / "generated" / "images" / "loose.png"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")

    disabled = service(repository, FakeImageGateway((PNG_DATA_URI,)), tmp_path)
    with pytest.raises(ImageGenerationTaskError, match="orphan scan is disabled"):
        await disabled.scan_orphans(created_by=OWNER_QQ)

    scan = await image_service.scan_orphans(created_by=OWNER_QQ)
    assert scan["orphan_count"] == 1
    assert scan["missing_count"] == 1
    assert any(item.endswith("loose.png") for item in scan["orphans"])
    assert artifact["storage_path"] in scan["missing"]
    reports = await repository.owner_reports()
    assert any(item["category"] == "image_orphan_scan" for item in reports)
