"""FastAPI boundary for health checks and the OneBot reverse WebSocket."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ych_bot import __version__
from ych_bot.api.qualification import register_qualification_routes
from ych_bot.application import (
    AccountError,
    AccountService,
    ActivationScopeProbe,
    AdminSessionService,
    ArtifactInventoryService,
    CapabilityGateProbe,
    ChatQuotaService,
    ConversationContextService,
    DailySummaryError,
    DailySummaryService,
    DatabasePreflightService,
    DiaryError,
    DiaryService,
    DisabledWebSearchClient,
    DurablePauseProbe,
    FakeFirstReplyWorker,
    ImageGenerationService,
    ImageGenerationTaskError,
    ImportedSourceAdapter,
    KnowledgeImportError,
    KnowledgeImportService,
    KnowledgeProcessingError,
    KnowledgeProcessingService,
    LauncherEvidenceProbe,
    LauncherPreflightService,
    ManagedRootRegistry,
    MaterialError,
    MaterialService,
    MemoryService,
    MemoryValidationError,
    MessageIngestionService,
    MigrationBackupAdapter,
    NapCatHistoryReader,
    OfflineIsolationProbe,
    OneBotIdentityProbe,
    OperationalApiError,
    OperationalApiService,
    OpsDashboardService,
    OpsFlags,
    OwnerAuthorizationProbe,
    OwnerControlService,
    OwnerReportError,
    OwnerReportService,
    PersonaService,
    PersonaValidationError,
    PrivacyArtifactAdapter,
    PrivacyJobError,
    PrivacyJobService,
    ProactiveContentComposer,
    ProactiveMessageError,
    ProactiveMessageService,
    ProductionReplyRuntimeService,
    ProvenanceContextAssembler,
    QuarantineInventoryAdapter,
    QzoneProfileService,
    QzoneTaskError,
    QzoneTaskService,
    ReadinessGuard,
    ReadinessService,
    ReplyOperationsService,
    ReplyOrchestrationService,
    ReplyOutboxService,
    ReplyPipelineFlags,
    ReplyPlanService,
    ReplyRuntimeControlError,
    ReplyRuntimeControlService,
    ReplyRuntimeGates,
    ReplyStyleError,
    ReplyStyleService,
    RetentionError,
    RetentionService,
    RuntimeDatabaseProbe,
    ShadowInferenceError,
    ShadowReplyService,
    SharedLauncherEvidence,
    StatsService,
    StickerLibrary,
    WebSearchClient,
    WorkerCeilingProbe,
    WorkerLifecycleProbe,
    retention_error_as_operational,
    safe_configuration_fingerprint,
)
from ych_bot.application.inbound_images import inline_http_images
from ych_bot.application.qualification import (
    QualificationApiService,
    QualificationPlanningService,
)
from ych_bot.config import Settings
from ych_bot.domain.control import (
    ControlSource,
    OwnerCommand,
    OwnerCommandKind,
    QzonePostStatus,
    QzoneVisibility,
)
from ych_bot.domain.identity import PrivacyDataClass
from ych_bot.domain.images import ImageIntendedUse
from ych_bot.domain.managed_artifacts import (
    ArtifactOwnerScope,
    ArtifactReferenceState,
    ArtifactRetentionState,
    ArtifactVerificationState,
    ManagedArtifactType,
    QuarantineBatchState,
)
from ych_bot.domain.memory import MemoryConflictResolution, MemoryKind
from ych_bot.domain.modeling import DisabledChatModelGateway, DisabledImageModelGateway
from ych_bot.domain.models import ConversationKind
from ych_bot.domain.persona import DocumentPurpose, ProfileScope
from ych_bot.domain.proactive import (
    MissedTaskPolicy,
    ProactiveTaskStatus,
    QuietHoursBehavior,
)
from ych_bot.domain.qzone import QzoneSchedulePolicy
from ych_bot.domain.readiness import (
    CapabilityScope,
    LauncherPreflightResult,
    ReadinessProfile,
)
from ych_bot.domain.reply_pipeline import ReplyActivationMode
from ych_bot.domain.reports import ReportDeliveryPolicy
from ych_bot.domain.system_identity import CORE_IDENTITY
from ych_bot.infrastructure.database import SQLiteRepository
from ych_bot.infrastructure.models import (
    ModelCallGuard,
    ModelProtectionPolicy,
    OpenAICompatibleChatGateway,
    OpenAICompatibleImageGateway,
    ProtectedChatModelGateway,
    ProtectedImageModelGateway,
)
from ych_bot.infrastructure.napcat.client import LazyNapCatClient
from ych_bot.qualification.catalog import load_bundled_qualification_catalog
from ych_bot.qualification.prices import load_bundled_qualification_price_catalog
from ych_bot.workers import (
    ImageOrphanWorker,
    KnowledgeProcessingWorker,
    OutboxDispatcher,
    OwnerReportWorker,
    ProactiveMessageWorker,
    QzonePublishWorker,
    ReplyRuntimeWorker,
)


@dataclass(slots=True)
class OneBotConnectionState:
    connected: bool = False
    connected_at: datetime | None = None
    last_event_at: datetime | None = None
    stored_events: int = 0
    duplicate_events: int = 0
    ignored_events: int = 0
    authenticated_bot_qq: str | None = None
    connection_revision: int = 0

    def connected_now(self) -> None:
        self.connected = True
        self.connected_at = datetime.now(UTC)
        self.authenticated_bot_qq = None
        self.connection_revision += 1

    def disconnected_now(self) -> None:
        self.connected = False
        self.authenticated_bot_qq = None
        self.connection_revision += 1

    def observe(self, status: str, *, bot_qq: str | None = None) -> None:
        self.last_event_at = datetime.now(UTC)
        if status in {"stored", "duplicate", "observed"} and bot_qq and bot_qq.isdigit():
            self.authenticated_bot_qq = bot_qq
        if status == "stored":
            self.stored_events += 1
        elif status == "duplicate":
            self.duplicate_events += 1
        elif status == "observed":
            return
        else:
            self.ignored_events += 1


class DashboardCommandRequest(BaseModel):
    action: OwnerCommandKind
    arguments: dict[str, str] = Field(default_factory=dict)


class BackupCleanupRequest(BaseModel):
    cleanup_token: str = Field(min_length=64, max_length=64)


class ReplyRuntimePreviewRequest(BaseModel):
    action: Literal[
        "set_mode",
        "set_eligibility",
        "decide_approval",
        "cancel_approval",
        "emergency_stop",
        "resume",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


class ReplyRuntimeConfirmRequest(BaseModel):
    confirmation_token: str = Field(min_length=36, max_length=64)


class QzoneProfilePreviewRequest(BaseModel):
    purpose: str = "initialize_user_profile"


class ManualPersonaRequest(BaseModel):
    scope: ProfileScope
    user_qq: str | None = None
    name: str
    definition: str


class ManualMemoryRequest(BaseModel):
    kind: MemoryKind
    key: str
    value: dict[str, Any]
    expires_at: datetime | None = None


class ResolveMemoryConflictRequest(BaseModel):
    resolution: MemoryConflictResolution


class ImageTaskCreateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    intended_use: ImageIntendedUse = ImageIntendedUse.GENERAL


class ShadowReplayRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=128)


class ProactiveTaskCreateRequest(BaseModel):
    target_qq: str = Field(min_length=5, max_length=20)
    content: str = Field(min_length=1, max_length=2000)
    scheduled_for: datetime
    timezone: str | None = None
    missed_policy: MissedTaskPolicy = MissedTaskPolicy.SKIP
    missed_grace_seconds: int = Field(default=900, ge=0, le=86_400)


class ReplyStyleRequest(BaseModel):
    min_bubbles: int = Field(default=1, ge=1, le=10)
    max_bubbles: int = Field(default=1, ge=1, le=10)
    sentence_min_chars: int = Field(default=12, ge=4, le=120)
    sentence_max_chars: int = Field(default=80, ge=8, le=200)


class ProactivePolicyRequest(BaseModel):
    enabled: bool
    timezone: str
    quiet_hours_enabled: bool = True
    quiet_start: str = "22:00"
    quiet_end: str = "08:00"
    quiet_behavior: QuietHoursBehavior = QuietHoursBehavior.DELAY
    daily_limit: int = Field(default=3, ge=1, le=100)
    minimum_interval_seconds: int = Field(default=3600, ge=0, le=86_400)
    auto_content_enabled: bool = False
    send_diary: bool = False


class QzoneDraftCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    visibility: QzoneVisibility = QzoneVisibility.FRIENDS
    target_uins: list[str] = Field(default_factory=list, max_length=50)


class QzonePublishCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    scheduled_for: datetime | None = None
    timezone: str | None = None
    visibility: QzoneVisibility = QzoneVisibility.FRIENDS
    target_uins: list[str] = Field(default_factory=list, max_length=50)
    missed_policy: MissedTaskPolicy = MissedTaskPolicy.REQUIRE_REAPPROVAL
    missed_grace_seconds: int = Field(default=900, ge=0, le=86_400)


class OwnerReportPolicyRequest(BaseModel):
    timezone: str | None = None
    digest_local_time: str | None = None
    dedupe_window_seconds: int | None = Field(default=None, ge=60, le=86_400)
    action_required_policy: ReportDeliveryPolicy | None = None
    critical_policy: ReportDeliveryPolicy | None = None
    warning_policy: ReportDeliveryPolicy | None = None
    info_policy: ReportDeliveryPolicy | None = None


class OwnerUpdateRequest(BaseModel):
    owner_qq: str = Field(min_length=5, max_length=20)


class DiarySaveRequest(BaseModel):
    day_key: str
    content: str = ""


class MaterialCreateRequest(BaseModel):
    title: str
    content: str
    uploader_claim: str
    target_qq: str = ""


class MaterialEnableRequest(BaseModel):
    enabled: bool


class BotCreateRequest(BaseModel):
    qq: str = Field(min_length=5, max_length=20)
    label: str = ""


class BotUpdateRequest(BaseModel):
    label: str | None = None
    quota_user_default: int | None = Field(default=None, ge=1)
    quota_group_default: int | None = Field(default=None, ge=1)
    quota_user_reply: str | None = Field(default=None, min_length=1, max_length=500)


class QuotaLimitRequest(BaseModel):
    daily_limit: int = Field(ge=1)
    display_name: str | None = Field(default=None, max_length=64)


class QuotaBonusRequest(BaseModel):
    amount: int = Field(ge=1)


class QzonePolicyRequest(BaseModel):
    timezone: str
    quiet_hours_enabled: bool = True
    quiet_start: str = "22:00"
    quiet_end: str = "08:00"
    quiet_behavior: QuietHoursBehavior = QuietHoursBehavior.DELAY
    daily_limit: int = Field(default=1, ge=1, le=20)
    minimum_interval_seconds: int = Field(default=21_600, ge=0, le=604_800)


class ReadinessRefreshRequest(BaseModel):
    bot_qq: str = Field(min_length=5, max_length=20)
    process_instance_id: str = Field(min_length=1, max_length=128)
    profile: ReadinessProfile
    capability_scope: CapabilityScope


class ArtifactRefreshRequest(BaseModel):
    bot_qq: str = Field(min_length=5, max_length=20)
    process_instance_id: str = Field(min_length=1, max_length=128)
    owner_scope: ArtifactOwnerScope | None = None
    owner_qq: str | None = Field(default=None, min_length=5, max_length=20)


class RetentionPreviewRequest(BaseModel):
    bot_qq: str = Field(min_length=5, max_length=20)
    process_instance_id: str = Field(min_length=1, max_length=128)
    target_batch_type: Literal[
        "migration_backups",
        "privacy_exports",
        "privacy_deletion_backups",
        "imported_sources",
    ]
    owner_scope: ArtifactOwnerScope
    owner_qq: str | None = Field(default=None, min_length=5, max_length=20)


class RetentionConfirmRequest(BaseModel):
    bot_qq: str = Field(min_length=5, max_length=20)
    process_instance_id: str = Field(min_length=1, max_length=128)
    confirmation_token: str = Field(min_length=32, max_length=128)
    expected_revision: int = Field(ge=1)


def _bearer_token(authorization: str) -> str:
    return authorization[7:] if authorization.lower().startswith("bearer ") else ""


def _operational_http_exception(exc: OperationalApiError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _authorized(websocket: WebSocket, expected_token: str) -> bool:
    if not expected_token:
        return False
    authorization = websocket.headers.get("authorization", "")
    bearer = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    query_token = websocket.query_params.get("access_token", "")
    presented = bearer or query_token
    if not presented:
        return False
    return hmac.compare_digest(presented, expected_token)


def create_app(
    settings: Settings | None = None,
    *,
    process_instance_id: str | None = None,
    launcher_result: LauncherPreflightResult | None = None,
    clock: Callable[[], datetime] | None = None,
    napcat_factory: Callable[[str, str], Any] | None = None,
    readiness_probe_overrides: Mapping[str, Any] | None = None,
) -> FastAPI:
    active_settings = settings or Settings.load()
    active_process_instance_id = process_instance_id or str(uuid4())
    repository = SQLiteRepository(active_settings.database_path)
    managed_roots = ManagedRootRegistry(active_settings.project_root)
    artifact_inventory = ArtifactInventoryService(
        repository,
        adapters=(
            MigrationBackupAdapter(managed_roots),
            PrivacyArtifactAdapter(managed_roots, repository),
            ImportedSourceAdapter(managed_roots, repository),
            QuarantineInventoryAdapter(managed_roots, repository),
        ),
    )
    database_preflight = DatabasePreflightService(
        database_path=active_settings.database_path,
        project_root=active_settings.project_root,
        backup_enabled=active_settings.migration_backup_enabled,
    )
    retention = RetentionService(
        repository,
        artifact_inventory,
        managed_roots,
        process_instance_id=active_process_instance_id,
        clock=clock,
        database_preflight=database_preflight,
    )
    readiness_guard = ReadinessGuard(
        repository,
        bot_qq=active_settings.bot_qq,
        process_instance_id=active_process_instance_id,
    )
    napcat_client = LazyNapCatClient(
        active_settings.napcat_http_url,
        active_settings.onebot_access_token,
        factory=napcat_factory,
    )
    connection_state = OneBotConnectionState()
    admin_sessions = AdminSessionService(
        repository,
        bootstrap_token=active_settings.admin_access_token,
        lifetime_hours=active_settings.admin_session_hours,
    )
    privacy_jobs = PrivacyJobService(
        repository,
        project_root=active_settings.project_root,
        enabled=active_settings.privacy_jobs_enabled,
    )
    persona_service = PersonaService(repository)
    memory_service = MemoryService(repository)
    sticker_library = StickerLibrary(active_settings.project_root / "storage" / "stickers")
    reply_style = ReplyStyleService(repository)
    conversation_context = ConversationContextService(persona_service, memory_service)
    chat_model_configured = (
        active_settings.chat_api_protocol == "openai_compatible"
        and bool(active_settings.chat_api_base)
        and bool(active_settings.chat_model)
    )
    vision_model_configured = (
        active_settings.vision_api_protocol == "openai_compatible"
        and bool(active_settings.vision_api_base)
        and bool(active_settings.vision_model)
    )
    image_model_configured = (
        active_settings.image_api_protocol == "openai_compatible"
        and bool(active_settings.image_api_base)
        and bool(active_settings.image_model)
    )
    stats_model_configured = (
        active_settings.stats_api_protocol == "openai_compatible"
        and bool(active_settings.stats_api_base)
        and bool(active_settings.stats_model)
    )
    chat_model_guard = ModelCallGuard(
        repository,
        ModelProtectionPolicy(
            route="chat",
            timezone=active_settings.timezone,
            daily_request_limit=active_settings.chat_daily_request_limit,
            daily_token_limit=active_settings.chat_daily_token_limit,
            failure_threshold=active_settings.chat_failure_threshold,
            cooldown_seconds=active_settings.chat_circuit_cooldown_seconds,
            lease_seconds=max(
                60,
                int(
                    active_settings.chat_timeout_seconds * (active_settings.chat_max_retries + 1)
                    + 120
                ),
            ),
        ),
    )
    vision_model_guard = ModelCallGuard(
        repository,
        ModelProtectionPolicy(
            route="vision",
            timezone=active_settings.timezone,
            daily_request_limit=active_settings.vision_daily_request_limit,
            daily_token_limit=active_settings.vision_daily_token_limit,
            failure_threshold=active_settings.vision_failure_threshold,
            cooldown_seconds=active_settings.vision_circuit_cooldown_seconds,
            lease_seconds=max(
                60,
                int(
                    active_settings.vision_timeout_seconds
                    * (active_settings.vision_max_retries + 1)
                    + 120
                ),
            ),
        ),
    )
    image_model_guard = ModelCallGuard(
        repository,
        ModelProtectionPolicy(
            route="image",
            timezone=active_settings.timezone,
            daily_request_limit=active_settings.image_daily_request_limit,
            daily_token_limit=None,
            failure_threshold=active_settings.image_failure_threshold,
            cooldown_seconds=active_settings.image_circuit_cooldown_seconds,
            lease_seconds=max(
                60,
                int(
                    active_settings.image_timeout_seconds * (active_settings.image_max_retries + 1)
                    + 120
                ),
            ),
        ),
    )
    stats_model_guard = ModelCallGuard(
        repository,
        ModelProtectionPolicy(
            route="stats",
            timezone=active_settings.timezone,
            daily_request_limit=active_settings.stats_daily_request_limit,
            daily_token_limit=active_settings.stats_daily_token_limit,
            failure_threshold=active_settings.stats_failure_threshold,
            cooldown_seconds=active_settings.stats_circuit_cooldown_seconds,
            lease_seconds=max(
                60,
                int(
                    active_settings.stats_timeout_seconds * (active_settings.stats_max_retries + 1)
                    + 120
                ),
            ),
        ),
    )
    chat_gateway = DisabledChatModelGateway()
    chat_gateway_active = (
        active_settings.model_network_enabled
        and active_settings.chat_model_enabled
        and chat_model_configured
    )
    if chat_gateway_active:
        chat_gateway = ProtectedChatModelGateway(
            OpenAICompatibleChatGateway(
                base_url=active_settings.chat_api_base,
                api_key=active_settings.chat_api_key,
                model=active_settings.chat_model,
                timeout_seconds=active_settings.chat_timeout_seconds,
                max_retries=active_settings.chat_max_retries,
            ),
            chat_model_guard,
            readiness_guard,
            capability_scope=CapabilityScope.CHAT_MODEL,
        )
    vision_gateway: DisabledChatModelGateway | ProtectedChatModelGateway = (
        DisabledChatModelGateway()
    )
    vision_gateway_active = (
        active_settings.model_network_enabled
        and active_settings.vision_model_enabled
        and vision_model_configured
    )
    if vision_gateway_active:
        vision_gateway = ProtectedChatModelGateway(
            OpenAICompatibleChatGateway(
                base_url=active_settings.vision_api_base,
                api_key=active_settings.vision_api_key,
                model=active_settings.vision_model,
                timeout_seconds=active_settings.vision_timeout_seconds,
                max_retries=active_settings.vision_max_retries,
            ),
            vision_model_guard,
            readiness_guard,
            capability_scope=CapabilityScope.VISION_MODEL,
        )
    stats_gateway: DisabledChatModelGateway | ProtectedChatModelGateway = DisabledChatModelGateway()
    stats_gateway_active = (
        active_settings.model_network_enabled
        and active_settings.stats_model_enabled
        and stats_model_configured
    )
    if stats_gateway_active:
        stats_gateway = ProtectedChatModelGateway(
            OpenAICompatibleChatGateway(
                base_url=active_settings.stats_api_base,
                api_key=active_settings.stats_api_key,
                model=active_settings.stats_model,
                timeout_seconds=active_settings.stats_timeout_seconds,
                max_retries=active_settings.stats_max_retries,
            ),
            stats_model_guard,
            readiness_guard,
            capability_scope=CapabilityScope.STATS_MODEL,
        )
    knowledge_processing_execution_enabled = (
        active_settings.knowledge_processing_enabled and chat_gateway_active
    )
    knowledge_import = KnowledgeImportService(
        repository,
        project_root=active_settings.project_root,
        import_enabled=active_settings.document_import_enabled,
        processing_enabled=knowledge_processing_execution_enabled,
        max_megabytes=active_settings.document_max_megabytes,
        max_pages=active_settings.document_max_pages,
        ocr_enabled=active_settings.document_ocr_enabled,
    )
    knowledge_processing = KnowledgeProcessingService(
        repository,
        chat_gateway,
        owner_qq=active_settings.owner_qq,
        enabled=knowledge_processing_execution_enabled,
        lease_seconds=max(
            300,
            int(
                active_settings.chat_timeout_seconds * (active_settings.chat_max_retries + 1) + 120
            ),
        ),
    )
    knowledge_worker = KnowledgeProcessingWorker(
        repository,
        knowledge_processing,
        configured_enabled=active_settings.knowledge_worker_enabled,
        poll_seconds=active_settings.knowledge_worker_poll_seconds,
        max_attempts=active_settings.knowledge_worker_max_attempts,
        retry_base_seconds=active_settings.knowledge_worker_retry_base_seconds,
    )
    image_gateway = DisabledImageModelGateway()
    image_gateway_active = (
        active_settings.model_network_enabled
        and active_settings.image_model_enabled
        and image_model_configured
    )
    if image_gateway_active:
        image_gateway = ProtectedImageModelGateway(
            OpenAICompatibleImageGateway(
                base_url=active_settings.image_api_base,
                api_key=active_settings.image_api_key,
                model=active_settings.image_model,
                response_format=active_settings.image_response_format,
                timeout_seconds=active_settings.image_timeout_seconds,
                max_retries=active_settings.image_max_retries,
            ),
            image_model_guard,
            readiness_guard,
        )
    image_task_execution_enabled = (
        image_gateway_active and active_settings.image_response_format == "b64_json"
    )
    image_generation = ImageGenerationService(
        repository,
        image_gateway,
        project_root=active_settings.project_root,
        owner_qq=active_settings.owner_qq,
        model_route=active_settings.image_model,
        enabled=image_task_execution_enabled,
        max_artifact_megabytes=active_settings.image_artifact_max_megabytes,
        max_pixels=active_settings.image_artifact_max_pixels,
        max_artifacts=active_settings.image_max_artifacts_per_task,
        review_enabled=active_settings.image_content_review_enabled,
        orphan_scan_enabled=active_settings.image_orphan_scan_enabled,
    )
    image_orphan_worker = ImageOrphanWorker(
        repository,
        image_generation,
        configured_enabled=active_settings.image_orphan_scan_enabled,
        poll_seconds=active_settings.image_orphan_worker_poll_seconds,
        owner_qq=active_settings.owner_qq,
    )
    proactive_messages = ProactiveMessageService(
        repository,
        owner_qq=active_settings.owner_qq,
        bot_qq=active_settings.bot_qq,
        default_timezone=active_settings.timezone,
        default_quiet_start=active_settings.proactive_default_quiet_start,
        default_quiet_end=active_settings.proactive_default_quiet_end,
        default_daily_limit=active_settings.proactive_default_daily_limit,
        default_minimum_interval_seconds=(
            active_settings.proactive_default_minimum_interval_seconds
        ),
        max_future_days=active_settings.proactive_max_future_days,
    )
    stats_dashboard = StatsService(repository, timezone_name=active_settings.timezone)
    web_search: DisabledWebSearchClient | WebSearchClient = DisabledWebSearchClient()
    if active_settings.web_search_enabled:
        web_search = WebSearchClient(
            enabled=True,
            api_base=active_settings.web_search_api_base,
            api_key=active_settings.web_search_api_key,
        )
    diary_service = DiaryService(repository, timezone_name=active_settings.timezone)
    material_service = MaterialService(
        repository,
        chat_gateway if chat_gateway_active else stats_gateway,
        web_search,
        owner_qq=active_settings.owner_qq,
        bot_qq=active_settings.bot_qq,
        model_route=active_settings.chat_model or active_settings.stats_model,
        enabled=chat_gateway_active or stats_gateway_active,
    )
    daily_summaries = DailySummaryService(
        repository,
        stats_dashboard,
        stats_gateway if stats_gateway_active else chat_gateway,
        owner_qq=active_settings.owner_qq,
        bot_qq=active_settings.bot_qq,
        model_route=active_settings.stats_model or active_settings.chat_model,
        enabled=stats_gateway_active or chat_gateway_active,
    )
    content_composer = ProactiveContentComposer(
        repository,
        stats_dashboard,
        chat_gateway,
        conversation_context,
        web_search,
        owner_qq=active_settings.owner_qq,
        bot_qq=active_settings.bot_qq,
        model_route=active_settings.chat_model,
        enabled=chat_gateway_active,
    )
    proactive_worker = ProactiveMessageWorker(
        repository,
        owner_qq=active_settings.owner_qq,
        configured_enabled=active_settings.proactive_scheduler_enabled,
        poll_seconds=active_settings.proactive_scheduler_poll_seconds,
        schedule_tolerance_seconds=active_settings.proactive_schedule_tolerance_seconds,
        policy_recheck_seconds=active_settings.proactive_policy_recheck_seconds,
        content_composer=content_composer,
    )
    qzone_tasks = QzoneTaskService(
        repository,
        owner_qq=active_settings.owner_qq,
        default_timezone=active_settings.timezone,
        default_quiet_start=active_settings.qzone_default_quiet_start,
        default_quiet_end=active_settings.qzone_default_quiet_end,
        default_daily_limit=active_settings.qzone_default_daily_limit,
        default_minimum_interval_seconds=(active_settings.qzone_default_minimum_interval_seconds),
        max_future_days=active_settings.qzone_max_future_days,
    )
    owner_reports = OwnerReportService(
        repository,
        owner_qq=active_settings.owner_qq,
        default_timezone=active_settings.timezone,
        default_digest_local_time=active_settings.owner_report_digest_local_time,
        default_dedupe_window_seconds=active_settings.owner_report_dedupe_window_seconds,
    )
    owner_report_worker = OwnerReportWorker(
        repository,
        owner_qq=active_settings.owner_qq,
        configured_enabled=active_settings.owner_report_worker_enabled,
        reports_enabled=active_settings.owner_reports_enabled,
        poll_seconds=active_settings.owner_report_worker_poll_seconds,
    )
    qzone_profile = QzoneProfileService(
        repository,
        napcat_client,
        owner_qq=active_settings.owner_qq,
        bot_qq=active_settings.bot_qq,
        readiness_guard=readiness_guard,
        collection_enabled=active_settings.qzone_profile_collection_enabled,
        vision_gateway=vision_gateway if vision_gateway_active else None,
        vision_model_route=active_settings.vision_model or "",
        image_inliner=(
            (lambda images: inline_http_images(images, napcat=napcat_client))
            if vision_gateway_active
            else None
        ),
    )
    history_reader = NapCatHistoryReader(
        repository,
        napcat_client,
        readiness_guard=readiness_guard,
    )
    qzone_worker = QzonePublishWorker(
        repository,
        napcat_client,
        owner_qq=active_settings.owner_qq,
        configured_enabled=active_settings.qzone_worker_enabled,
        publish_enabled=active_settings.qzone_publish_enabled,
        readiness_guard=readiness_guard,
        poll_seconds=active_settings.qzone_worker_poll_seconds,
        schedule_tolerance_seconds=active_settings.qzone_schedule_tolerance_seconds,
    )
    outbox_dispatcher = OutboxDispatcher(
        repository,
        napcat_client,
        enabled=active_settings.outbound_enabled,
        readiness_guard=readiness_guard,
        poll_seconds=active_settings.outbound_poll_seconds,
        max_attempts=active_settings.outbound_max_attempts,
        retry_base_seconds=active_settings.outbound_retry_base_seconds,
        default_bot_qq=active_settings.bot_qq,
    )
    reply_orchestration = ReplyOrchestrationService(repository)

    async def reply_runtime_gates() -> ReplyRuntimeGates:
        chat_snapshot = await chat_model_guard.snapshot()
        return ReplyRuntimeGates(
            worker_enabled=active_settings.reply_worker_enabled,
            max_mode=ReplyActivationMode(active_settings.reply_runtime_max_mode),
            chat_model_configured=chat_model_configured,
            chat_route_enabled=active_settings.chat_model_enabled,
            model_network_enabled=active_settings.model_network_enabled,
            chat_circuit_available=chat_snapshot.state != "open",
            outbound_enabled=active_settings.outbound_enabled,
            outbound_worker_active=outbox_dispatcher.active,
            onebot_token_configured=bool(active_settings.onebot_access_token),
            onebot_connected=connection_state.connected,
        )

    reply_runtime_control = ReplyRuntimeControlService(
        repository,
        bot_qq=active_settings.bot_qq,
        owner_qq=active_settings.owner_qq,
        gates_provider=reply_runtime_gates,
        outbox_control=outbox_dispatcher,
    )

    production_reply_runtime_holder: dict[str, ProductionReplyRuntimeService] = {}

    async def reply_before_model(run_id: str, lease_token: str) -> bool:
        return await production_reply_runtime_holder["runtime"].before_model(run_id, lease_token)

    reply_generation_worker = FakeFirstReplyWorker(
        repository,
        reply_orchestration,
        ProvenanceContextAssembler(repository, conversation_context),
        chat_gateway,
        enabled=True,
        model_route=active_settings.chat_model or "unconfigured",
        model_timeout_seconds=active_settings.chat_timeout_seconds,
        before_model=reply_before_model,
        vision_gateway=vision_gateway if vision_gateway_active else None,
        vision_model_route=active_settings.vision_model or "unconfigured",
        vision_timeout_seconds=min(active_settings.vision_timeout_seconds, 120),
        image_inliner=(
            (lambda images: inline_http_images(images, napcat=napcat_client))
            if vision_gateway_active
            else None
        ),
    )
    production_reply_runtime = ProductionReplyRuntimeService(
        repository,
        bot_qq=active_settings.bot_qq,
        owner_qq=active_settings.owner_qq,
        control=reply_runtime_control,
        generation_worker=reply_generation_worker,
        planner=ReplyPlanService(repository),
        outbox=ReplyOutboxService(repository),
        approval_ttl_seconds=active_settings.reply_approval_ttl_seconds,
    )
    production_reply_runtime_holder["runtime"] = production_reply_runtime
    reply_runtime_worker = ReplyRuntimeWorker(
        repository,
        production_reply_runtime,
        bot_qq=active_settings.bot_qq,
        configured_enabled=active_settings.reply_worker_enabled,
        poll_seconds=active_settings.reply_worker_poll_seconds,
    )
    accounts = AccountService(repository)
    quotas = ChatQuotaService(repository, timezone=active_settings.timezone)
    shadow_reply = ShadowReplyService(
        repository,
        conversation_context,
        chat_gateway,
        enabled=active_settings.shadow_inference_enabled
        and (chat_gateway_active or vision_gateway_active),
        model_route=active_settings.chat_model or "unconfigured",
        quota_service=quotas,
        vision_gateway=vision_gateway if vision_gateway_active else None,
        vision_model_route=active_settings.vision_model or "unconfigured",
    )
    qualification_fixture_catalog = load_bundled_qualification_catalog()
    qualification_price_catalog = load_bundled_qualification_price_catalog()
    qualification_planning = QualificationPlanningService(
        repository,
        process_instance_id=active_process_instance_id,
        fixture_catalog=qualification_fixture_catalog,
        price_catalog=qualification_price_catalog,
        clock=clock,
    )
    qualification_api = QualificationApiService(
        repository,
        planning=qualification_planning,
        settings=active_settings,
        fixture_catalog=qualification_fixture_catalog,
        price_catalog=qualification_price_catalog,
        clock=clock,
    )
    control_service = OwnerControlService(
        repository,
        napcat_client,
        owner_qq=active_settings.owner_qq,
        bot_qq=active_settings.bot_qq,
        image_generation_service=image_generation,
        image_orphan_worker_control=image_orphan_worker,
        knowledge_processing_service=knowledge_processing,
        knowledge_worker_control=knowledge_worker,
        proactive_message_service=proactive_messages,
        proactive_worker_control=proactive_worker,
        outbox_worker_control=outbox_dispatcher,
        qzone_task_service=qzone_tasks,
        qzone_worker_control=qzone_worker,
        qzone_profile_service=qzone_profile,
        history_reader=history_reader,
        owner_report_service=owner_reports,
        owner_report_worker_control=owner_report_worker,
        shadow_reply_service=shadow_reply,
        quota_service=quotas,
        qualification_status_service=qualification_api,
    )
    ingestion = MessageIngestionService(
        repository,
        bot_qq=active_settings.bot_qq,
        enabled=active_settings.ingest_enabled,
        owner_qq=active_settings.owner_qq,
        control_service=control_service,
        shadow_reply_service=None,
        reply_orchestration_service=reply_orchestration,
        reply_runtime_control_service=reply_runtime_control,
    )

    async def readiness_capability_state(scope: CapabilityScope) -> dict[str, Any]:
        if scope is CapabilityScope.CHAT_MODEL:
            snapshot = (await chat_model_guard.snapshot()).as_dict()
            return {
                "configured": chat_model_configured,
                "active": chat_gateway_active,
                "circuit_available": snapshot.get("state") != "open",
                "revision": f"chat:{snapshot.get('state', 'unknown')}",
            }
        if scope is CapabilityScope.VISION_MODEL:
            snapshot = (await vision_model_guard.snapshot()).as_dict()
            return {
                "configured": vision_model_configured,
                "active": vision_gateway_active,
                "circuit_available": snapshot.get("state") != "open",
                "revision": f"vision:{snapshot.get('state', 'unknown')}",
            }
        if scope is CapabilityScope.IMAGE_MODEL:
            snapshot = (await image_model_guard.snapshot()).as_dict()
            return {
                "configured": image_model_configured,
                "active": image_gateway_active,
                "circuit_available": snapshot.get("state") != "open",
                "revision": f"image:{snapshot.get('state', 'unknown')}",
            }
        if scope is CapabilityScope.STATS_MODEL:
            snapshot = (await stats_model_guard.snapshot()).as_dict()
            return {
                "configured": stats_model_configured,
                "active": stats_gateway_active,
                "circuit_available": snapshot.get("state") != "open",
                "revision": f"stats:{snapshot.get('state', 'unknown')}",
            }
        active_by_scope = {
            CapabilityScope.QQ_REPLY: active_settings.outbound_enabled,
            CapabilityScope.QQ_PROACTIVE: (
                active_settings.outbound_enabled and active_settings.proactive_scheduler_enabled
            ),
            CapabilityScope.OWNER_REPORT: (
                active_settings.outbound_enabled and active_settings.owner_reports_enabled
            ),
            CapabilityScope.QZONE_PUBLISH: active_settings.qzone_publish_enabled,
            CapabilityScope.QZONE_PROFILE: active_settings.qzone_profile_collection_enabled,
            CapabilityScope.LIVE_HISTORY: active_settings.live_history_enabled,
        }
        active = bool(active_by_scope.get(scope, False))
        configured = bool(active_settings.onebot_access_token)
        return {
            "configured": configured,
            "active": active,
            "circuit_available": True,
            "revision": f"{scope.value}:{int(configured)}:{int(active)}",
        }

    async def readiness_worker_state(scope: CapabilityScope) -> dict[str, Any]:
        if scope in {
            CapabilityScope.CHAT_MODEL,
            CapabilityScope.VISION_MODEL,
            CapabilityScope.IMAGE_MODEL,
            CapabilityScope.STATS_MODEL,
            CapabilityScope.QZONE_PROFILE,
            CapabilityScope.LIVE_HISTORY,
        }:
            return {"configured": True, "active": True, "revision": "direct-v1"}
        if scope is CapabilityScope.QQ_REPLY:
            snapshot = outbox_dispatcher.snapshot()
        elif scope is CapabilityScope.QQ_PROACTIVE:
            snapshot = proactive_worker.snapshot()
        elif scope is CapabilityScope.OWNER_REPORT:
            snapshot = owner_report_worker.snapshot()
        else:
            snapshot = qzone_worker.snapshot()
        active = bool(snapshot.get("active")) and bool(snapshot.get("loop_running"))
        return {
            "configured": bool(snapshot.get("configured_enabled", snapshot.get("enabled", False))),
            "active": active,
            "revision": (f"{scope.value}:{int(active)}:{int(bool(snapshot.get('paused')))}"),
        }

    async def readiness_pause_state(scope: CapabilityScope) -> dict[str, Any]:
        worker_name = {
            CapabilityScope.QQ_REPLY: "outbox",
            CapabilityScope.QQ_PROACTIVE: "proactive",
            CapabilityScope.OWNER_REPORT: "owner_reports",
            CapabilityScope.QZONE_PUBLISH: "qzone",
        }.get(scope)
        if worker_name is None:
            return {"paused": False, "revision": f"{scope.value}:not-applicable"}
        paused = await asyncio.to_thread(repository.worker_paused_sync, worker_name)
        return {"paused": paused, "revision": f"{worker_name}:{int(paused)}"}

    async def readiness_activation_state(scope: CapabilityScope) -> dict[str, Any]:
        if scope is CapabilityScope.QQ_REPLY:
            status = await reply_runtime_control.status()
            state = status["state"]
            allowed = ReplyActivationMode(status["effective_mode"]).allows_delivery
            return {
                "allowed": allowed,
                "emergency_paused": bool(state["emergency_paused"]),
                "revision": state["revision"],
            }
        capability = await readiness_capability_state(scope)
        return {
            "allowed": bool(capability.get("active")),
            "emergency_paused": False,
            "revision": capability.get("revision", "unknown"),
        }

    async def readiness_owner_state() -> dict[str, Any]:
        current_owner = await repository.current_owner_qq()
        return {
            "authorized": current_owner == active_settings.owner_qq,
            "revision": f"owner:{current_owner or 'absent'}",
        }

    launcher_probe_codes = (
        "launcher.configuration",
        "launcher.database",
        "launcher.managed_paths",
        "launcher.admin_auth",
        "launcher.port",
    )
    launcher_evidence = SharedLauncherEvidence(result=launcher_result)
    if launcher_result is not None:

        async def refresh_launcher_evidence() -> LauncherPreflightResult:
            return await LauncherPreflightService(active_settings, clock=clock).inspect(
                process_instance_id=active_process_instance_id,
                accept_current_listener=True,
            )

        launcher_evidence.refresher = refresh_launcher_evidence
    readiness_probes: dict[str, Any] = {
        code: LauncherEvidenceProbe(
            code,
            evidence=launcher_evidence,
            expected_process_instance_id=active_process_instance_id,
            expected_configuration_fingerprint=safe_configuration_fingerprint(active_settings),
        )
        for code in launcher_probe_codes
    }
    readiness_probes.update(
        {
            "runtime.database": RuntimeDatabaseProbe(repository),
            "runtime.worker_ceilings": WorkerCeilingProbe(
                configured_modes={
                    "reply": active_settings.reply_runtime_max_mode,
                    "outbound": active_settings.outbound_enabled,
                    "proactive": active_settings.proactive_scheduler_enabled,
                    "qzone": active_settings.qzone_publish_enabled,
                    "owner_reports": active_settings.owner_reports_enabled,
                }
            ),
            "offline.isolation": OfflineIsolationProbe(
                state=lambda: {
                    "network_disabled": not active_settings.model_network_enabled
                    and not active_settings.outbound_enabled
                    and not active_settings.qzone_publish_enabled
                    and not active_settings.qzone_profile_collection_enabled,
                    "outbox_non_delivering": not active_settings.outbound_enabled,
                    "context_isolation": True,
                }
            ),
            "runtime.durable_pause": DurablePauseProbe(state=readiness_pause_state),
            "onebot.identity": OneBotIdentityProbe(
                state=lambda: {
                    "connected": connection_state.connected,
                    "token_configured": bool(active_settings.onebot_access_token),
                    "authenticated_bot_qq": connection_state.authenticated_bot_qq,
                    "connection_revision": connection_state.connection_revision,
                }
            ),
            "capability.gate": CapabilityGateProbe(state=readiness_capability_state),
            "runtime.worker_lifecycle": WorkerLifecycleProbe(state=readiness_worker_state),
            "runtime.activation_scope": ActivationScopeProbe(state=readiness_activation_state),
            "runtime.owner_authorization": OwnerAuthorizationProbe(state=readiness_owner_state),
        }
    )
    if readiness_probe_overrides:
        readiness_probes.update(readiness_probe_overrides)
    readiness_service = ReadinessService(
        repository,
        bot_qq=active_settings.bot_qq,
        process_instance_id=active_process_instance_id,
        probes=readiness_probes,
        clock=clock,
    )
    readiness_guard.bind(readiness_service)
    operational_api = OperationalApiService(
        repository,
        admin_sessions,
        readiness_service,
        retention,
        bot_qq=active_settings.bot_qq,
        process_instance_id=active_process_instance_id,
        configured_owner_qq=active_settings.owner_qq,
        clock=clock,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await database_preflight.ensure_backup_before_migration()
        await repository.initialize()
        await accounts.ensure_seeded(
            owner_qq=active_settings.owner_qq,
            bot_qq=active_settings.bot_qq,
        )
        await repository.ensure_reply_runtime_state(active_settings.bot_qq)
        await retention.reconcile_interrupted_batches()
        await retention.refresh_classification()
        await readiness_service.evaluate(
            ReadinessProfile.LOCAL_START,
            capability_scope=CapabilityScope.LOCAL_RUNTIME,
        )
        worker_tasks: list[asyncio.Task[None]] = []
        if knowledge_worker.configured_enabled and knowledge_processing.enabled:
            worker_tasks.append(
                asyncio.create_task(
                    knowledge_worker.run_forever(),
                    name="ych-knowledge-worker",
                )
            )
        if proactive_worker.configured_enabled:
            worker_tasks.append(
                asyncio.create_task(
                    proactive_worker.run_forever(),
                    name="ych-proactive-worker",
                )
            )
        if qzone_worker.loop_should_start:
            worker_tasks.append(
                asyncio.create_task(
                    qzone_worker.run_forever(),
                    name="ych-qzone-worker",
                )
            )
        if owner_report_worker.configured_enabled:
            worker_tasks.append(
                asyncio.create_task(
                    owner_report_worker.run_forever(),
                    name="ych-owner-report-worker",
                )
            )
        if outbox_dispatcher.enabled:
            worker_tasks.append(
                asyncio.create_task(
                    outbox_dispatcher.run_forever(),
                    name="ych-outbox-worker",
                )
            )
        if image_orphan_worker.configured_enabled:
            worker_tasks.append(
                asyncio.create_task(
                    image_orphan_worker.run_forever(),
                    name="ych-image-orphan-worker",
                )
            )
        if reply_runtime_worker.loop_should_start:
            worker_tasks.append(
                asyncio.create_task(
                    reply_runtime_worker.run_forever(),
                    name="ych-reply-runtime-worker",
                )
            )
        try:
            yield
        finally:
            knowledge_worker.request_stop()
            proactive_worker.request_stop()
            qzone_worker.request_stop()
            owner_report_worker.request_stop()
            outbox_dispatcher.request_stop()
            image_orphan_worker.request_stop()
            reply_runtime_worker.request_stop()
            for worker_task in worker_tasks:
                try:
                    await asyncio.wait_for(worker_task, timeout=5)
                except TimeoutError:
                    worker_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await worker_task
                except Exception:
                    pass
            await chat_gateway.close()
            await vision_gateway.close()
            await image_gateway.close()
            await stats_gateway.close()
            await napcat_client.close()

    app = FastAPI(
        title="YCH Bot",
        version=__version__,
        description="Local-first QQ assistant control plane",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    ops_dashboard = OpsDashboardService(
        repository,
        flags=OpsFlags(
            timezone=active_settings.timezone,
            shadow_enabled=shadow_reply.enabled,
            outbound_enabled=active_settings.outbound_enabled,
            qzone_publish_enabled=active_settings.qzone_publish_enabled,
        ),
    )
    reply_operations = ReplyOperationsService(
        repository,
        flags=ReplyPipelineFlags(
            ingestion_enabled=active_settings.ingest_enabled,
            chat_model_configured=chat_model_configured,
            model_network_enabled=active_settings.model_network_enabled,
            chat_route_enabled=active_settings.chat_model_enabled,
            outbound_enabled=active_settings.outbound_enabled,
            onebot_token_configured=bool(active_settings.onebot_access_token),
            reply_worker_wired=True,
            reply_worker_enabled=active_settings.reply_worker_enabled,
        ),
    )
    app.state.settings = active_settings
    app.state.repository = repository
    app.state.managed_roots = managed_roots
    app.state.artifact_inventory = artifact_inventory
    app.state.retention = retention
    app.state.database_preflight = database_preflight
    app.state.ingestion = ingestion
    app.state.napcat_client = napcat_client
    app.state.control_service = control_service
    app.state.admin_sessions = admin_sessions
    app.state.privacy_jobs = privacy_jobs
    app.state.persona_service = persona_service
    app.state.memory_service = memory_service
    app.state.reply_style = reply_style
    app.state.knowledge_import = knowledge_import
    app.state.knowledge_processing = knowledge_processing
    app.state.knowledge_worker = knowledge_worker
    app.state.conversation_context = conversation_context
    app.state.shadow_reply = shadow_reply
    app.state.reply_runtime_control = reply_runtime_control
    app.state.reply_runtime_worker = reply_runtime_worker
    app.state.chat_gateway = chat_gateway
    app.state.vision_gateway = vision_gateway
    app.state.image_gateway = image_gateway
    app.state.image_generation = image_generation
    app.state.image_orphan_worker = image_orphan_worker
    app.state.proactive_messages = proactive_messages
    app.state.proactive_worker = proactive_worker
    app.state.stats_dashboard = stats_dashboard
    app.state.web_search = web_search
    app.state.daily_summaries = daily_summaries
    app.state.diary_service = diary_service
    app.state.material_service = material_service
    app.state.qzone_tasks = qzone_tasks
    app.state.qzone_worker = qzone_worker
    app.state.qzone_profile = qzone_profile
    app.state.owner_reports = owner_reports
    app.state.owner_report_worker = owner_report_worker
    app.state.outbox_dispatcher = outbox_dispatcher
    app.state.chat_model_guard = chat_model_guard
    app.state.vision_model_guard = vision_model_guard
    app.state.image_model_guard = image_model_guard
    app.state.stats_model_guard = stats_model_guard
    app.state.onebot_connection = connection_state
    app.state.reply_operations = reply_operations
    app.state.process_instance_id = active_process_instance_id
    app.state.launcher_preflight = launcher_result
    app.state.readiness_service = readiness_service
    app.state.readiness_guard = readiness_guard
    app.state.operational_api = operational_api

    @app.get("/health/live")
    async def live() -> dict[str, Any]:
        return {"status": "ok", "service": "ych-bot", "version": __version__}

    @app.get("/health/ready")
    async def ready() -> dict[str, Any]:
        database_ok = await repository.healthcheck()
        return {
            "status": "ready" if database_ok else "not_ready",
            "database": database_ok,
            "ingestion_enabled": active_settings.ingest_enabled,
            "outbound_enabled": active_settings.outbound_enabled,
        }

    @app.get("/api/v1/status")
    async def status() -> dict[str, Any]:
        counts = await repository.counts()
        chat_protection = (await chat_model_guard.snapshot()).as_dict()
        vision_protection = (await vision_model_guard.snapshot()).as_dict()
        image_protection = (await image_model_guard.snapshot()).as_dict()
        stats_protection = (await stats_model_guard.snapshot()).as_dict()
        return {
            "brand": CORE_IDENTITY.brand,
            "bot_qq": active_settings.bot_qq,
            "owner_qq": active_settings.owner_qq,
            "mode": "observe_only" if not active_settings.outbound_enabled else "active",
            "control": {
                "owner_reports_enabled": active_settings.owner_reports_enabled,
                "qzone_publish_enabled": active_settings.qzone_publish_enabled,
                "qzone_profile_collection_enabled": (
                    active_settings.qzone_profile_collection_enabled
                ),
            },
            "knowledge_worker": knowledge_worker.public_snapshot(),
            "proactive_messages": {
                "scheduler": proactive_worker.public_snapshot(),
                "delivery": outbox_dispatcher.snapshot(),
                "outbound_enabled": active_settings.outbound_enabled,
                "requires_owner_approval": True,
                "default_user_policy": "disabled",
                "summary": await repository.proactive_summary(),
            },
            "qzone": {
                "publisher": qzone_worker.public_snapshot(),
                "requires_owner_approval": True,
                "uses_real_calendar_time": True,
                "automatic_network_retry": False,
                "summary": await repository.qzone_summary(),
            },
            "owner_reports": {
                "worker": owner_report_worker.public_snapshot(),
                "delivery_route_enabled": active_settings.owner_reports_enabled,
                "automatic_retry": False,
                "summary": await repository.owner_report_summary(),
            },
            "models": {
                "chat": {
                    "configured": chat_model_configured,
                    "network_enabled": active_settings.model_network_enabled,
                    "route_enabled": active_settings.chat_model_enabled,
                    "shadow_enabled": shadow_reply.enabled,
                    "protocol": active_settings.chat_api_protocol,
                    "model": active_settings.chat_model or None,
                    "knowledge_processing_enabled": knowledge_processing.enabled,
                    "protection": chat_protection,
                },
                "vision": {
                    "configured": vision_model_configured,
                    "network_enabled": active_settings.model_network_enabled,
                    "route_enabled": active_settings.vision_model_enabled,
                    "active": vision_gateway_active,
                    "protocol": active_settings.vision_api_protocol,
                    "model": active_settings.vision_model or None,
                    "protection": vision_protection,
                },
                "image": {
                    "configured": image_model_configured,
                    "network_enabled": active_settings.model_network_enabled,
                    "route_enabled": active_settings.image_model_enabled,
                    "active": image_gateway_active,
                    "protocol": active_settings.image_api_protocol,
                    "model": active_settings.image_model or None,
                    "response_format": active_settings.image_response_format,
                    "task_execution_enabled": image_generation.enabled,
                    "content_review_enabled": image_generation.review_enabled,
                    "orphan_scan_enabled": image_generation.orphan_scan_enabled,
                    "local_artifact_ingestion": (
                        "supported"
                        if active_settings.image_response_format == "b64_json"
                        else "remote_url_rejected"
                    ),
                    "protection": image_protection,
                },
                "stats": {
                    "configured": stats_model_configured,
                    "network_enabled": active_settings.model_network_enabled,
                    "route_enabled": active_settings.stats_model_enabled,
                    "active": stats_gateway_active,
                    "protocol": active_settings.stats_api_protocol,
                    "model": active_settings.stats_model or None,
                    "protection": stats_protection,
                    "web_search_enabled": active_settings.web_search_enabled,
                },
                "routes_independent": True,
            },
            "onebot": {
                "connected": connection_state.connected,
                "connected_at": (
                    connection_state.connected_at.isoformat()
                    if connection_state.connected_at
                    else None
                ),
                "last_event_at": (
                    connection_state.last_event_at.isoformat()
                    if connection_state.last_event_at
                    else None
                ),
                "auth_configured": bool(active_settings.onebot_access_token),
                "authenticated_bot_qq": connection_state.authenticated_bot_qq,
                "exact_bot": connection_state.authenticated_bot_qq == active_settings.bot_qq,
                "stored_events": connection_state.stored_events,
                "duplicate_events": connection_state.duplicate_events,
                "ignored_events": connection_state.ignored_events,
            },
            "storage": counts,
        }

    @app.get("/api/v1/system/identity")
    async def system_identity(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        snapshot = await accounts.snapshot()
        return {
            "brand": CORE_IDENTITY.brand,
            "creator_name": CORE_IDENTITY.creator_name,
            "disclosure_policy": CORE_IDENTITY.disclosure_policy,
            "locked": True,
            "accounts_editable": True,
            "source": "compiled_core_identity_plus_instance_accounts",
            **snapshot,
        }

    @app.get("/api/v1/accounts")
    async def account_snapshot(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        return await accounts.snapshot()

    @app.put("/api/v1/accounts/owner")
    async def update_owner(
        body: OwnerUpdateRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await accounts.set_owner(body.owner_qq, updated_by="dashboard")
        except AccountError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/accounts/bots")
    async def create_bot(
        body: BotCreateRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await accounts.add_bot(body.qq, label=body.label, updated_by="dashboard")
        except AccountError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/accounts/bots/{bot_qq}/enable")
    async def enable_bot(bot_qq: str, authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await accounts.enable_bot(bot_qq, updated_by="dashboard")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/accounts/bots/{bot_qq}/disable")
    async def disable_bot(bot_qq: str, authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await accounts.disable_bot(bot_qq, updated_by="dashboard")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/api/v1/accounts/bots/{bot_qq}")
    async def update_bot(
        bot_qq: str,
        body: BotUpdateRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await accounts.update_bot(
                bot_qq,
                updated_by="dashboard",
                label=body.label,
                quota_user_default=body.quota_user_default,
                quota_group_default=body.quota_group_default,
                quota_user_reply=body.quota_user_reply,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/quotas")
    async def list_quotas(
        bot_qq: str,
        peer_kind: str | None = None,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"items": await quotas.list_quotas(bot_qq=bot_qq, peer_kind=peer_kind)}

    @app.put("/api/v1/quotas/{bot_qq}/{peer_kind}/{peer_id}")
    async def update_quota_limit(
        bot_qq: str,
        peer_kind: Literal["private", "group"],
        peer_id: str,
        body: QuotaLimitRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await quotas.set_daily_limit(
                bot_qq=bot_qq,
                peer_kind=peer_kind,
                peer_id=peer_id,
                daily_limit=body.daily_limit,
                display_name=body.display_name,
                updated_by="dashboard",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/quotas/{bot_qq}/{peer_kind}/{peer_id}/today-bonus")
    async def add_quota_today_bonus(
        bot_qq: str,
        peer_kind: Literal["private", "group"],
        peer_id: str,
        body: QuotaBonusRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await quotas.add_today_bonus(
                bot_qq=bot_qq,
                peer_kind=peer_kind,
                peer_id=peer_id,
                amount=body.amount,
                updated_by="dashboard",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/ops/snapshot")
    async def ops_snapshot(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        chat_protection = (await chat_model_guard.snapshot()).as_dict()
        image_protection = (await image_model_guard.snapshot()).as_dict()
        return await ops_dashboard.snapshot(
            workers={
                "knowledge": knowledge_worker.snapshot(),
                "proactive": proactive_worker.snapshot(),
                "qzone": qzone_worker.snapshot(),
                "owner_reports": owner_report_worker.snapshot(),
                "outbox": outbox_dispatcher.snapshot(),
                "image_orphan": image_orphan_worker.snapshot(),
            },
            protection={"chat": chat_protection, "image": image_protection},
            onebot={
                "connected": connection_state.connected,
                "last_event_at": (
                    connection_state.last_event_at.isoformat()
                    if connection_state.last_event_at
                    else None
                ),
            },
            mode="observe_only" if not active_settings.outbound_enabled else "active",
        )

    @app.get("/api/v1/ops/series")
    async def ops_series(
        days: int = 7,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await ops_dashboard.series(days=days)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/ops/activity")
    async def ops_activity(
        limit: int = 8,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return await ops_dashboard.activity(limit=limit)

    @app.get("/api/v1/stats/overview")
    async def stats_overview(
        range: Literal["today", "7d", "30d"] = "today",
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return await stats_dashboard.overview(range)

    @app.get("/api/v1/stats/summary")
    async def stats_summary(
        date: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            cached = await daily_summaries.get(date)
        except DailySummaryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return cached or {"day_key": date, "entries": [], "generated_at": None}

    @app.post("/api/v1/stats/summary")
    async def generate_stats_summary(
        date: str,
        force: bool = False,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await daily_summaries.generate(date, force=force)
        except DailySummaryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/chatlog/peers")
    async def chatlog_peers(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        return {"items": await stats_dashboard.chatlog_peers()}

    @app.get("/api/v1/chatlog")
    async def chatlog(
        kind: Literal["private", "group"] = "private",
        peer: str = "",
        after_id: str = "",
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await stats_dashboard.chatlog(kind=kind, peer_id=peer, after_id=after_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/diary")
    async def diary_list(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        return {"items": await diary_service.list_entries()}

    @app.get("/api/v1/diary/{day_key}")
    async def diary_get(day_key: str, authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            entry = await diary_service.get(day_key)
        except DiaryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if entry is None:
            return {"day_key": day_key, "content": "", "status": "pending"}
        return entry

    @app.put("/api/v1/diary")
    async def diary_save(
        body: DiarySaveRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await diary_service.save(day_key=body.day_key, content=body.content)
        except DiaryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/materials")
    async def materials_list(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        return {"items": await material_service.list_items()}

    @app.post("/api/v1/materials")
    async def materials_create(
        body: MaterialCreateRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await material_service.create(
                title=body.title,
                content=body.content,
                uploader_claim=body.uploader_claim,
                target_qq=body.target_qq,
            )
        except MaterialError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/materials/{material_id}/enable")
    async def materials_enable(
        material_id: str,
        body: MaterialEnableRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await material_service.set_enabled(material_id, enabled=body.enabled)
        except MaterialError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/users/summary")
    async def users_summary(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        summary = await repository.identity_summary()
        return {
            "bot_qq": active_settings.bot_qq,
            "classification_rule": "evidence_based",
            "default_history_access": "deny",
            **summary,
        }

    @app.get("/api/v1/privacy/status")
    async def privacy_status(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        summary = await repository.identity_summary()
        return {
            "default_history_access": "deny",
            "live_history_read_enabled": active_settings.live_history_enabled,
            "qzone_profile_collection_enabled": (active_settings.qzone_profile_collection_enabled),
            "qzone_profile_modes": summary.get("qzone_profile_modes", {}),
            "privacy_jobs_enabled": privacy_jobs.enabled,
            "document_import_enabled": active_settings.document_import_enabled,
            "document_ocr_enabled": active_settings.document_ocr_enabled,
            "image_content_review_enabled": active_settings.image_content_review_enabled,
            "image_orphan_scan_enabled": active_settings.image_orphan_scan_enabled,
            "shadow_inference_enabled": active_settings.shadow_inference_enabled,
            "knowledge_processing_enabled": active_settings.knowledge_processing_enabled,
            "knowledge_processing_execution_enabled": knowledge_processing.enabled,
            "frozen_users": summary["frozen_users"],
            "history_modes": summary["history_modes"],
        }

    @app.get("/api/v1/system/preflight")
    async def system_preflight(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        return await database_preflight.inspect()

    @app.get("/api/v1/operations/readiness/summary")
    async def operational_readiness_summary(
        controlled_scope: CapabilityScope = CapabilityScope.QQ_REPLY,
        bot_qq: str | None = None,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            return await operational_api.readiness_summary(
                authorization,
                controlled_scope=controlled_scope,
                bot_qq=bot_qq,
            )
        except OperationalApiError as exc:
            raise _operational_http_exception(exc) from exc

    @app.get("/api/v1/operations/readiness/current")
    async def operational_readiness_current(
        profile: ReadinessProfile,
        capability_scope: CapabilityScope,
        bot_qq: str | None = None,
        process_instance_id: str | None = None,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            return await operational_api.readiness_detail(
                authorization,
                profile=profile,
                capability_scope=capability_scope,
                bot_qq=bot_qq,
                process_instance_id=process_instance_id,
            )
        except OperationalApiError as exc:
            raise _operational_http_exception(exc) from exc

    @app.post("/api/v1/operations/readiness/refresh")
    async def operational_readiness_refresh(
        body: ReadinessRefreshRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            return await operational_api.refresh_readiness(
                authorization,
                profile=body.profile,
                capability_scope=body.capability_scope,
                bot_qq=body.bot_qq,
                process_instance_id=body.process_instance_id,
            )
        except OperationalApiError as exc:
            raise _operational_http_exception(exc) from exc

    @app.get("/api/v1/operations/readiness/history")
    async def operational_readiness_history(
        profile: ReadinessProfile,
        capability_scope: CapabilityScope,
        include_historical_instances: bool = True,
        limit: int = 50,
        bot_qq: str | None = None,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            return await operational_api.readiness_history(
                authorization,
                profile=profile,
                capability_scope=capability_scope,
                include_historical_instances=include_historical_instances,
                limit=limit,
                bot_qq=bot_qq,
            )
        except OperationalApiError as exc:
            raise _operational_http_exception(exc) from exc

    @app.get("/api/v1/operations/artifacts")
    async def operational_artifacts(
        owner_scope: ArtifactOwnerScope | None = None,
        owner_qq: str | None = None,
        artifact_type: ManagedArtifactType | None = None,
        verification_state: ArtifactVerificationState | None = None,
        reference_state: ArtifactReferenceState | None = None,
        retention_state: ArtifactRetentionState | None = None,
        limit: int = 500,
        bot_qq: str | None = None,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            return await operational_api.list_artifacts(
                authorization,
                owner_scope=owner_scope,
                owner_qq=owner_qq,
                artifact_type=artifact_type,
                verification_state=verification_state,
                reference_state=reference_state,
                retention_state=retention_state,
                limit=limit,
                bot_qq=bot_qq,
            )
        except OperationalApiError as exc:
            raise _operational_http_exception(exc) from exc

    @app.post("/api/v1/operations/artifacts/refresh")
    async def operational_artifacts_refresh(
        body: ArtifactRefreshRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            return await operational_api.refresh_artifacts(
                authorization,
                bot_qq=body.bot_qq,
                process_instance_id=body.process_instance_id,
                owner_scope=body.owner_scope,
                owner_qq=body.owner_qq,
            )
        except OperationalApiError as exc:
            raise _operational_http_exception(exc) from exc

    @app.post("/api/v1/operations/artifacts/retention/preview")
    async def operational_retention_preview(
        body: RetentionPreviewRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            return await operational_api.create_retention_preview(
                authorization,
                bot_qq=body.bot_qq,
                process_instance_id=body.process_instance_id,
                target_batch_type=body.target_batch_type,
                owner_scope=body.owner_scope,
                owner_qq=body.owner_qq,
            )
        except OperationalApiError as exc:
            raise _operational_http_exception(exc) from exc
        except RetentionError as exc:
            raise _operational_http_exception(retention_error_as_operational(exc)) from exc

    @app.post("/api/v1/operations/artifacts/retention/confirm")
    async def operational_retention_confirm(
        body: RetentionConfirmRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            return await operational_api.confirm_retention(
                authorization,
                bot_qq=body.bot_qq,
                process_instance_id=body.process_instance_id,
                confirmation_token=body.confirmation_token,
                expected_revision=body.expected_revision,
            )
        except OperationalApiError as exc:
            raise _operational_http_exception(exc) from exc
        except RetentionError as exc:
            raise _operational_http_exception(retention_error_as_operational(exc)) from exc

    @app.get("/api/v1/operations/artifacts/quarantine")
    async def operational_quarantine_history(
        owner_scope: ArtifactOwnerScope | None = None,
        owner_qq: str | None = None,
        state: QuarantineBatchState | None = None,
        bot_qq: str | None = None,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        try:
            return await operational_api.quarantine_history(
                authorization,
                owner_scope=owner_scope,
                owner_qq=owner_qq,
                state=state,
                bot_qq=bot_qq,
            )
        except OperationalApiError as exc:
            raise _operational_http_exception(exc) from exc

    @app.get("/api/v1/reply-runs")
    async def list_reply_runs(
        stage: str | None = None,
        limit: int = 50,
        offset: int = 0,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await reply_operations.list_runs(
                stage=stage,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/reply-runs/{run_id}")
    async def reply_run_detail(
        run_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            detail = await reply_operations.detail(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if detail is None:
            raise HTTPException(status_code=404, detail="reply run not found")
        return detail

    @app.get("/api/v1/reply-pipeline/readiness")
    async def reply_pipeline_readiness(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return await reply_operations.readiness(
            onebot_connected=connection_state.connected,
            outbound_worker=outbox_dispatcher.snapshot(),
            chat_protection=(await chat_model_guard.snapshot()).as_dict(),
            runtime_status=await reply_runtime_control.status(),
            runtime_worker=await reply_runtime_worker.snapshot(),
        )

    @app.get("/api/v1/reply-runtime")
    async def reply_runtime_status(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {
            **await reply_runtime_control.status(),
            "worker": await reply_runtime_worker.snapshot(),
        }

    @app.post("/api/v1/reply-runtime/preview")
    async def preview_reply_runtime_change(
        body: ReplyRuntimePreviewRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await reply_runtime_control.preview(action=body.action, payload=body.payload)
        except (ValueError, ReplyRuntimeControlError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/reply-runtime/confirm")
    async def confirm_reply_runtime_change(
        body: ReplyRuntimeConfirmRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await reply_runtime_control.confirm(
                token=body.confirmation_token,
                actor=await repository.current_owner_qq() or active_settings.owner_qq,
            )
        except (ValueError, ReplyRuntimeControlError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/reply-runtime/approvals")
    async def reply_runtime_approvals(
        status: str | None = None,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        items = await repository.reply_runtime_approvals(active_settings.bot_qq, status=status)
        return {
            "items": [
                {
                    key: item.get(key)
                    for key in (
                        "id",
                        "run_id",
                        "runtime_revision",
                        "status",
                        "expires_at",
                        "created_at",
                        "decided_at",
                    )
                }
                for item in items
            ]
        }

    @app.post("/api/v1/reply-runtime/worker/run-once")
    async def run_reply_runtime_once(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            progressed = await reply_runtime_worker.run_once()
        except Exception as exc:
            raise HTTPException(
                status_code=409, detail="reply runtime worker failed safely"
            ) from exc
        return {"progressed": progressed, "worker": await reply_runtime_worker.snapshot()}

    @app.post("/api/v1/system/backups/cleanup")
    async def cleanup_migration_backups(
        body: BackupCleanupRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        session_token = await require_admin(authorization)
        actor_id = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
        try:
            result = await retention.quarantine_legacy_migration_preview(
                cleanup_token=body.cleanup_token,
                actor_id=actor_id,
                actor_source="dashboard",
            )
        except RetentionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result

    async def require_admin(authorization: str = Header(default="")) -> str:
        if not admin_sessions.configured:
            raise HTTPException(status_code=503, detail="dashboard control token is not configured")
        token = _bearer_token(authorization)
        if not await admin_sessions.authenticate(token):
            raise HTTPException(status_code=401, detail="invalid or expired dashboard session")
        return token

    register_qualification_routes(
        app,
        require_admin=require_admin,
        service=qualification_api,
    )

    @app.post("/api/v1/auth/session")
    async def create_admin_session(
        authorization: str = Header(default=""),
    ) -> dict[str, str]:
        if not admin_sessions.configured:
            raise HTTPException(status_code=503, detail="dashboard control token is not configured")
        session = await admin_sessions.create(_bearer_token(authorization), source="dashboard")
        if session is None:
            raise HTTPException(status_code=401, detail="invalid dashboard bootstrap token")
        return {
            "access_token": session.token,
            "token_type": "bearer",
            "expires_at": session.expires_at,
        }

    @app.delete("/api/v1/auth/session")
    async def revoke_admin_session(
        authorization: str = Header(default=""),
    ) -> dict[str, bool]:
        token = await require_admin(authorization)
        return {"revoked": await admin_sessions.revoke(token)}

    @app.get("/api/v1/users")
    async def list_users(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        return {
            "items": await repository.list_known_users(),
            "summary": await repository.identity_summary(),
            "default_history_access": "deny",
            "live_history_read_enabled": active_settings.live_history_enabled,
            "classification_rule": "evidence_based",
        }

    @app.get("/api/v1/operator-labels")
    async def list_operator_labels(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"items": await repository.list_operator_labels()}

    @app.get("/api/v1/users/{user_qq}", dependencies=[])
    async def user_detail(
        user_qq: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        if not user_qq.isdigit():
            raise HTTPException(status_code=422, detail="user_qq must contain digits only")
        return await repository.user_detail(user_qq)

    @app.get("/api/v1/users/{user_qq}/reply-style")
    async def user_reply_style(
        user_qq: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await reply_style.policy(user_qq)
        except ReplyStyleError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/v1/users/{user_qq}/reply-style")
    async def update_user_reply_style(
        user_qq: str,
        body: ReplyStyleRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await reply_style.update_policy(
                user_qq=user_qq,
                min_bubbles=body.min_bubbles,
                max_bubbles=body.max_bubbles,
                sentence_min_chars=body.sentence_min_chars,
                sentence_max_chars=body.sentence_max_chars,
                updated_by=active_settings.owner_qq,
            )
        except ReplyStyleError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/users/{user_qq}/qzone-profile")
    async def qzone_profile_detail(
        user_qq: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        if not user_qq.isdigit():
            raise HTTPException(status_code=422, detail="user_qq must contain digits only")
        return await repository.qzone_profile_detail(user_qq)

    @app.post("/api/v1/users/{user_qq}/qzone-profile/preview")
    async def qzone_profile_preview(
        user_qq: str,
        payload: QzoneProfilePreviewRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        if not user_qq.isdigit():
            raise HTTPException(status_code=422, detail="user_qq must contain digits only")
        result = await qzone_profile.preview(
            user_qq,
            purpose=payload.purpose,
            actor_qq=active_settings.owner_qq,
            source="dashboard",
        )
        return result.public_dict()

    @app.get("/api/v1/control/approvals")
    async def approvals(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        return {"items": await repository.pending_approvals()}

    @app.get("/api/v1/privacy/requests")
    async def privacy_requests(
        status: str | None = None,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"items": await repository.privacy_requests(status=status)}

    @app.get("/api/v1/privacy/access-log")
    async def privacy_access_log(
        user_qq: str | None = None,
        decision: Literal["allowed", "denied"] | None = None,
        data_class: PrivacyDataClass | None = None,
        limit: int = 100,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        if user_qq is not None and not user_qq.isdigit():
            raise HTTPException(status_code=422, detail="user_qq must contain digits only")
        return await repository.data_access_audit(
            user_qq=user_qq,
            decision=decision,
            data_class=data_class.value if data_class else None,
            limit=limit,
        )

    @app.get("/api/v1/privacy/requests/{request_id}/preview")
    async def privacy_request_preview(
        request_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await privacy_jobs.preview(request_id)
        except PrivacyJobError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/privacy/requests/{request_id}/execute")
    async def execute_privacy_request(
        request_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            result = await privacy_jobs.execute(request_id)
        except PrivacyJobError as exc:
            status_code = 409 if privacy_jobs.enabled else 503
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return {
            "request_id": result.request_id,
            "status": result.status,
            "details": result.details,
        }

    @app.get("/api/v1/privacy/requests/{request_id}/artifact/verify")
    async def verify_privacy_artifact(
        request_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await privacy_jobs.verify_artifact(request_id)
        except PrivacyJobError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/personas")
    async def personas(
        user_qq: str | None = None,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        if user_qq is not None and not user_qq.isdigit():
            raise HTTPException(status_code=422, detail="user_qq must contain digits only")
        return {"items": await repository.persona_profiles(user_qq=user_qq)}

    @app.post("/api/v1/personas/manual")
    async def save_manual_persona(
        body: ManualPersonaRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, object]:
        await require_admin(authorization)
        try:
            return await persona_service.save_manual(
                scope=body.scope,
                user_qq=body.user_qq,
                name=body.name,
                definition=body.definition,
                created_by=active_settings.owner_qq,
            )
        except PersonaValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/personas/context-preview")
    async def persona_context_preview(
        conversation_kind: ConversationKind,
        peer_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        if not peer_id.isdigit():
            raise HTTPException(status_code=422, detail="peer_id must contain digits only")
        context = await persona_service.context(
            conversation_kind=conversation_kind,
            peer_id=peer_id,
        )
        return {
            "core_identity": context.core_identity.as_dict(),
            "core_directives": context.core_directives,
            "base_definition": context.base_definition,
            "private_definition": context.private_definition,
            "derived_persona": context.derived_persona,
            "user_context": context.user_context,
            "applied_profile_ids": context.applied_profile_ids,
        }

    @app.get("/api/v1/users/{user_qq}/memories")
    async def memories(
        user_qq: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        if not user_qq.isdigit():
            raise HTTPException(status_code=422, detail="user_qq must contain digits only")
        return {"items": await repository.memory_records(user_qq)}

    @app.post("/api/v1/users/{user_qq}/memories/manual")
    async def create_manual_memory(
        user_qq: str,
        body: ManualMemoryRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await memory_service.create_manual(
                user_qq=user_qq,
                kind=body.kind,
                key=body.key,
                value=body.value,
                created_by=active_settings.owner_qq,
                expires_at=body.expires_at,
            )
        except MemoryValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/v1/users/{user_qq}/memories/{memory_id}")
    async def forget_memory(
        user_qq: str,
        memory_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, bool]:
        await require_admin(authorization)
        if not user_qq.isdigit():
            raise HTTPException(status_code=422, detail="user_qq must contain digits only")
        records = await repository.memory_records(user_qq)
        if not any(record["id"] == memory_id for record in records):
            raise HTTPException(status_code=404, detail="memory not found for user")
        return {
            "forgotten": await repository.forget_memory(
                memory_id,
                forgotten_by=active_settings.owner_qq,
            )
        }

    @app.get("/api/v1/users/{user_qq}/memory-context-preview")
    async def memory_context_preview(
        user_qq: str,
        conversation_kind: ConversationKind,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        if not user_qq.isdigit():
            raise HTTPException(status_code=422, detail="user_qq must contain digits only")
        return {
            "items": await memory_service.context(
                conversation_kind=conversation_kind,
                peer_id=user_qq,
            )
        }

    @app.get("/api/v1/memory/conflicts")
    async def memory_conflicts(
        user_qq: str | None = None,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        if user_qq is not None and not user_qq.isdigit():
            raise HTTPException(status_code=422, detail="user_qq must contain digits only")
        return {"items": await repository.pending_memory_conflicts(user_qq)}

    @app.post("/api/v1/memory/conflicts/{conflict_id}/resolve")
    async def resolve_memory_conflict(
        conflict_id: str,
        body: ResolveMemoryConflictRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, bool]:
        await require_admin(authorization)
        resolved = await memory_service.resolve_conflict(
            conflict_id=conflict_id,
            resolution=body.resolution,
            resolved_by=active_settings.owner_qq,
        )
        if not resolved:
            raise HTTPException(status_code=404, detail="pending memory conflict not found")
        return {"resolved": True}

    @app.post("/api/v1/knowledge/documents")
    async def stage_knowledge_document(
        user_qq: Annotated[str, Form()],
        purpose: Annotated[DocumentPurpose, Form()],
        document: Annotated[UploadFile, File()],
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        maximum = active_settings.document_max_megabytes * 1024 * 1024
        content = await document.read(maximum + 1)
        await document.close()
        try:
            result = await knowledge_import.stage(
                user_qq=user_qq,
                purpose=purpose,
                filename=document.filename or "document.txt",
                media_type=document.content_type or "application/octet-stream",
                content=content,
                created_by=active_settings.owner_qq,
            )
        except KnowledgeImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "document_id": result.document_id,
            "job_id": result.job_id,
            "status": result.status,
            "details": result.details,
        }

    @app.get("/api/v1/knowledge/jobs")
    async def knowledge_jobs(
        user_qq: str | None = None,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        if user_qq is not None and not user_qq.isdigit():
            raise HTTPException(status_code=422, detail="user_qq must contain digits only")
        return {"items": await repository.knowledge_jobs(user_qq)}

    @app.get("/api/v1/knowledge/jobs/{job_id}")
    async def knowledge_job(
        job_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        job = await repository.knowledge_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="knowledge job not found")
        return job

    @app.post("/api/v1/knowledge/jobs/{job_id}/approve")
    async def approve_knowledge_job(
        job_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, object]:
        await require_admin(authorization)
        try:
            return await knowledge_import.approve_preview(
                job_id=job_id,
                approved_by=active_settings.owner_qq,
            )
        except KnowledgeImportError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/knowledge/jobs/{job_id}/process")
    async def process_knowledge_job(
        job_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, object]:
        await require_admin(authorization)
        try:
            result = await knowledge_processing.process(job_id)
        except KnowledgeProcessingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "job_id": result.job_id,
            "status": result.status,
            "completed_chunks": result.completed_chunks,
            "total_chunks": result.total_chunks,
        }

    @app.get("/api/v1/knowledge/worker")
    async def knowledge_worker_status(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return knowledge_worker.snapshot()

    @app.post("/api/v1/knowledge/worker/run-once")
    async def run_knowledge_worker_once(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        result = await knowledge_worker.run_once()
        return {
            "status": result.status,
            "job_id": result.job_id,
            "reason": result.reason,
            "worker": knowledge_worker.snapshot(),
        }

    @app.post("/api/v1/knowledge/worker/pause")
    async def pause_knowledge_worker(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"changed": knowledge_worker.pause(), **knowledge_worker.snapshot()}

    @app.post("/api/v1/knowledge/worker/resume")
    async def resume_knowledge_worker(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"changed": knowledge_worker.resume(), **knowledge_worker.snapshot()}

    @app.get("/api/v1/conversations/context-preview")
    async def conversation_context_preview(
        conversation_kind: ConversationKind,
        peer_id: str,
        actor_qq: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        if not peer_id.isdigit() or not actor_qq.isdigit():
            raise HTTPException(
                status_code=422, detail="peer_id and actor_qq must contain digits only"
            )
        context = await conversation_context.assemble(
            conversation_kind=conversation_kind,
            peer_id=peer_id,
            actor_qq=actor_qq,
        )
        return context.as_dict()

    @app.get("/api/v1/stickers")
    async def sticker_catalog(authorization: str = Header(default="")) -> dict[str, Any]:
        await require_admin(authorization)
        return sticker_library.catalog()

    @app.get("/api/v1/inference/runs")
    async def inference_runs(
        limit: int = 100,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"items": await repository.inference_runs(limit=limit)}

    @app.get("/api/v1/inference/runs/{run_id}")
    async def inference_run(
        run_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await shadow_reply.run_detail(run_id)
        except ShadowInferenceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/inference/replay")
    async def replay_shadow_inference(
        body: ShadowReplayRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await shadow_reply.replay(
                body.message_id,
                created_by=active_settings.owner_qq,
            )
        except ShadowInferenceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/models/protection")
    async def model_protection(
        route: Literal["chat", "image", "vision", "stats"] | None = None,
        limit: int = 100,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {
            "chat": (await chat_model_guard.snapshot()).as_dict(),
            "vision": (await vision_model_guard.snapshot()).as_dict(),
            "image": (await image_model_guard.snapshot()).as_dict(),
            "stats": (await stats_model_guard.snapshot()).as_dict(),
            "events": await repository.model_call_events(route=route, limit=limit),
        }

    @app.post("/api/v1/images/tasks")
    async def create_image_task(
        body: ImageTaskCreateRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, object]:
        await require_admin(authorization)
        try:
            return await image_generation.create_task(
                prompt=body.prompt,
                intended_use=body.intended_use,
                requested_by=active_settings.owner_qq,
                request_source="dashboard",
            )
        except ImageGenerationTaskError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/images/tasks")
    async def image_tasks(
        status: str | None = None,
        limit: int = 100,
        authorization: str = Header(default=""),
    ) -> dict[str, object]:
        await require_admin(authorization)
        try:
            return {"items": await image_generation.tasks(status=status, limit=limit)}
        except ImageGenerationTaskError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/images/tasks/{task_id}")
    async def image_task(
        task_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, object]:
        await require_admin(authorization)
        try:
            return await image_generation.task(task_id)
        except ImageGenerationTaskError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/images/tasks/{task_id}/generate")
    async def generate_image_task(
        task_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, object]:
        await require_admin(authorization)
        try:
            return await image_generation.generate(task_id)
        except ImageGenerationTaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/images/tasks/{task_id}/renew-approval")
    async def renew_image_approval(
        task_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, object]:
        await require_admin(authorization)
        try:
            return await image_generation.renew_approval(task_id)
        except ImageGenerationTaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/images/orphans/scan")
    async def scan_image_orphans(
        authorization: str = Header(default=""),
    ) -> dict[str, object]:
        await require_admin(authorization)
        try:
            return await image_generation.scan_orphans(created_by=active_settings.owner_qq)
        except ImageGenerationTaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/images/orphans/worker")
    async def image_orphan_worker_status(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return image_orphan_worker.snapshot()

    @app.post("/api/v1/images/orphans/worker/run-once")
    async def run_image_orphan_worker_once(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        result = await image_orphan_worker.run_once()
        return {
            "status": result.status,
            "scan_id": result.scan_id,
            "reason": result.reason,
            "worker": image_orphan_worker.snapshot(),
        }

    @app.post("/api/v1/images/orphans/worker/pause")
    async def pause_image_orphan_worker(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"changed": image_orphan_worker.pause(), **image_orphan_worker.snapshot()}

    @app.post("/api/v1/images/orphans/worker/resume")
    async def resume_image_orphan_worker(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"changed": image_orphan_worker.resume(), **image_orphan_worker.snapshot()}

    @app.get("/api/v1/images/artifacts/{artifact_id}/content")
    async def image_artifact_content(
        artifact_id: str,
        authorization: str = Header(default=""),
    ) -> FileResponse:
        await require_admin(authorization)
        try:
            path, media_type = await image_generation.artifact_file(artifact_id)
        except ImageGenerationTaskError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type=media_type)

    @app.get("/api/v1/owner/reports/summary")
    async def owner_report_summary(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {
            "worker": owner_report_worker.snapshot(),
            "delivery_route_enabled": active_settings.owner_reports_enabled,
            "outbound_enabled": active_settings.outbound_enabled,
            "automatic_retry": False,
            "summary": await repository.owner_report_summary(),
        }

    @app.get("/api/v1/owner/reports/policy")
    async def owner_report_policy(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return await owner_reports.policy()

    @app.put("/api/v1/owner/reports/policy")
    async def update_owner_report_policy(
        body: OwnerReportPolicyRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await owner_reports.update_policy(
                timezone_name=body.timezone,
                digest_local_time=body.digest_local_time,
                dedupe_window_seconds=body.dedupe_window_seconds,
                action_required_policy=body.action_required_policy,
                critical_policy=body.critical_policy,
                warning_policy=body.warning_policy,
                info_policy=body.info_policy,
                updated_by=active_settings.owner_qq,
            )
        except OwnerReportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/owner/reports/worker")
    async def owner_report_worker_status(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return owner_report_worker.snapshot()

    @app.post("/api/v1/owner/reports/worker/run-once")
    async def run_owner_report_worker_once(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        result = await owner_report_worker.run_once()
        return {
            "status": result.status,
            "report_id": result.report_id,
            "reason": result.reason,
            "worker": owner_report_worker.snapshot(),
        }

    @app.post("/api/v1/owner/reports/worker/pause")
    async def pause_owner_report_worker(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"changed": owner_report_worker.pause(), **owner_report_worker.snapshot()}

    @app.post("/api/v1/owner/reports/worker/resume")
    async def resume_owner_report_worker(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"changed": owner_report_worker.resume(), **owner_report_worker.snapshot()}

    @app.get("/api/v1/owner/reports")
    async def list_owner_reports(
        status: str | None = None,
        limit: int = 100,
        authorization: str = Header(default=""),
    ) -> dict[str, object]:
        await require_admin(authorization)
        return {"items": await repository.owner_reports(status=status, limit=limit)}

    @app.get("/api/v1/owner/reports/{report_id}")
    async def owner_report_detail(
        report_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await owner_reports.report(report_id)
        except OwnerReportError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/proactive/summary")
    async def proactive_summary(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {
            "scheduler": proactive_worker.snapshot(),
            "delivery": outbox_dispatcher.snapshot(),
            "outbound_enabled": active_settings.outbound_enabled,
            "requires_owner_approval": True,
            "uses_real_calendar_time": True,
            "summary": await repository.proactive_summary(),
        }

    @app.post("/api/v1/proactive/tasks")
    async def create_proactive_task(
        body: ProactiveTaskCreateRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await proactive_messages.create_task(
                target_qq=body.target_qq,
                content=body.content,
                scheduled_for=body.scheduled_for,
                timezone_name=body.timezone or active_settings.timezone,
                missed_policy=body.missed_policy,
                missed_grace_seconds=body.missed_grace_seconds,
                created_by=active_settings.owner_qq,
                source="dashboard",
            )
        except ProactiveMessageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/proactive/tasks")
    async def proactive_tasks(
        target_qq: str | None = None,
        status: ProactiveTaskStatus | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 100,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            items = await proactive_messages.tasks(
                target_qq=target_qq,
                status=status,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )
        except ProactiveMessageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"items": items}

    @app.get("/api/v1/proactive/tasks/{task_id}")
    async def proactive_task(
        task_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await proactive_messages.task(task_id)
        except ProactiveMessageError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/proactive/tasks/{task_id}/cancel")
    async def cancel_proactive_task(
        task_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await proactive_messages.cancel(task_id, actor_qq=active_settings.owner_qq)
        except ProactiveMessageError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/proactive/users/{user_qq}/policy")
    async def proactive_user_policy(
        user_qq: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await proactive_messages.policy(user_qq)
        except ProactiveMessageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/v1/proactive/users/{user_qq}/policy")
    async def update_proactive_user_policy(
        user_qq: str,
        body: ProactivePolicyRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await proactive_messages.update_policy(
                user_qq=user_qq,
                enabled=body.enabled,
                timezone_name=body.timezone,
                quiet_hours_enabled=body.quiet_hours_enabled,
                quiet_start=body.quiet_start,
                quiet_end=body.quiet_end,
                quiet_behavior=body.quiet_behavior,
                daily_limit=body.daily_limit,
                minimum_interval_seconds=body.minimum_interval_seconds,
                auto_content_enabled=body.auto_content_enabled,
                send_diary=body.send_diary,
                updated_by=active_settings.owner_qq,
            )
        except ProactiveMessageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/proactive/worker")
    async def proactive_worker_status(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return proactive_worker.snapshot()

    @app.post("/api/v1/proactive/worker/run-once")
    async def run_proactive_worker_once(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        result = await proactive_worker.run_once()
        return {
            "status": result.status,
            "task_id": result.task_id,
            "reason": result.reason,
            "worker": proactive_worker.snapshot(),
        }

    @app.post("/api/v1/proactive/worker/pause")
    async def pause_proactive_worker(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"changed": proactive_worker.pause(), **proactive_worker.snapshot()}

    @app.post("/api/v1/proactive/worker/resume")
    async def resume_proactive_worker(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"changed": proactive_worker.resume(), **proactive_worker.snapshot()}

    @app.get("/api/v1/qzone/summary")
    async def qzone_summary(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {
            "publisher": qzone_worker.snapshot(),
            "requires_owner_approval": True,
            "uses_real_calendar_time": True,
            "automatic_network_retry": False,
            "summary": await repository.qzone_summary(),
        }

    @app.post("/api/v1/qzone/drafts")
    async def create_qzone_draft(
        body: QzoneDraftCreateRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await qzone_tasks.create_draft(
                content=body.content,
                visibility=body.visibility,
                target_uins=body.target_uins,
                created_by=active_settings.owner_qq,
                source="dashboard",
            )
        except QzoneTaskError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/qzone/posts")
    async def create_qzone_publish_request(
        body: QzonePublishCreateRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await qzone_tasks.request_publish(
                content=body.content,
                scheduled_for=body.scheduled_for,
                timezone_name=body.timezone or active_settings.timezone,
                created_by=active_settings.owner_qq,
                source="dashboard",
                visibility=body.visibility,
                target_uins=body.target_uins,
                missed_policy=body.missed_policy,
                missed_grace_seconds=body.missed_grace_seconds,
            )
        except QzoneTaskError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/qzone/posts")
    async def qzone_posts(
        status: QzonePostStatus | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 100,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            items = await qzone_tasks.posts(
                status=status,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )
        except QzoneTaskError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"items": items}

    @app.get("/api/v1/qzone/posts/{post_id}")
    async def qzone_post(
        post_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await qzone_tasks.post(post_id)
        except QzoneTaskError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/qzone/posts/{post_id}/cancel")
    async def cancel_qzone_post(
        post_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await qzone_tasks.cancel(post_id, actor_qq=active_settings.owner_qq)
        except QzoneTaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/qzone/posts/{post_id}/revoke")
    async def revoke_qzone_post(
        post_id: str,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await qzone_tasks.request_revoke(
                post_id,
                actor_qq=active_settings.owner_qq,
                source="dashboard",
            )
        except QzoneTaskError as exc:
            status = 404 if "not found" in str(exc) else 409
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.get("/api/v1/qzone/policy")
    async def qzone_policy(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return await qzone_tasks.policy()

    @app.put("/api/v1/qzone/policy")
    async def update_qzone_policy(
        body: QzonePolicyRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        try:
            return await qzone_tasks.update_policy(
                QzoneSchedulePolicy(
                    timezone=body.timezone,
                    quiet_hours_enabled=body.quiet_hours_enabled,
                    quiet_start=body.quiet_start,
                    quiet_end=body.quiet_end,
                    quiet_behavior=body.quiet_behavior,
                    daily_limit=body.daily_limit,
                    minimum_interval_seconds=body.minimum_interval_seconds,
                ),
                updated_by=active_settings.owner_qq,
            )
        except QzoneTaskError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/qzone/worker")
    async def qzone_worker_status(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return qzone_worker.snapshot()

    @app.post("/api/v1/qzone/worker/run-once")
    async def run_qzone_worker_once(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        result = await qzone_worker.run_once()
        return {
            "status": result.status,
            "post_id": result.post_id,
            "reason": result.reason,
            "worker": qzone_worker.snapshot(),
        }

    @app.post("/api/v1/qzone/worker/pause")
    async def pause_qzone_worker(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"changed": qzone_worker.pause(), **qzone_worker.snapshot()}

    @app.post("/api/v1/qzone/worker/resume")
    async def resume_qzone_worker(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"changed": qzone_worker.resume(), **qzone_worker.snapshot()}

    @app.get("/api/v1/outbox/worker")
    async def outbox_worker_status(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return outbox_dispatcher.snapshot()

    @app.post("/api/v1/outbox/worker/run-once")
    async def run_outbox_worker_once(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        sent = await outbox_dispatcher.run_once()
        return {"sent": sent, **outbox_dispatcher.snapshot()}

    @app.post("/api/v1/outbox/worker/pause")
    async def pause_outbox_worker(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"changed": outbox_dispatcher.pause(), **outbox_dispatcher.snapshot()}

    @app.post("/api/v1/outbox/worker/resume")
    async def resume_outbox_worker(
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"changed": outbox_dispatcher.resume(), **outbox_dispatcher.snapshot()}

    @app.get("/api/v1/reply-candidates")
    async def reply_candidates(
        status: str | None = None,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {"items": await repository.reply_candidates(status=status)}

    @app.post("/api/v1/control/commands")
    async def dashboard_command(
        body: DashboardCommandRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        command_id = str(uuid4())
        command = OwnerCommand(
            id=command_id,
            kind=body.action,
            actor_qq=await repository.current_owner_qq() or active_settings.owner_qq,
            source_message_id=f"dashboard:{command_id}",
            arguments=body.arguments,
            source=ControlSource.DASHBOARD,
        )
        result = await control_service.execute(command)
        return {"status": result.status, "command_id": result.command_id, "data": result.data}

    @app.get("/api/v1/control/commands")
    async def control_commands(
        action: OwnerCommandKind | None = None,
        status: str | None = None,
        limit: int = 100,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        return {
            "items": await repository.control_commands(
                action=action.value if action else None,
                status=status,
                limit=limit,
            )
        }

    @app.websocket(active_settings.onebot_ws_path)
    async def onebot_events(websocket: WebSocket) -> None:
        if not _authorized(websocket, active_settings.onebot_access_token):
            await websocket.close(code=1008, reason="invalid access token")
            return

        await websocket.accept()
        connection_state.connected_now()
        try:
            while True:
                payload = await websocket.receive_json()
                if not isinstance(payload, dict):
                    connection_state.observe("ignored")
                    continue
                result = await ingestion.ingest(payload)
                self_id = str(payload.get("self_id") or "")
                connection_state.observe(
                    result.status,
                    bot_qq=self_id if self_id.isdigit() else None,
                )
        except WebSocketDisconnect:
            pass
        finally:
            connection_state.disconnected_now()

    return app
