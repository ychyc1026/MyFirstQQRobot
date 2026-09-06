"""Configuration loading with safe, local-first defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _as_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    bot_qq: str = "2000000002"
    owner_qq: str = "2000000001"
    napcat_http_url: str = "http://127.0.0.1:3000"
    onebot_ws_path: str = "/onebot/v11/ws"
    onebot_access_token: str = ""
    admin_host: str = "127.0.0.1"
    admin_port: int = 8765
    admin_access_token: str = ""
    admin_session_hours: int = 8
    timezone: str = "Asia/Shanghai"
    database_path: Path | None = None
    ingest_enabled: bool = True
    outbound_enabled: bool = False
    outbound_poll_seconds: float = 1
    outbound_max_attempts: int = 5
    outbound_retry_base_seconds: int = 30
    reply_worker_enabled: bool = False
    reply_worker_poll_seconds: float = 1
    reply_runtime_max_mode: str = "observe_only"
    reply_approval_ttl_seconds: int = 1800
    qzone_publish_enabled: bool = False
    qzone_profile_collection_enabled: bool = False
    live_history_enabled: bool = False
    qzone_worker_enabled: bool = False
    qzone_worker_poll_seconds: float = 5
    qzone_schedule_tolerance_seconds: int = 60
    qzone_default_quiet_start: str = "22:00"
    qzone_default_quiet_end: str = "08:00"
    qzone_default_daily_limit: int = 1
    qzone_default_minimum_interval_seconds: int = 21_600
    qzone_max_future_days: int = 366
    owner_reports_enabled: bool = False
    owner_report_worker_enabled: bool = False
    owner_report_worker_poll_seconds: float = 5
    owner_report_dedupe_window_seconds: int = 1_800
    owner_report_digest_local_time: str = "09:00"
    privacy_jobs_enabled: bool = False
    migration_backup_enabled: bool = True
    document_import_enabled: bool = True
    knowledge_processing_enabled: bool = False
    knowledge_worker_enabled: bool = False
    knowledge_worker_poll_seconds: float = 5
    knowledge_worker_max_attempts: int = 3
    knowledge_worker_retry_base_seconds: int = 60
    proactive_scheduler_enabled: bool = False
    proactive_scheduler_poll_seconds: float = 5
    proactive_schedule_tolerance_seconds: int = 60
    proactive_policy_recheck_seconds: int = 300
    proactive_default_quiet_start: str = "22:00"
    proactive_default_quiet_end: str = "08:00"
    proactive_default_daily_limit: int = 3
    proactive_default_minimum_interval_seconds: int = 3600
    proactive_max_future_days: int = 366
    document_max_megabytes: int = 100
    document_max_pages: int = 2000
    document_ocr_enabled: bool = False
    chat_api_base: str = ""
    chat_api_key: str = ""
    chat_model: str = ""
    chat_api_protocol: str = "disabled"
    chat_model_enabled: bool = False
    chat_timeout_seconds: float = 60
    chat_max_retries: int = 2
    chat_daily_request_limit: int = 200
    chat_daily_token_limit: int = 500_000
    chat_failure_threshold: int = 5
    chat_circuit_cooldown_seconds: int = 300
    vision_api_base: str = ""
    vision_api_key: str = ""
    vision_model: str = ""
    vision_api_protocol: str = "disabled"
    vision_model_enabled: bool = False
    vision_timeout_seconds: float = 90
    vision_max_retries: int = 1
    vision_daily_request_limit: int = 40
    vision_daily_token_limit: int = 200_000
    vision_failure_threshold: int = 3
    vision_circuit_cooldown_seconds: int = 300
    image_api_base: str = ""
    image_api_key: str = ""
    image_model: str = ""
    image_api_protocol: str = "disabled"
    image_response_format: str = "b64_json"
    image_model_enabled: bool = False
    image_timeout_seconds: float = 180
    image_max_retries: int = 1
    image_daily_request_limit: int = 20
    image_failure_threshold: int = 3
    image_circuit_cooldown_seconds: int = 600
    image_artifact_max_megabytes: int = 20
    image_artifact_max_pixels: int = 40_000_000
    image_max_artifacts_per_task: int = 4
    image_content_review_enabled: bool = False
    image_orphan_scan_enabled: bool = False
    image_orphan_worker_poll_seconds: float = 300
    shadow_inference_enabled: bool = False
    model_network_enabled: bool = False
    stats_api_base: str = ""
    stats_api_key: str = ""
    stats_model: str = ""
    stats_api_protocol: str = "disabled"
    stats_model_enabled: bool = False
    stats_timeout_seconds: float = 60
    stats_max_retries: int = 1
    stats_daily_request_limit: int = 40
    stats_daily_token_limit: int = 100_000
    stats_failure_threshold: int = 3
    stats_circuit_cooldown_seconds: int = 300
    web_search_enabled: bool = False
    web_search_api_base: str = ""
    web_search_api_key: str = ""

    def __post_init__(self) -> None:
        if not self.bot_qq.isdigit():
            raise ValueError("YCH_BOT_QQ must contain digits only")
        if not self.owner_qq.isdigit():
            raise ValueError("YCH_OWNER_QQ must contain digits only")
        if self.owner_qq == self.bot_qq:
            raise ValueError("YCH_OWNER_QQ and YCH_BOT_QQ must be different accounts")
        if not self.onebot_ws_path.startswith("/"):
            raise ValueError("YCH_ONEBOT_WS_PATH must start with /")
        if not (1 <= self.admin_port <= 65535):
            raise ValueError("YCH_ADMIN_PORT is outside the valid port range")
        if not (1 <= self.admin_session_hours <= 24):
            raise ValueError("YCH_ADMIN_SESSION_HOURS must be between 1 and 24")
        if not (0.1 <= self.outbound_poll_seconds <= 300):
            raise ValueError("YCH_OUTBOUND_POLL_SECONDS must be between 0.1 and 300")
        if not (1 <= self.outbound_max_attempts <= 20):
            raise ValueError("YCH_OUTBOUND_MAX_ATTEMPTS must be between 1 and 20")
        if not (1 <= self.outbound_retry_base_seconds <= 3600):
            raise ValueError("YCH_OUTBOUND_RETRY_BASE_SECONDS must be between 1 and 3600")
        if not (0.1 <= self.reply_worker_poll_seconds <= 300):
            raise ValueError("YCH_REPLY_WORKER_POLL_SECONDS must be between 0.1 and 300")
        if self.reply_runtime_max_mode not in {
            "observe_only",
            "shadow",
            "owner_approved",
            "limited_auto",
            "auto",
        }:
            raise ValueError("YCH_REPLY_RUNTIME_MAX_MODE is unsupported")
        if not (60 <= self.reply_approval_ttl_seconds <= 3600):
            raise ValueError("YCH_REPLY_APPROVAL_TTL_SECONDS must be between 60 and 3600")
        if not (0.1 <= self.qzone_worker_poll_seconds <= 300):
            raise ValueError("YCH_QZONE_WORKER_POLL_SECONDS must be between 0.1 and 300")
        if not (0 <= self.qzone_schedule_tolerance_seconds <= 600):
            raise ValueError("YCH_QZONE_SCHEDULE_TOLERANCE_SECONDS must be between 0 and 600")
        if not (1 <= self.qzone_default_daily_limit <= 20):
            raise ValueError("YCH_QZONE_DEFAULT_DAILY_LIMIT must be between 1 and 20")
        if not (0 <= self.qzone_default_minimum_interval_seconds <= 604_800):
            raise ValueError(
                "YCH_QZONE_DEFAULT_MINIMUM_INTERVAL_SECONDS must be between 0 and 604800"
            )
        if not (1 <= self.qzone_max_future_days <= 3660):
            raise ValueError("YCH_QZONE_MAX_FUTURE_DAYS must be between 1 and 3660")
        if not (0.1 <= self.owner_report_worker_poll_seconds <= 300):
            raise ValueError("YCH_OWNER_REPORT_WORKER_POLL_SECONDS must be between 0.1 and 300")
        if not (60 <= self.owner_report_dedupe_window_seconds <= 86_400):
            raise ValueError("YCH_OWNER_REPORT_DEDUPE_WINDOW_SECONDS must be between 60 and 86400")
        if not (1 <= self.document_max_megabytes <= 500):
            raise ValueError("YCH_DOCUMENT_MAX_MEGABYTES must be between 1 and 500")
        if not (1 <= self.document_max_pages <= 10_000):
            raise ValueError("YCH_DOCUMENT_MAX_PAGES must be between 1 and 10000")
        if not (0.1 <= self.knowledge_worker_poll_seconds <= 300):
            raise ValueError("YCH_KNOWLEDGE_WORKER_POLL_SECONDS must be between 0.1 and 300")
        if not (1 <= self.knowledge_worker_max_attempts <= 20):
            raise ValueError("YCH_KNOWLEDGE_WORKER_MAX_ATTEMPTS must be between 1 and 20")
        if not (1 <= self.knowledge_worker_retry_base_seconds <= 86_400):
            raise ValueError("YCH_KNOWLEDGE_WORKER_RETRY_BASE_SECONDS must be between 1 and 86400")
        if not (0.1 <= self.proactive_scheduler_poll_seconds <= 300):
            raise ValueError("YCH_PROACTIVE_SCHEDULER_POLL_SECONDS must be between 0.1 and 300")
        if not (0 <= self.proactive_schedule_tolerance_seconds <= 600):
            raise ValueError("YCH_PROACTIVE_SCHEDULE_TOLERANCE_SECONDS must be between 0 and 600")
        if not (10 <= self.proactive_policy_recheck_seconds <= 86_400):
            raise ValueError("YCH_PROACTIVE_POLICY_RECHECK_SECONDS must be between 10 and 86400")
        if not (1 <= self.proactive_default_daily_limit <= 100):
            raise ValueError("YCH_PROACTIVE_DEFAULT_DAILY_LIMIT must be between 1 and 100")
        if not (0 <= self.proactive_default_minimum_interval_seconds <= 86_400):
            raise ValueError(
                "YCH_PROACTIVE_DEFAULT_MINIMUM_INTERVAL_SECONDS must be between 0 and 86400"
            )
        if not (1 <= self.proactive_max_future_days <= 3660):
            raise ValueError("YCH_PROACTIVE_MAX_FUTURE_DAYS must be between 1 and 3660")
        supported_protocols = {"disabled", "openai_compatible"}
        if self.chat_api_protocol not in supported_protocols:
            raise ValueError("YCH_CHAT_API_PROTOCOL is unsupported")
        if self.vision_api_protocol not in supported_protocols:
            raise ValueError("YCH_VISION_API_PROTOCOL is unsupported")
        if self.image_api_protocol not in supported_protocols:
            raise ValueError("YCH_IMAGE_API_PROTOCOL is unsupported")
        if self.stats_api_protocol not in supported_protocols:
            raise ValueError("YCH_STATS_API_PROTOCOL is unsupported")
        if self.image_response_format not in {"b64_json", "url"}:
            raise ValueError("YCH_IMAGE_RESPONSE_FORMAT must be b64_json or url")
        if (
            self.chat_timeout_seconds <= 0
            or self.vision_timeout_seconds <= 0
            or self.image_timeout_seconds <= 0
        ):
            raise ValueError("model timeouts must be positive")
        if not (0 <= self.chat_max_retries <= 5):
            raise ValueError("YCH_CHAT_MAX_RETRIES must be between 0 and 5")
        if not (0 <= self.vision_max_retries <= 5):
            raise ValueError("YCH_VISION_MAX_RETRIES must be between 0 and 5")
        if not (0 <= self.image_max_retries <= 5):
            raise ValueError("YCH_IMAGE_MAX_RETRIES must be between 0 and 5")
        if self.chat_daily_request_limit < 1:
            raise ValueError("YCH_CHAT_DAILY_REQUEST_LIMIT must be positive")
        if self.chat_daily_token_limit < 1:
            raise ValueError("YCH_CHAT_DAILY_TOKEN_LIMIT must be positive")
        if self.vision_daily_request_limit < 1:
            raise ValueError("YCH_VISION_DAILY_REQUEST_LIMIT must be positive")
        if self.vision_daily_token_limit < 1:
            raise ValueError("YCH_VISION_DAILY_TOKEN_LIMIT must be positive")
        if self.image_daily_request_limit < 1:
            raise ValueError("YCH_IMAGE_DAILY_REQUEST_LIMIT must be positive")
        if not (1 <= self.chat_failure_threshold <= 20):
            raise ValueError("YCH_CHAT_FAILURE_THRESHOLD must be between 1 and 20")
        if not (1 <= self.vision_failure_threshold <= 20):
            raise ValueError("YCH_VISION_FAILURE_THRESHOLD must be between 1 and 20")
        if not (1 <= self.image_failure_threshold <= 20):
            raise ValueError("YCH_IMAGE_FAILURE_THRESHOLD must be between 1 and 20")
        if not (1 <= self.chat_circuit_cooldown_seconds <= 86_400):
            raise ValueError("YCH_CHAT_CIRCUIT_COOLDOWN_SECONDS must be between 1 and 86400")
        if not (1 <= self.vision_circuit_cooldown_seconds <= 86_400):
            raise ValueError("YCH_VISION_CIRCUIT_COOLDOWN_SECONDS must be between 1 and 86400")
        if not (1 <= self.image_circuit_cooldown_seconds <= 86_400):
            raise ValueError("YCH_IMAGE_CIRCUIT_COOLDOWN_SECONDS must be between 1 and 86400")
        if self.stats_timeout_seconds <= 0:
            raise ValueError("model timeouts must be positive")
        if not (0 <= self.stats_max_retries <= 5):
            raise ValueError("YCH_STATS_MAX_RETRIES must be between 0 and 5")
        if self.stats_daily_request_limit < 1:
            raise ValueError("YCH_STATS_DAILY_REQUEST_LIMIT must be positive")
        if self.stats_daily_token_limit < 1:
            raise ValueError("YCH_STATS_DAILY_TOKEN_LIMIT must be positive")
        if not (1 <= self.stats_failure_threshold <= 20):
            raise ValueError("YCH_STATS_FAILURE_THRESHOLD must be between 1 and 20")
        if not (1 <= self.stats_circuit_cooldown_seconds <= 86_400):
            raise ValueError("YCH_STATS_CIRCUIT_COOLDOWN_SECONDS must be between 1 and 86400")
        if not (1 <= self.image_artifact_max_megabytes <= 100):
            raise ValueError("YCH_IMAGE_ARTIFACT_MAX_MEGABYTES must be between 1 and 100")
        if not (1 <= self.image_artifact_max_pixels <= 100_000_000):
            raise ValueError("YCH_IMAGE_ARTIFACT_MAX_PIXELS must be between 1 and 100000000")
        if not (1 <= self.image_max_artifacts_per_task <= 8):
            raise ValueError("YCH_IMAGE_MAX_ARTIFACTS_PER_TASK must be between 1 and 8")
        if not (0.1 <= self.image_orphan_worker_poll_seconds <= 86_400):
            raise ValueError("YCH_IMAGE_ORPHAN_WORKER_POLL_SECONDS must be between 0.1 and 86400")
        if self.database_path is None:
            object.__setattr__(
                self,
                "database_path",
                self.project_root / "storage" / "runtime" / "ych.sqlite3",
            )

    @classmethod
    def load(cls, env_file: Path | None = None) -> Settings:
        project_root = Path(__file__).resolve().parents[3]
        file_values = _read_env_file(env_file or project_root / ".env")

        def value(name: str, default: str) -> str:
            return os.environ.get(name, file_values.get(name, default))

        raw_db_path = value("YCH_DATABASE_PATH", "storage/runtime/ych.sqlite3")
        db_path = Path(raw_db_path)
        if not db_path.is_absolute():
            db_path = project_root / db_path

        return cls(
            project_root=project_root,
            bot_qq=value("YCH_BOT_QQ", "2000000002"),
            owner_qq=value("YCH_OWNER_QQ", "2000000001"),
            napcat_http_url=value("YCH_NAPCAT_HTTP_URL", "http://127.0.0.1:3000").rstrip("/"),
            onebot_ws_path=value("YCH_ONEBOT_WS_PATH", "/onebot/v11/ws"),
            onebot_access_token=value("YCH_ONEBOT_ACCESS_TOKEN", ""),
            admin_host=value("YCH_ADMIN_HOST", "127.0.0.1"),
            admin_port=int(value("YCH_ADMIN_PORT", "8765")),
            admin_access_token=value("YCH_ADMIN_ACCESS_TOKEN", ""),
            admin_session_hours=int(value("YCH_ADMIN_SESSION_HOURS", "8")),
            timezone=value("YCH_TIMEZONE", "Asia/Shanghai"),
            database_path=db_path.resolve(),
            ingest_enabled=_as_bool(value("YCH_INGEST_ENABLED", "true"), name="YCH_INGEST_ENABLED"),
            outbound_enabled=_as_bool(
                value("YCH_OUTBOUND_ENABLED", "false"),
                name="YCH_OUTBOUND_ENABLED",
            ),
            outbound_poll_seconds=float(value("YCH_OUTBOUND_POLL_SECONDS", "1")),
            outbound_max_attempts=int(value("YCH_OUTBOUND_MAX_ATTEMPTS", "5")),
            outbound_retry_base_seconds=int(value("YCH_OUTBOUND_RETRY_BASE_SECONDS", "30")),
            reply_worker_enabled=_as_bool(
                value("YCH_REPLY_WORKER_ENABLED", "false"),
                name="YCH_REPLY_WORKER_ENABLED",
            ),
            reply_worker_poll_seconds=float(value("YCH_REPLY_WORKER_POLL_SECONDS", "1")),
            reply_runtime_max_mode=value("YCH_REPLY_RUNTIME_MAX_MODE", "observe_only"),
            reply_approval_ttl_seconds=int(value("YCH_REPLY_APPROVAL_TTL_SECONDS", "1800")),
            qzone_publish_enabled=_as_bool(
                value("YCH_QZONE_PUBLISH_ENABLED", "false"),
                name="YCH_QZONE_PUBLISH_ENABLED",
            ),
            qzone_profile_collection_enabled=_as_bool(
                value("YCH_QZONE_PROFILE_COLLECTION_ENABLED", "false"),
                name="YCH_QZONE_PROFILE_COLLECTION_ENABLED",
            ),
            live_history_enabled=_as_bool(
                value("YCH_LIVE_HISTORY_ENABLED", "false"),
                name="YCH_LIVE_HISTORY_ENABLED",
            ),
            qzone_worker_enabled=_as_bool(
                value("YCH_QZONE_WORKER_ENABLED", "false"),
                name="YCH_QZONE_WORKER_ENABLED",
            ),
            qzone_worker_poll_seconds=float(value("YCH_QZONE_WORKER_POLL_SECONDS", "5")),
            qzone_schedule_tolerance_seconds=int(
                value("YCH_QZONE_SCHEDULE_TOLERANCE_SECONDS", "60")
            ),
            qzone_default_quiet_start=value("YCH_QZONE_DEFAULT_QUIET_START", "22:00"),
            qzone_default_quiet_end=value("YCH_QZONE_DEFAULT_QUIET_END", "08:00"),
            qzone_default_daily_limit=int(value("YCH_QZONE_DEFAULT_DAILY_LIMIT", "1")),
            qzone_default_minimum_interval_seconds=int(
                value("YCH_QZONE_DEFAULT_MINIMUM_INTERVAL_SECONDS", "21600")
            ),
            qzone_max_future_days=int(value("YCH_QZONE_MAX_FUTURE_DAYS", "366")),
            owner_reports_enabled=_as_bool(
                value("YCH_OWNER_REPORTS_ENABLED", "false"),
                name="YCH_OWNER_REPORTS_ENABLED",
            ),
            owner_report_worker_enabled=_as_bool(
                value("YCH_OWNER_REPORT_WORKER_ENABLED", "false"),
                name="YCH_OWNER_REPORT_WORKER_ENABLED",
            ),
            owner_report_worker_poll_seconds=float(
                value("YCH_OWNER_REPORT_WORKER_POLL_SECONDS", "5")
            ),
            owner_report_dedupe_window_seconds=int(
                value("YCH_OWNER_REPORT_DEDUPE_WINDOW_SECONDS", "1800")
            ),
            owner_report_digest_local_time=value("YCH_OWNER_REPORT_DIGEST_LOCAL_TIME", "09:00"),
            privacy_jobs_enabled=_as_bool(
                value("YCH_PRIVACY_JOBS_ENABLED", "false"),
                name="YCH_PRIVACY_JOBS_ENABLED",
            ),
            migration_backup_enabled=_as_bool(
                value("YCH_MIGRATION_BACKUP_ENABLED", "true"),
                name="YCH_MIGRATION_BACKUP_ENABLED",
            ),
            document_import_enabled=_as_bool(
                value("YCH_DOCUMENT_IMPORT_ENABLED", "true"),
                name="YCH_DOCUMENT_IMPORT_ENABLED",
            ),
            knowledge_processing_enabled=_as_bool(
                value("YCH_KNOWLEDGE_PROCESSING_ENABLED", "false"),
                name="YCH_KNOWLEDGE_PROCESSING_ENABLED",
            ),
            knowledge_worker_enabled=_as_bool(
                value("YCH_KNOWLEDGE_WORKER_ENABLED", "false"),
                name="YCH_KNOWLEDGE_WORKER_ENABLED",
            ),
            knowledge_worker_poll_seconds=float(value("YCH_KNOWLEDGE_WORKER_POLL_SECONDS", "5")),
            knowledge_worker_max_attempts=int(value("YCH_KNOWLEDGE_WORKER_MAX_ATTEMPTS", "3")),
            knowledge_worker_retry_base_seconds=int(
                value("YCH_KNOWLEDGE_WORKER_RETRY_BASE_SECONDS", "60")
            ),
            proactive_scheduler_enabled=_as_bool(
                value("YCH_PROACTIVE_SCHEDULER_ENABLED", "false"),
                name="YCH_PROACTIVE_SCHEDULER_ENABLED",
            ),
            proactive_scheduler_poll_seconds=float(
                value("YCH_PROACTIVE_SCHEDULER_POLL_SECONDS", "5")
            ),
            proactive_schedule_tolerance_seconds=int(
                value("YCH_PROACTIVE_SCHEDULE_TOLERANCE_SECONDS", "60")
            ),
            proactive_policy_recheck_seconds=int(
                value("YCH_PROACTIVE_POLICY_RECHECK_SECONDS", "300")
            ),
            proactive_default_quiet_start=value("YCH_PROACTIVE_DEFAULT_QUIET_START", "22:00"),
            proactive_default_quiet_end=value("YCH_PROACTIVE_DEFAULT_QUIET_END", "08:00"),
            proactive_default_daily_limit=int(value("YCH_PROACTIVE_DEFAULT_DAILY_LIMIT", "3")),
            proactive_default_minimum_interval_seconds=int(
                value("YCH_PROACTIVE_DEFAULT_MINIMUM_INTERVAL_SECONDS", "3600")
            ),
            proactive_max_future_days=int(value("YCH_PROACTIVE_MAX_FUTURE_DAYS", "366")),
            document_max_megabytes=int(value("YCH_DOCUMENT_MAX_MEGABYTES", "100")),
            document_max_pages=int(value("YCH_DOCUMENT_MAX_PAGES", "2000")),
            document_ocr_enabled=_as_bool(
                value("YCH_DOCUMENT_OCR_ENABLED", "false"),
                name="YCH_DOCUMENT_OCR_ENABLED",
            ),
            chat_api_base=value("YCH_CHAT_API_BASE", "").rstrip("/"),
            chat_api_key=value("YCH_CHAT_API_KEY", ""),
            chat_model=value("YCH_CHAT_MODEL", ""),
            chat_api_protocol=value("YCH_CHAT_API_PROTOCOL", "disabled"),
            chat_model_enabled=_as_bool(
                value("YCH_CHAT_MODEL_ENABLED", "false"),
                name="YCH_CHAT_MODEL_ENABLED",
            ),
            chat_timeout_seconds=float(value("YCH_CHAT_TIMEOUT_SECONDS", "60")),
            chat_max_retries=int(value("YCH_CHAT_MAX_RETRIES", "2")),
            chat_daily_request_limit=int(value("YCH_CHAT_DAILY_REQUEST_LIMIT", "200")),
            chat_daily_token_limit=int(value("YCH_CHAT_DAILY_TOKEN_LIMIT", "500000")),
            chat_failure_threshold=int(value("YCH_CHAT_FAILURE_THRESHOLD", "5")),
            chat_circuit_cooldown_seconds=int(value("YCH_CHAT_CIRCUIT_COOLDOWN_SECONDS", "300")),
            vision_api_base=value("YCH_VISION_API_BASE", "").rstrip("/"),
            vision_api_key=value("YCH_VISION_API_KEY", ""),
            vision_model=value("YCH_VISION_MODEL", ""),
            vision_api_protocol=value("YCH_VISION_API_PROTOCOL", "disabled"),
            vision_model_enabled=_as_bool(
                value("YCH_VISION_MODEL_ENABLED", "false"),
                name="YCH_VISION_MODEL_ENABLED",
            ),
            vision_timeout_seconds=float(value("YCH_VISION_TIMEOUT_SECONDS", "90")),
            vision_max_retries=int(value("YCH_VISION_MAX_RETRIES", "1")),
            vision_daily_request_limit=int(value("YCH_VISION_DAILY_REQUEST_LIMIT", "40")),
            vision_daily_token_limit=int(value("YCH_VISION_DAILY_TOKEN_LIMIT", "200000")),
            vision_failure_threshold=int(value("YCH_VISION_FAILURE_THRESHOLD", "3")),
            vision_circuit_cooldown_seconds=int(
                value("YCH_VISION_CIRCUIT_COOLDOWN_SECONDS", "300")
            ),
            image_api_base=value("YCH_IMAGE_API_BASE", "").rstrip("/"),
            image_api_key=value("YCH_IMAGE_API_KEY", ""),
            image_model=value("YCH_IMAGE_MODEL", ""),
            image_api_protocol=value("YCH_IMAGE_API_PROTOCOL", "disabled"),
            image_response_format=value("YCH_IMAGE_RESPONSE_FORMAT", "b64_json"),
            image_model_enabled=_as_bool(
                value("YCH_IMAGE_MODEL_ENABLED", "false"),
                name="YCH_IMAGE_MODEL_ENABLED",
            ),
            image_timeout_seconds=float(value("YCH_IMAGE_TIMEOUT_SECONDS", "180")),
            image_max_retries=int(value("YCH_IMAGE_MAX_RETRIES", "1")),
            image_daily_request_limit=int(value("YCH_IMAGE_DAILY_REQUEST_LIMIT", "20")),
            image_failure_threshold=int(value("YCH_IMAGE_FAILURE_THRESHOLD", "3")),
            image_circuit_cooldown_seconds=int(value("YCH_IMAGE_CIRCUIT_COOLDOWN_SECONDS", "600")),
            image_artifact_max_megabytes=int(value("YCH_IMAGE_ARTIFACT_MAX_MEGABYTES", "20")),
            image_artifact_max_pixels=int(value("YCH_IMAGE_ARTIFACT_MAX_PIXELS", "40000000")),
            image_max_artifacts_per_task=int(value("YCH_IMAGE_MAX_ARTIFACTS_PER_TASK", "4")),
            image_content_review_enabled=_as_bool(
                value("YCH_IMAGE_CONTENT_REVIEW_ENABLED", "false"),
                name="YCH_IMAGE_CONTENT_REVIEW_ENABLED",
            ),
            image_orphan_scan_enabled=_as_bool(
                value("YCH_IMAGE_ORPHAN_SCAN_ENABLED", "false"),
                name="YCH_IMAGE_ORPHAN_SCAN_ENABLED",
            ),
            image_orphan_worker_poll_seconds=float(
                value("YCH_IMAGE_ORPHAN_WORKER_POLL_SECONDS", "300")
            ),
            shadow_inference_enabled=_as_bool(
                value("YCH_SHADOW_INFERENCE_ENABLED", "false"),
                name="YCH_SHADOW_INFERENCE_ENABLED",
            ),
            model_network_enabled=_as_bool(
                value("YCH_MODEL_NETWORK_ENABLED", "false"),
                name="YCH_MODEL_NETWORK_ENABLED",
            ),
            stats_api_base=value("YCH_STATS_API_BASE", "").rstrip("/"),
            stats_api_key=value("YCH_STATS_API_KEY", ""),
            stats_model=value("YCH_STATS_MODEL", ""),
            stats_api_protocol=value("YCH_STATS_API_PROTOCOL", "disabled"),
            stats_model_enabled=_as_bool(
                value("YCH_STATS_MODEL_ENABLED", "false"),
                name="YCH_STATS_MODEL_ENABLED",
            ),
            stats_timeout_seconds=float(value("YCH_STATS_TIMEOUT_SECONDS", "60")),
            stats_max_retries=int(value("YCH_STATS_MAX_RETRIES", "1")),
            stats_daily_request_limit=int(value("YCH_STATS_DAILY_REQUEST_LIMIT", "40")),
            stats_daily_token_limit=int(value("YCH_STATS_DAILY_TOKEN_LIMIT", "100000")),
            stats_failure_threshold=int(value("YCH_STATS_FAILURE_THRESHOLD", "3")),
            stats_circuit_cooldown_seconds=int(value("YCH_STATS_CIRCUIT_COOLDOWN_SECONDS", "300")),
            web_search_enabled=_as_bool(
                value("YCH_WEB_SEARCH_ENABLED", "false"),
                name="YCH_WEB_SEARCH_ENABLED",
            ),
            web_search_api_base=value("YCH_WEB_SEARCH_API_BASE", "").rstrip("/"),
            web_search_api_key=value("YCH_WEB_SEARCH_API_KEY", ""),
        )
