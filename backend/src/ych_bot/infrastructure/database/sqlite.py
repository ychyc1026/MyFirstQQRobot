"""SQLite persistence with atomic event deduplication and a durable outbox."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ych_bot.domain.control import ApprovalStatus, OwnerCommand
from ych_bot.domain.identity import FriendState, HistoryAccessMode, QzoneProfileAccessMode
from ych_bot.domain.images import ImageTaskStatus
from ych_bot.domain.memory import (
    MemoryConflictResolution,
    MemoryKind,
    MemoryRecord,
    MemorySource,
    MemoryStatus,
)
from ych_bot.domain.models import (
    ConversationKind,
    MessageDirection,
    MessageSegment,
    OutboundMessage,
    OutboxStatus,
    UnifiedMessage,
    safe_model_image_url,
    safe_napcat_image_file_id,
)
from ych_bot.domain.persona import PersonaProfile, ProfileScope, ProfileSource, UserUnderstanding
from ych_bot.domain.proactive import (
    MissedTaskPolicy,
    ProactiveTaskStatus,
    ProactiveUserPolicy,
)
from ych_bot.domain.readiness import CapabilityScope
from ych_bot.domain.reply_style import UserReplyStylePolicy

from .accounts import AccountRepositoryMixin
from .content import ContentRepositoryMixin
from .operational_readiness import OperationalReadinessRepositoryMixin
from .qualification import QualificationRepositoryMixin
from .qzone import QzoneRepositoryMixin
from .qzone_profile import (
    QzoneProfileRepositoryMixin,
    _snapshot_view,
    collection_card,
)
from .reply_pipeline import ReplyPipelineRepositoryMixin
from .reply_runtime import ReplyRuntimeRepositoryMixin
from .reports import OwnerReportRepositoryMixin, record_owner_report_on_connection
from .stats import StatsRepositoryMixin

LATEST_SCHEMA_VERSION = 34

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inbound_events (
    event_key TEXT PRIMARY KEY,
    bot_qq TEXT NOT NULL,
    post_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    qq_id TEXT PRIMARY KEY,
    relationship_state TEXT NOT NULL DEFAULT 'unknown',
    history_policy TEXT NOT NULL DEFAULT 'none',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operator_labels (
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    label TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    PRIMARY KEY (subject_kind, subject_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    peer_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE REFERENCES inbound_events(event_key),
    source_message_id TEXT NOT NULL,
    bot_qq TEXT NOT NULL,
    direction TEXT NOT NULL,
    conversation_key TEXT NOT NULL REFERENCES conversations(conversation_key),
    sender_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    plain_text TEXT NOT NULL,
    segments_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    reply_to_message_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_time
ON messages(conversation_key, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_sender_time
ON messages(sender_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    conversation_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    segments_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_outbox_ready
ON outbox(status, available_at, created_at);

CREATE TABLE IF NOT EXISTS control_commands (
    id TEXT PRIMARY KEY,
    source_message_id TEXT NOT NULL,
    actor_qq TEXT NOT NULL,
    action TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received',
    result_json TEXT,
    created_at TEXT NOT NULL,
    handled_at TEXT
);

CREATE TABLE IF NOT EXISTS qzone_posts (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    images_json TEXT NOT NULL DEFAULT '[]',
    visibility INTEGER NOT NULL DEFAULT 1,
    target_uins_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    scheduled_for TEXT,
    approved_by TEXT,
    approved_at TEXT,
    published_at TEXT,
    qzone_tid TEXT,
    last_error TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_qzone_posts_schedule
ON qzone_posts(status, scheduled_for);

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    request_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    approval_code TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    requested_to TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_reports (
    id TEXT PRIMARY KEY,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    related_type TEXT,
    related_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    acknowledged_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_owner_reports_delivery
ON owner_reports(status, severity, created_at);

CREATE TABLE IF NOT EXISTS friend_baselines (
    id TEXT PRIMARY KEY,
    bot_qq TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    friend_count INTEGER NOT NULL,
    snapshot_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_friend_baselines_bot_time
ON friend_baselines(bot_qq, captured_at DESC);

CREATE TABLE IF NOT EXISTS user_relationships (
    user_qq TEXT PRIMARY KEY,
    friend_state TEXT NOT NULL DEFAULT 'unknown',
    state_source TEXT NOT NULL DEFAULT 'insufficient_evidence',
    confidence REAL NOT NULL DEFAULT 0,
    first_observed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    owner_overridden_by TEXT,
    data_frozen INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS identity_evidence (
    id TEXT PRIMARY KEY,
    user_qq TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    source TEXT NOT NULL,
    value_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_identity_evidence_user_time
ON identity_evidence(user_qq, observed_at DESC);

CREATE TABLE IF NOT EXISTS history_access_policies (
    user_qq TEXT PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'deny',
    selected_from TEXT,
    selected_to TEXT,
    max_messages INTEGER,
    allow_media INTEGER NOT NULL DEFAULT 0,
    one_time_remaining INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS privacy_scope_policies (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    data_class TEXT NOT NULL,
    collection_allowed INTEGER NOT NULL DEFAULT 0,
    model_use_allowed INTEGER NOT NULL DEFAULT 0,
    qzone_use_allowed INTEGER NOT NULL DEFAULT 0,
    retention_days INTEGER,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(scope_type, scope_id, data_class)
);

CREATE TABLE IF NOT EXISTS privacy_requests (
    id TEXT PRIMARY KEY,
    user_qq TEXT NOT NULL,
    request_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    result_json TEXT
);

CREATE TABLE IF NOT EXISTS data_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_qq TEXT NOT NULL,
    data_class TEXT NOT NULL,
    purpose TEXT NOT NULL,
    accessor TEXT NOT NULL,
    decision TEXT NOT NULL,
    policy_snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_data_access_log_created
ON data_access_log(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_data_access_log_user_created
ON data_access_log(user_qq, created_at DESC);

CREATE TABLE IF NOT EXISTS approval_payloads (
    approval_id TEXT PRIMARY KEY REFERENCES approval_requests(id),
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS friend_baseline_candidates (
    id TEXT PRIMARY KEY,
    bot_qq TEXT NOT NULL,
    friend_ids_json TEXT NOT NULL,
    friend_count INTEGER NOT NULL,
    snapshot_hash TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_friend_baseline_candidates_status
ON friend_baseline_candidates(status, expires_at);

CREATE TABLE IF NOT EXISTS admin_sessions (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT,
    created_from TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_sessions_expiry
ON admin_sessions(expires_at, revoked_at);

CREATE TABLE IF NOT EXISTS privacy_job_artifacts (
    request_id TEXT NOT NULL REFERENCES privacy_requests(id),
    owner_qq TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(request_id, artifact_type)
);

CREATE TABLE IF NOT EXISTS privacy_delete_previews (
    request_id TEXT PRIMARY KEY REFERENCES privacy_requests(id),
    impact_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id TEXT PRIMARY KEY,
    subject_user_qq TEXT NOT NULL,
    purpose TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    text_length INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_user
ON knowledge_documents(subject_user_qq, purpose, created_at DESC);

CREATE TABLE IF NOT EXISTS user_understanding_profiles (
    id TEXT PRIMARY KEY,
    user_qq TEXT NOT NULL,
    source_document_id TEXT REFERENCES knowledge_documents(id),
    summary_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_understanding_active
ON user_understanding_profiles(user_qq, status, version DESC);

CREATE TABLE IF NOT EXISTS persona_profiles (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_document_id TEXT REFERENCES knowledge_documents(id),
    name TEXT NOT NULL,
    developer_definition TEXT NOT NULL,
    traits_json TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_persona_profiles_active
ON persona_profiles(scope_type, scope_id, source_type, status, version DESC);

CREATE TABLE IF NOT EXISTS persona_profile_evidence (
    profile_id TEXT PRIMARY KEY REFERENCES persona_profiles(id) ON DELETE CASCADE,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_records (
    id TEXT PRIMARY KEY,
    user_qq TEXT NOT NULL,
    memory_kind TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    expires_at TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_records_retrieval
ON memory_records(user_qq, status, memory_kind, memory_key);

CREATE TABLE IF NOT EXISTS memory_evidence (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
    evidence_type TEXT NOT NULL,
    source_id TEXT,
    excerpt_hash TEXT,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_conflicts (
    id TEXT PRIMARY KEY,
    user_qq TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    left_memory_id TEXT NOT NULL REFERENCES memory_records(id),
    right_memory_id TEXT NOT NULL REFERENCES memory_records(id),
    status TEXT NOT NULL,
    resolved_by TEXT,
    resolution TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_memory_conflicts_pending
ON memory_conflicts(user_qq, status, created_at DESC);

CREATE TABLE IF NOT EXISTS document_blobs (
    document_id TEXT PRIMARY KEY REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    storage_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    detected_format TEXT NOT NULL,
    byte_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content_text TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_order
ON document_chunks(document_id, chunk_index);

CREATE TABLE IF NOT EXISTS knowledge_processing_jobs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    user_qq TEXT NOT NULL,
    purpose TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    requested_by TEXT NOT NULL,
    result_preview_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_knowledge_jobs_status
ON knowledge_processing_jobs(status, created_at);

CREATE TABLE IF NOT EXISTS knowledge_job_checkpoints (
    job_id TEXT PRIMARY KEY REFERENCES knowledge_processing_jobs(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    lease_token TEXT,
    lease_expires_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error_type TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_chunk_analyses (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES knowledge_processing_jobs(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    purpose TEXT NOT NULL,
    result_json TEXT NOT NULL,
    provider_request_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_analyses_job
ON knowledge_chunk_analyses(job_id, chunk_index);

CREATE TABLE IF NOT EXISTS inference_runs (
    id TEXT PRIMARY KEY,
    source_message_id TEXT NOT NULL,
    conversation_key TEXT NOT NULL,
    actor_qq TEXT NOT NULL,
    bot_qq TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    model_route TEXT NOT NULL,
    provider_request_id TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    error_type TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_inference_runs_message
ON inference_runs(source_message_id, created_at DESC);

CREATE TABLE IF NOT EXISTS reply_candidates (
    id TEXT PRIMARY KEY,
    inference_run_id TEXT NOT NULL UNIQUE REFERENCES inference_runs(id),
    source_message_id TEXT NOT NULL,
    conversation_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    safety_flags_json TEXT NOT NULL,
    bubbles_json TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_reply_candidates_status
ON reply_candidates(status, created_at DESC);

CREATE TABLE IF NOT EXISTS model_call_events (
    id TEXT PRIMARY KEY,
    route TEXT NOT NULL,
    request_id TEXT NOT NULL,
    day_key TEXT NOT NULL,
    status TEXT NOT NULL,
    reserved_tokens INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    failure_type TEXT,
    rejection_reason TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(route, request_id)
);

CREATE INDEX IF NOT EXISTS idx_model_call_events_route_day
ON model_call_events(route, day_key, status, created_at);

CREATE TABLE IF NOT EXISTS image_generation_tasks (
    id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    intended_use TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    request_source TEXT NOT NULL,
    model_route TEXT NOT NULL,
    current_request_id TEXT,
    provider_request_id TEXT,
    approval_id TEXT REFERENCES approval_requests(id),
    error_type TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    approved_by TEXT,
    approved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_image_generation_tasks_status
ON image_generation_tasks(status, created_at DESC);

CREATE TABLE IF NOT EXISTS image_artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES image_generation_tasks(id) ON DELETE CASCADE,
    storage_path TEXT NOT NULL UNIQUE,
    content_sha256 TEXT NOT NULL,
    media_type TEXT NOT NULL,
    byte_count INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_image_artifacts_task
ON image_artifacts(task_id, created_at);

CREATE TABLE IF NOT EXISTS image_artifact_reviews (
    artifact_id TEXT PRIMARY KEY REFERENCES image_artifacts(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES image_generation_tasks(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL,
    labels_json TEXT NOT NULL,
    engine TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_image_artifact_reviews_task
ON image_artifact_reviews(task_id, created_at);

CREATE TABLE IF NOT EXISTS image_orphan_scans (
    id TEXT PRIMARY KEY,
    orphan_count INTEGER NOT NULL,
    missing_count INTEGER NOT NULL,
    details_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_reply_style_policies (
    user_qq TEXT PRIMARY KEY,
    min_bubbles INTEGER NOT NULL DEFAULT 1,
    max_bubbles INTEGER NOT NULL DEFAULT 1,
    sentence_min_chars INTEGER NOT NULL DEFAULT 12,
    sentence_max_chars INTEGER NOT NULL DEFAULT 80,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proactive_user_policies (
    user_qq TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    timezone TEXT NOT NULL,
    quiet_hours_enabled INTEGER NOT NULL DEFAULT 1,
    quiet_start TEXT NOT NULL,
    quiet_end TEXT NOT NULL,
    quiet_behavior TEXT NOT NULL,
    daily_limit INTEGER NOT NULL,
    minimum_interval_seconds INTEGER NOT NULL,
    auto_content_enabled INTEGER NOT NULL DEFAULT 0,
    send_diary INTEGER NOT NULL DEFAULT 0,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proactive_message_tasks (
    id TEXT PRIMARY KEY,
    target_qq TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    original_scheduled_for TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    timezone TEXT NOT NULL,
    missed_policy TEXT NOT NULL,
    missed_grace_seconds INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    source TEXT NOT NULL,
    content_source TEXT NOT NULL DEFAULT 'manual',
    approval_id TEXT REFERENCES approval_requests(id),
    approved_by TEXT,
    approved_at TEXT,
    next_eligible_at TEXT,
    hold_reason TEXT,
    first_evaluated_at TEXT,
    last_evaluated_at TEXT,
    claim_expires_at TEXT,
    evaluation_attempts INTEGER NOT NULL DEFAULT 0,
    outbox_id TEXT REFERENCES outbox(id),
    enqueued_at TEXT,
    sent_at TEXT,
    cancelled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proactive_tasks_due
ON proactive_message_tasks(status, next_eligible_at, scheduled_for);

CREATE INDEX IF NOT EXISTS idx_proactive_tasks_target_time
ON proactive_message_tasks(target_qq, original_scheduled_for DESC);

CREATE TABLE IF NOT EXISTS proactive_delivery_events (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES proactive_message_tasks(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    reason TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proactive_events_task_time
ON proactive_delivery_events(task_id, occurred_at);

CREATE TABLE IF NOT EXISTS qzone_schedule_policy (
    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
    timezone TEXT NOT NULL,
    quiet_hours_enabled INTEGER NOT NULL DEFAULT 1,
    quiet_start TEXT NOT NULL,
    quiet_end TEXT NOT NULL,
    quiet_behavior TEXT NOT NULL,
    daily_limit INTEGER NOT NULL,
    minimum_interval_seconds INTEGER NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qzone_post_runtime (
    post_id TEXT PRIMARY KEY REFERENCES qzone_posts(id) ON DELETE CASCADE,
    original_scheduled_for TEXT NOT NULL,
    timezone TEXT NOT NULL,
    missed_policy TEXT NOT NULL,
    missed_grace_seconds INTEGER NOT NULL,
    approval_id TEXT REFERENCES approval_requests(id),
    next_eligible_at TEXT NOT NULL,
    hold_reason TEXT,
    first_evaluated_at TEXT,
    last_evaluated_at TEXT,
    lease_expires_at TEXT,
    evaluation_attempts INTEGER NOT NULL DEFAULT 0,
    owner_exception INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_qzone_runtime_due
ON qzone_post_runtime(next_eligible_at, lease_expires_at);

CREATE TABLE IF NOT EXISTS qzone_post_events (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL REFERENCES qzone_posts(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    reason TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_qzone_post_events_time
ON qzone_post_events(post_id, occurred_at);

CREATE TABLE IF NOT EXISTS owner_report_delivery_policy (
    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
    timezone TEXT NOT NULL,
    digest_local_time TEXT NOT NULL,
    dedupe_window_seconds INTEGER NOT NULL,
    action_required_policy TEXT NOT NULL,
    critical_policy TEXT NOT NULL,
    warning_policy TEXT NOT NULL,
    info_policy TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_report_runtime (
    report_id TEXT PRIMARY KEY REFERENCES owner_reports(id) ON DELETE CASCADE,
    dedupe_key TEXT NOT NULL,
    delivery_policy TEXT NOT NULL,
    delivery_status TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    first_occurred_at TEXT NOT NULL,
    last_occurred_at TEXT NOT NULL,
    next_eligible_at TEXT NOT NULL,
    outbox_id TEXT,
    lease_expires_at TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_owner_report_runtime_due
ON owner_report_runtime(delivery_status, next_eligible_at, lease_expires_at);

CREATE INDEX IF NOT EXISTS idx_owner_report_runtime_dedupe
ON owner_report_runtime(dedupe_key, last_occurred_at);

CREATE TABLE IF NOT EXISTS owner_report_events (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES owner_reports(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    reason TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_owner_report_events_time
ON owner_report_events(report_id, occurred_at);

CREATE TABLE IF NOT EXISTS qzone_profile_access_policies (
    user_qq TEXT PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'deny',
    max_items INTEGER,
    expires_at TEXT,
    one_time_remaining INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qzone_profile_snapshots (
    id TEXT PRIMARY KEY,
    user_qq TEXT NOT NULL,
    purpose TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_qzone_profile_snapshots_user
ON qzone_profile_snapshots(user_qq, fetched_at DESC);

CREATE TABLE IF NOT EXISTS worker_runtime_state (
    worker_name TEXT PRIMARY KEY,
    paused INTEGER NOT NULL DEFAULT 0,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instance_owner (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    owner_qq TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bots (
    qq TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    quota_user_default INTEGER NOT NULL DEFAULT 500000,
    quota_group_default INTEGER NOT NULL DEFAULT 2000000,
    quota_user_reply TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_quota_overrides (
    bot_qq TEXT NOT NULL,
    peer_kind TEXT NOT NULL,
    peer_id TEXT NOT NULL,
    daily_limit INTEGER NOT NULL,
    today_bonus INTEGER NOT NULL DEFAULT 0,
    bonus_day TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (bot_qq, peer_kind, peer_id)
);

CREATE TABLE IF NOT EXISTS chat_quota_notices (
    bot_qq TEXT NOT NULL,
    peer_kind TEXT NOT NULL,
    peer_id TEXT NOT NULL,
    day_key TEXT NOT NULL,
    owner_notified INTEGER NOT NULL DEFAULT 0,
    user_replied INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bot_qq, peer_kind, peer_id, day_key)
);

CREATE TABLE IF NOT EXISTS daily_summaries (
    id TEXT PRIMARY KEY,
    day_key TEXT NOT NULL UNIQUE,
    entries_json TEXT NOT NULL,
    source_peer_ids_json TEXT NOT NULL DEFAULT '[]',
    model_route TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diary_entries (
    id TEXT PRIMARY KEY,
    day_key TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proactive_materials (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    file_name TEXT NOT NULL DEFAULT '',
    uploader_claim TEXT NOT NULL,
    ai_verdict TEXT NOT NULL DEFAULT 'pending',
    ai_reason TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS privacy_subject_links (
    subject_user_qq TEXT NOT NULL,
    record_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(subject_user_qq, record_type, record_id, source_path)
);

CREATE INDEX IF NOT EXISTS idx_privacy_subject_links_record
ON privacy_subject_links(record_type, record_id);

CREATE TABLE IF NOT EXISTS reply_runs (
    id TEXT PRIMARY KEY,
    bot_qq TEXT NOT NULL,
    conversation_key TEXT NOT NULL REFERENCES conversations(conversation_key) ON DELETE CASCADE,
    conversation_kind TEXT NOT NULL CHECK(conversation_kind IN ('private', 'group')),
    subject_user_qq TEXT,
    stage TEXT NOT NULL CHECK(stage IN (
        'pending', 'settling', 'assembling_context', 'calling_model',
        'planning_reply', 'creating_outbox', 'awaiting_delivery',
        'awaiting_approval', 'shadow_completed',
        'completed', 'suppressed', 'failed', 'cancelled'
    )),
    policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    settled_until TEXT,
    reply_plan_json TEXT,
    failure_code TEXT,
    failure_category TEXT,
    failure_detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    CHECK(
        (conversation_kind = 'private' AND subject_user_qq IS NOT NULL)
        OR (conversation_kind = 'group' AND subject_user_qq IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_reply_runs_stage_updated
ON reply_runs(stage, updated_at);

CREATE INDEX IF NOT EXISTS idx_reply_runs_conversation_created
ON reply_runs(conversation_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_reply_runs_subject_created
ON reply_runs(subject_user_qq, created_at DESC)
WHERE subject_user_qq IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_reply_runs_one_active_conversation
ON reply_runs(conversation_key)
WHERE stage NOT IN ('shadow_completed', 'completed', 'suppressed', 'failed', 'cancelled');

CREATE TABLE IF NOT EXISTS reply_run_triggers (
    run_id TEXT NOT NULL REFERENCES reply_runs(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    added_at TEXT NOT NULL,
    PRIMARY KEY(run_id, message_id),
    UNIQUE(run_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_reply_run_triggers_message
ON reply_run_triggers(message_id);

CREATE TABLE IF NOT EXISTS reply_context_manifests (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES reply_runs(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    conversation_key TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    rendered_sha256 TEXT,
    budget_chars INTEGER CHECK(budget_chars IS NULL OR budget_chars >= 0),
    used_chars INTEGER CHECK(used_chars IS NULL OR used_chars >= 0),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_reply_context_manifests_run
ON reply_context_manifests(run_id, revision DESC);

CREATE TABLE IF NOT EXISTS reply_run_leases (
    run_id TEXT PRIMARY KEY REFERENCES reply_runs(id) ON DELETE CASCADE,
    lease_owner TEXT NOT NULL,
    lease_token TEXT NOT NULL UNIQUE,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reply_run_leases_expiry
ON reply_run_leases(expires_at);

CREATE TABLE IF NOT EXISTS reply_delivery_evidence (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES reply_runs(id) ON DELETE CASCADE,
    outbox_id TEXT NOT NULL REFERENCES outbox(id) ON DELETE CASCADE,
    bubble_sequence INTEGER NOT NULL CHECK(bubble_sequence >= 1),
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    idempotency_key TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN (
        'queued', 'sending', 'delivered', 'rejected', 'delivery_unknown'
    )),
    provider_message_id TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT NOT NULL,
    UNIQUE(outbox_id, attempt, outcome)
);

CREATE INDEX IF NOT EXISTS idx_reply_delivery_evidence_run
ON reply_delivery_evidence(run_id, bubble_sequence, attempt);

CREATE TABLE IF NOT EXISTS reply_runtime_state (
    bot_qq TEXT PRIMARY KEY,
    requested_mode TEXT NOT NULL DEFAULT 'observe_only' CHECK(requested_mode IN (
        'observe_only', 'shadow', 'owner_approved', 'limited_auto', 'auto'
    )),
    emergency_paused INTEGER NOT NULL DEFAULT 1 CHECK(emergency_paused IN (0, 1)),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    lifecycle TEXT NOT NULL DEFAULT 'disabled' CHECK(lifecycle IN (
        'disabled', 'idle', 'running', 'paused', 'stopping', 'failed'
    )),
    last_iteration_at TEXT,
    last_claim_at TEXT,
    last_progress_at TEXT,
    last_failure_code TEXT,
    recovered_count INTEGER NOT NULL DEFAULT 0 CHECK(recovered_count >= 0),
    updated_by TEXT NOT NULL DEFAULT 'migration',
    updated_source TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reply_runtime_eligibility (
    bot_qq TEXT NOT NULL,
    conversation_key TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    updated_by TEXT NOT NULL,
    updated_source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(bot_qq, conversation_key)
);

CREATE TABLE IF NOT EXISTS reply_runtime_approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES reply_runs(id) ON DELETE CASCADE,
    plan_hash TEXT NOT NULL,
    runtime_revision INTEGER NOT NULL CHECK(runtime_revision >= 1),
    status TEXT NOT NULL CHECK(status IN (
        'pending', 'approved', 'rejected', 'expired', 'cancelled'
    )),
    requested_by TEXT NOT NULL,
    decided_by TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    UNIQUE(run_id, plan_hash, runtime_revision)
);

CREATE INDEX IF NOT EXISTS idx_reply_runtime_approvals_status_expiry
ON reply_runtime_approvals(status, expires_at);

CREATE TABLE IF NOT EXISTS reply_deferred_triggers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_qq TEXT NOT NULL,
    conversation_key TEXT NOT NULL,
    message_id TEXT NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
    settled_until TEXT NOT NULL,
    policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
    deferred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reply_deferred_conversation
ON reply_deferred_triggers(conversation_key, id);

CREATE TABLE IF NOT EXISTS reply_runtime_events (
    id TEXT PRIMARY KEY,
    bot_qq TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    prior_revision INTEGER,
    resulting_revision INTEGER,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reply_runtime_events_bot_time
ON reply_runtime_events(bot_qq, created_at DESC);

CREATE TABLE IF NOT EXISTS readiness_decisions (
    id TEXT PRIMARY KEY,
    bot_qq TEXT NOT NULL REFERENCES bots(qq) ON DELETE CASCADE,
    process_instance_id TEXT NOT NULL,
    profile TEXT NOT NULL CHECK(profile IN (
        'local_start', 'offline_shadow', 'controlled_real_effect'
    )),
    capability_scope TEXT NOT NULL,
    scope_hash TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    status TEXT NOT NULL CHECK(status IN ('passed', 'blocked')),
    blockers_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    correlation_id TEXT NOT NULL DEFAULT '',
    evaluated_at TEXT NOT NULL,
    UNIQUE(bot_qq, process_instance_id, profile, capability_scope, revision)
);

CREATE INDEX IF NOT EXISTS idx_readiness_decisions_current
ON readiness_decisions(bot_qq, process_instance_id, profile, capability_scope, revision DESC);

CREATE TABLE IF NOT EXISTS readiness_probe_evidence (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES readiness_decisions(id) ON DELETE CASCADE,
    probe_code TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pass', 'warning', 'blocked', 'unknown', 'stale')),
    capability_scope TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    source TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    safe_detail TEXT NOT NULL DEFAULT '',
    remediation_code TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(decision_id, probe_code, capability_scope)
);

CREATE INDEX IF NOT EXISTS idx_readiness_probe_expiry
ON readiness_probe_evidence(expires_at, status);

CREATE TABLE IF NOT EXISTS managed_artifacts (
    id TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL CHECK(artifact_type IN (
        'migration_backup', 'privacy_export', 'privacy_deletion_backup',
        'imported_source', 'quarantine_evidence'
    )),
    owner_scope TEXT NOT NULL CHECK(owner_scope IN ('system', 'user')),
    owner_qq TEXT,
    relative_path TEXT NOT NULL,
    bundle_key TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    digest_sha256 TEXT NOT NULL DEFAULT '',
    manifest_type TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    verification_state TEXT NOT NULL DEFAULT 'pending' CHECK(verification_state IN (
        'pending', 'verified', 'invalid', 'missing', 'anomalous'
    )),
    verification_revision INTEGER NOT NULL DEFAULT 1 CHECK(verification_revision >= 1),
    reference_state TEXT NOT NULL DEFAULT 'unknown' CHECK(reference_state IN (
        'unknown', 'unreferenced', 'protected'
    )),
    reference_revision INTEGER NOT NULL DEFAULT 1 CHECK(reference_revision >= 1),
    retention_state TEXT NOT NULL DEFAULT 'retain' CHECK(retention_state IN (
        'retain', 'candidate', 'quarantined', 'blocked'
    )),
    quarantine_batch_id TEXT REFERENCES quarantine_batches(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    updated_at TEXT NOT NULL,
    CHECK(
        (owner_scope = 'system' AND owner_qq IS NULL)
        OR (owner_scope = 'user' AND owner_qq IS NOT NULL)
    ),
    UNIQUE(artifact_type, owner_scope, owner_qq, relative_path)
);

CREATE INDEX IF NOT EXISTS idx_managed_artifacts_scope
ON managed_artifacts(owner_scope, owner_qq, artifact_type, retention_state);

CREATE UNIQUE INDEX IF NOT EXISTS idx_managed_artifacts_system_path
ON managed_artifacts(artifact_type, relative_path) WHERE owner_scope = 'system';

CREATE UNIQUE INDEX IF NOT EXISTS idx_managed_artifacts_user_path
ON managed_artifacts(artifact_type, owner_qq, relative_path) WHERE owner_scope = 'user';

CREATE TABLE IF NOT EXISTS managed_artifact_references (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES managed_artifacts(id) ON DELETE CASCADE,
    reference_type TEXT NOT NULL,
    reference_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('unknown', 'unreferenced', 'protected')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    observed_at TEXT NOT NULL,
    UNIQUE(artifact_id, reference_type, reference_key)
);

CREATE TABLE IF NOT EXISTS retention_previews (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    actor_id TEXT NOT NULL,
    actor_source TEXT NOT NULL,
    process_instance_id TEXT NOT NULL,
    policy_revision INTEGER NOT NULL CHECK(policy_revision >= 1),
    candidate_ids_json TEXT NOT NULL,
    evidence_revisions_json TEXT NOT NULL,
    total_bytes INTEGER NOT NULL CHECK(total_bytes >= 0),
    target_batch_type TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'ready' CHECK(state IN (
        'ready', 'consumed', 'expired', 'rejected'
    )),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    correlation_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_retention_previews_actor
ON retention_previews(actor_id, process_instance_id, state, expires_at);

CREATE TABLE IF NOT EXISTS quarantine_batches (
    id TEXT PRIMARY KEY,
    preview_id TEXT NOT NULL UNIQUE REFERENCES retention_previews(id) ON DELETE RESTRICT,
    batch_type TEXT NOT NULL,
    owner_scope TEXT NOT NULL CHECK(owner_scope IN ('system', 'user')),
    owner_qq TEXT,
    actor_id TEXT NOT NULL,
    process_instance_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'prepared', 'moving', 'quarantined', 'rollback_required', 'rolled_back', 'blocked'
    )),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    correlation_id TEXT NOT NULL DEFAULT '',
    failure_code TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(
        (owner_scope = 'system' AND owner_qq IS NULL)
        OR (owner_scope = 'user' AND owner_qq IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_quarantine_batches_scope
ON quarantine_batches(owner_scope, owner_qq, state, updated_at DESC);

CREATE TABLE IF NOT EXISTS quarantine_batch_items (
    batch_id TEXT NOT NULL REFERENCES quarantine_batches(id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES managed_artifacts(id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    source_relative_path TEXT NOT NULL,
    quarantine_relative_path TEXT NOT NULL,
    expected_digest_sha256 TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN (
        'pending', 'moved', 'restored', 'blocked'
    )),
    error_code TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(batch_id, artifact_id),
    UNIQUE(batch_id, sequence)
);

CREATE TABLE IF NOT EXISTS qualification_route_revisions (
    fingerprint TEXT PRIMARY KEY,
    capability TEXT NOT NULL CHECK(capability IN ('chat', 'vision', 'image', 'stats')),
    provider_protocol TEXT NOT NULL,
    sanitized_base_host TEXT NOT NULL,
    model_identifier TEXT NOT NULL,
    generation_settings_json TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    price_catalog_revision TEXT NOT NULL,
    protection_policy_revision TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qualification_previews (
    id TEXT PRIMARY KEY,
    confirmation_handle_hash TEXT NOT NULL UNIQUE,
    actor_id TEXT NOT NULL,
    process_instance_id TEXT NOT NULL,
    capability TEXT NOT NULL CHECK(capability IN ('chat', 'vision', 'image', 'stats')),
    route_fingerprint TEXT NOT NULL REFERENCES qualification_route_revisions(fingerprint)
        ON DELETE RESTRICT,
    suite_id TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    suite_snapshot_json TEXT NOT NULL,
    policy_revision TEXT NOT NULL,
    fixture_ids_json TEXT NOT NULL,
    max_requests INTEGER NOT NULL CHECK(max_requests >= 0),
    max_input_tokens INTEGER,
    max_output_tokens INTEGER,
    max_images INTEGER,
    conservative_max_cost TEXT,
    execution_mode TEXT NOT NULL CHECK(execution_mode IN ('fake', 'controlled_live')),
    state TEXT NOT NULL DEFAULT 'ready' CHECK(state IN (
        'ready', 'consumed', 'expired', 'rejected', 'cancelled'
    )),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    correlation_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_qualification_previews_actor
ON qualification_previews(actor_id, process_instance_id, capability, state, expires_at);

CREATE TABLE IF NOT EXISTS qualification_runs (
    id TEXT PRIMARY KEY,
    preview_id TEXT NOT NULL REFERENCES qualification_previews(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL,
    process_instance_id TEXT NOT NULL,
    capability TEXT NOT NULL CHECK(capability IN ('chat', 'vision', 'image', 'stats')),
    route_fingerprint TEXT NOT NULL REFERENCES qualification_route_revisions(fingerprint)
        ON DELETE RESTRICT,
    suite_id TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    suite_snapshot_json TEXT NOT NULL,
    execution_mode TEXT NOT NULL CHECK(execution_mode IN ('fake', 'controlled_live')),
    state TEXT NOT NULL CHECK(state IN (
        'prepared', 'running', 'passed', 'failed', 'inconclusive', 'blocked', 'cancelled'
    )),
    idempotency_key TEXT NOT NULL UNIQUE,
    lease_token TEXT NOT NULL DEFAULT '',
    lease_expires_at TEXT,
    correlation_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_qualification_runs_active_live
ON qualification_runs(capability)
WHERE execution_mode = 'controlled_live' AND state IN ('prepared', 'running');

CREATE TABLE IF NOT EXISTS qualification_cases (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES qualification_runs(id) ON DELETE CASCADE,
    fixture_id TEXT NOT NULL,
    fixture_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'pending', 'running', 'passed', 'failed', 'blocked', 'cancelled', 'inconclusive'
    )),
    idempotency_key TEXT NOT NULL UNIQUE,
    lease_token TEXT NOT NULL DEFAULT '',
    lease_expires_at TEXT,
    reason_code TEXT,
    latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    image_count INTEGER NOT NULL DEFAULT 0,
    provider_request_id TEXT,
    response_hash TEXT,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(run_id, fixture_id)
);

CREATE TABLE IF NOT EXISTS qualification_check_results (
    case_id TEXT NOT NULL REFERENCES qualification_cases(id) ON DELETE CASCADE,
    check_code TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('blocking', 'advisory')),
    passed INTEGER,
    score TEXT,
    reason_code TEXT,
    PRIMARY KEY(case_id, check_code)
);

CREATE TABLE IF NOT EXISTS qualification_cost_evidence (
    run_id TEXT PRIMARY KEY REFERENCES qualification_runs(id) ON DELETE CASCADE,
    price_catalog_revision TEXT NOT NULL,
    planned_max_cost TEXT,
    observed_cost TEXT,
    currency TEXT,
    request_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    image_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qualification_artifacts (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL UNIQUE REFERENCES qualification_cases(id) ON DELETE RESTRICT,
    relative_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    pixel_width INTEGER,
    pixel_height INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qualification_decisions (
    id TEXT PRIMARY KEY,
    capability TEXT NOT NULL CHECK(capability IN ('chat', 'vision', 'image', 'stats')),
    route_fingerprint TEXT NOT NULL REFERENCES qualification_route_revisions(fingerprint)
        ON DELETE RESTRICT,
    suite_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'unqualified', 'passed', 'failed', 'stale', 'blocked'
    )),
    run_id TEXT REFERENCES qualification_runs(id) ON DELETE RESTRICT,
    advisory_score TEXT,
    blocker_codes_json TEXT NOT NULL DEFAULT '[]',
    evaluated_at TEXT NOT NULL,
    UNIQUE(capability, route_fingerprint, suite_version)
);

CREATE TRIGGER IF NOT EXISTS trg_control_command_privacy_links_insert
AFTER INSERT ON control_commands
BEGIN
    INSERT OR IGNORE INTO privacy_subject_links(
        subject_user_qq, record_type, record_id, source_path, created_at
    )
    SELECT CAST(tree.atom AS TEXT), 'control_command', NEW.id, tree.fullkey, NEW.created_at
    FROM json_tree(NEW.arguments_json) AS tree
    WHERE tree.key IN ('user_qq', 'target_qq', 'subject_user_qq')
      AND tree.type IN ('text', 'integer')
      AND length(CAST(tree.atom AS TEXT)) BETWEEN 5 AND 15
      AND CAST(tree.atom AS TEXT) NOT GLOB '*[^0-9]*';
END;

CREATE TRIGGER IF NOT EXISTS trg_control_command_privacy_links_delete
AFTER DELETE ON control_commands
BEGIN
    DELETE FROM privacy_subject_links
    WHERE record_type = 'control_command' AND record_id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_approval_payload_privacy_links_insert
AFTER INSERT ON approval_payloads
BEGIN
    INSERT OR IGNORE INTO privacy_subject_links(
        subject_user_qq, record_type, record_id, source_path, created_at
    )
    SELECT CAST(tree.atom AS TEXT), 'approval_request', NEW.approval_id,
           tree.fullkey, COALESCE(
               (SELECT created_at FROM approval_requests WHERE id = NEW.approval_id),
               CURRENT_TIMESTAMP
           )
    FROM json_tree(NEW.payload_json) AS tree
    WHERE tree.key IN ('user_qq', 'target_qq', 'subject_user_qq')
      AND tree.type IN ('text', 'integer')
      AND length(CAST(tree.atom AS TEXT)) BETWEEN 5 AND 15
      AND CAST(tree.atom AS TEXT) NOT GLOB '*[^0-9]*';
END;

CREATE TRIGGER IF NOT EXISTS trg_approval_payload_privacy_links_delete
AFTER DELETE ON approval_payloads
BEGIN
    DELETE FROM privacy_subject_links
    WHERE record_type = 'approval_request' AND record_id = OLD.approval_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_privacy_links_insert
AFTER INSERT ON audit_log
BEGIN
    INSERT OR IGNORE INTO privacy_subject_links(
        subject_user_qq, record_type, record_id, source_path, created_at
    )
    SELECT CAST(tree.atom AS TEXT), 'audit_log', CAST(NEW.id AS TEXT),
           tree.fullkey, NEW.created_at
    FROM json_tree(NEW.details_json) AS tree
    WHERE tree.key IN ('user_qq', 'target_qq', 'subject_user_qq')
      AND tree.type IN ('text', 'integer')
      AND length(CAST(tree.atom AS TEXT)) BETWEEN 5 AND 15
      AND CAST(tree.atom AS TEXT) NOT GLOB '*[^0-9]*';
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_privacy_links_delete
AFTER DELETE ON audit_log
BEGIN
    DELETE FROM privacy_subject_links
    WHERE record_type = 'audit_log' AND record_id = CAST(OLD.id AS TEXT);
END;
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _candidate_dict(row: Any) -> dict[str, Any]:
    payload = dict(row)
    payload["safety_flags"] = json.loads(row["safety_flags_json"])
    raw_bubbles = payload.get("bubbles_json")
    payload["bubbles"] = json.loads(raw_bubbles) if raw_bubbles else []
    return payload


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _resolve_outbound_bot_qq(
    connection: sqlite3.Connection,
    kind: str,
    peer_id: str,
    default_bot_qq: str,
) -> str:
    row = connection.execute(
        """
        SELECT conversation_key FROM conversations
        WHERE kind = ? AND peer_id = ?
        ORDER BY last_activity_at DESC
        LIMIT 1
        """,
        (kind, peer_id),
    ).fetchone()
    if row:
        parts = str(row["conversation_key"]).split(":", 2)
        if len(parts) == 3 and parts[0]:
            return parts[0]
    bot = connection.execute(
        "SELECT qq FROM bots WHERE enabled = 1 ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    if bot:
        return str(bot["qq"])
    bot = connection.execute("SELECT qq FROM bots ORDER BY created_at ASC LIMIT 1").fetchone()
    if bot:
        return str(bot["qq"])
    return default_bot_qq.strip()


def _mirror_outbound_message(
    connection: sqlite3.Connection,
    outbox: sqlite3.Row,
    default_bot_qq: str,
    now: str,
) -> None:
    kind = str(outbox["conversation_kind"])
    peer_id = str(outbox["target_id"])
    bot_qq = _resolve_outbound_bot_qq(connection, kind, peer_id, default_bot_qq)
    if not bot_qq:
        return
    event_key = f"outbox:{bot_qq}:{outbox['id']}"
    existing = connection.execute(
        "SELECT 1 FROM messages WHERE event_key = ?",
        (event_key,),
    ).fetchone()
    if existing is not None:
        return
    conversation_key = f"{bot_qq}:{kind}:{peer_id}"
    occurred_at = outbox["sent_at"] or now
    segments_raw = outbox["segments_json"]
    try:
        segments = json.loads(segments_raw)
    except json.JSONDecodeError:
        segments = []
    plain_text = "".join(
        str(item.get("data", {}).get("text", ""))
        for item in segments
        if isinstance(item, dict) and item.get("type") == "text"
    ).strip()
    message_id = str(uuid5(NAMESPACE_URL, event_key))
    connection.execute(
        """
        INSERT OR IGNORE INTO inbound_events(
            event_key, bot_qq, post_type, payload_json, received_at
        ) VALUES(?, ?, 'outbound', ?, ?)
        """,
        (event_key, bot_qq, _json({"outbox_id": outbox["id"]}), now),
    )
    connection.execute(
        """
        INSERT INTO conversations(
            conversation_key, kind, peer_id, created_at, last_activity_at
        ) VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(conversation_key) DO UPDATE SET
            last_activity_at = excluded.last_activity_at
        """,
        (conversation_key, kind, peer_id, occurred_at, occurred_at),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO messages(
            id, event_key, source_message_id, bot_qq, direction,
            conversation_key, sender_id, occurred_at, plain_text,
            segments_json, raw_json, reply_to_message_id, created_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        (
            message_id,
            event_key,
            str(outbox["id"]),
            bot_qq,
            MessageDirection.OUTBOUND.value,
            conversation_key,
            bot_qq,
            occurred_at,
            plain_text,
            segments_raw,
            _json({"outbox_id": outbox["id"]}),
            now,
        ),
    )


class SQLiteRepository(
    AccountRepositoryMixin,
    ContentRepositoryMixin,
    StatsRepositoryMixin,
    QzoneRepositoryMixin,
    OwnerReportRepositoryMixin,
    QzoneProfileRepositoryMixin,
    ReplyPipelineRepositoryMixin,
    ReplyRuntimeRepositoryMixin,
    OperationalReadinessRepositoryMixin,
    QualificationRepositoryMixin,
):
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(f"BEGIN IMMEDIATE;\n{SCHEMA}\nCOMMIT;")
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(2, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(3, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(4, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(5, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(6, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(7, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(8, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(9, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(10, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(11, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(12, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(13, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(14, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(15, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(16, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(17, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(18, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(19, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(20, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(21, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(22, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(23, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(24, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(25, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(26, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(27, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(28, ?)",
                (_utc_now(),),
            )
            self._migrate_reply_runs_v29(connection)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(29, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(30, ?)",
                (_utc_now(),),
            )
            privacy_artifact_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(privacy_job_artifacts)").fetchall()
            }
            if "owner_qq" not in privacy_artifact_columns:
                connection.execute(
                    "ALTER TABLE privacy_job_artifacts ADD COLUMN owner_qq TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    """
                    UPDATE privacy_job_artifacts
                    SET owner_qq = COALESCE((
                        SELECT privacy_requests.user_qq
                        FROM privacy_requests
                        WHERE privacy_requests.id = privacy_job_artifacts.request_id
                          AND privacy_requests.user_qq NOT GLOB '*[^0-9]*'
                    ), '')
                    """
                )
            self._migrate_managed_artifacts_v31(connection)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(31, ?)",
                (_utc_now(),),
            )
            self._migrate_quarantine_batches_v32(connection)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(32, ?)",
                (_utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(33, ?)",
                (_utc_now(),),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operator_labels (
                    subject_kind TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    PRIMARY KEY (subject_kind, subject_id)
                )
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(34, ?)",
                (_utc_now(),),
            )
            now = _utc_now()
            connection.execute(
                """
                INSERT OR IGNORE INTO reply_runtime_state(
                    bot_qq, requested_mode, emergency_paused, revision, lifecycle,
                    updated_by, updated_source, created_at, updated_at
                )
                SELECT qq, 'observe_only', 1, 1, 'disabled',
                       'migration', 'system', ?, ?
                FROM bots
                """,
                (now, now),
            )
            self._backfill_privacy_subject_links(connection)
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(inference_runs)").fetchall()
            }
            if "bot_qq" not in columns:
                connection.execute(
                    "ALTER TABLE inference_runs ADD COLUMN bot_qq TEXT NOT NULL DEFAULT ''"
                )
            candidate_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(reply_candidates)").fetchall()
            }
            if "bubbles_json" not in candidate_columns:
                connection.execute("ALTER TABLE reply_candidates ADD COLUMN bubbles_json TEXT")
            policy_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(proactive_user_policies)"
                ).fetchall()
            }
            if "send_diary" not in policy_columns:
                connection.execute(
                    "ALTER TABLE proactive_user_policies "
                    "ADD COLUMN send_diary INTEGER NOT NULL DEFAULT 0"
                )
            if "auto_content_enabled" not in policy_columns:
                connection.execute(
                    "ALTER TABLE proactive_user_policies "
                    "ADD COLUMN auto_content_enabled INTEGER NOT NULL DEFAULT 0"
                )
            summary_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(daily_summaries)").fetchall()
            }
            if "source_peer_ids_json" not in summary_columns:
                connection.execute(
                    "ALTER TABLE daily_summaries "
                    "ADD COLUMN source_peer_ids_json TEXT NOT NULL DEFAULT '[]'"
                )
            task_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(proactive_message_tasks)"
                ).fetchall()
            }
            if "content_source" not in task_columns:
                connection.execute(
                    "ALTER TABLE proactive_message_tasks "
                    "ADD COLUMN content_source TEXT NOT NULL DEFAULT 'manual'"
                )

    def _migrate_managed_artifacts_v31(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'managed_artifacts'"
        ).fetchone()
        legacy_owner = "owner_qq TEXT REFERENCES users(qq_id) ON DELETE RESTRICT"
        if row is None or legacy_owner not in str(row["sql"]):
            return
        create_sql = str(row["sql"]).replace(legacy_owner, "owner_qq TEXT", 1)
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA legacy_alter_table = ON")
        try:
            connection.executescript(
                f"""
                BEGIN IMMEDIATE;
                DROP INDEX IF EXISTS idx_managed_artifacts_scope;
                DROP INDEX IF EXISTS idx_managed_artifacts_system_path;
                DROP INDEX IF EXISTS idx_managed_artifacts_user_path;
                ALTER TABLE managed_artifacts RENAME TO managed_artifacts_v30;
                {create_sql};
                INSERT INTO managed_artifacts SELECT * FROM managed_artifacts_v30;
                DROP TABLE managed_artifacts_v30;
                CREATE INDEX idx_managed_artifacts_scope
                    ON managed_artifacts(
                        owner_scope, owner_qq, artifact_type, retention_state
                    );
                CREATE UNIQUE INDEX idx_managed_artifacts_system_path
                    ON managed_artifacts(artifact_type, relative_path)
                    WHERE owner_scope = 'system';
                CREATE UNIQUE INDEX idx_managed_artifacts_user_path
                    ON managed_artifacts(artifact_type, owner_qq, relative_path)
                    WHERE owner_scope = 'user';
                COMMIT;
                """
            )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(
                    "managed artifact ownership migration failed foreign-key check"
                )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA legacy_alter_table = OFF")
            connection.execute("PRAGMA foreign_keys = ON")

    def _migrate_quarantine_batches_v32(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'quarantine_batches'"
        ).fetchone()
        legacy_owner = "owner_qq TEXT REFERENCES users(qq_id) ON DELETE RESTRICT"
        if row is None or legacy_owner not in str(row["sql"]):
            return
        create_sql = str(row["sql"]).replace(legacy_owner, "owner_qq TEXT", 1)
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA legacy_alter_table = ON")
        try:
            connection.executescript(
                f"""
                BEGIN IMMEDIATE;
                DROP INDEX IF EXISTS idx_quarantine_batches_scope;
                ALTER TABLE quarantine_batches RENAME TO quarantine_batches_v31;
                {create_sql};
                INSERT INTO quarantine_batches SELECT * FROM quarantine_batches_v31;
                DROP TABLE quarantine_batches_v31;
                CREATE INDEX idx_quarantine_batches_scope
                    ON quarantine_batches(owner_scope, owner_qq, state, updated_at DESC);
                COMMIT;
                """
            )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(
                    "quarantine ownership migration failed foreign-key check"
                )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA legacy_alter_table = OFF")
            connection.execute("PRAGMA foreign_keys = ON")

    def _migrate_reply_runs_v29(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'reply_runs'"
        ).fetchone()
        if row is None or "shadow_completed" in str(row["sql"]):
            return
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA legacy_alter_table = ON")
        try:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                DROP INDEX IF EXISTS idx_reply_runs_one_active_conversation;
                DROP INDEX IF EXISTS idx_reply_runs_stage_updated;
                DROP INDEX IF EXISTS idx_reply_runs_conversation_created;
                DROP INDEX IF EXISTS idx_reply_runs_subject_created;
                ALTER TABLE reply_runs RENAME TO reply_runs_v28;
                CREATE TABLE reply_runs (
                    id TEXT PRIMARY KEY,
                    bot_qq TEXT NOT NULL,
                    conversation_key TEXT NOT NULL
                        REFERENCES conversations(conversation_key) ON DELETE CASCADE,
                    conversation_kind TEXT NOT NULL
                        CHECK(conversation_kind IN ('private', 'group')),
                    subject_user_qq TEXT,
                    stage TEXT NOT NULL CHECK(stage IN (
                        'pending', 'settling', 'assembling_context', 'calling_model',
                        'planning_reply', 'creating_outbox', 'awaiting_delivery',
                        'awaiting_approval', 'shadow_completed',
                        'completed', 'suppressed', 'failed', 'cancelled'
                    )),
                    policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
                    settled_until TEXT,
                    reply_plan_json TEXT,
                    failure_code TEXT,
                    failure_category TEXT,
                    failure_detail TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    CHECK(
                        (conversation_kind = 'private' AND subject_user_qq IS NOT NULL)
                        OR (conversation_kind = 'group' AND subject_user_qq IS NULL)
                    )
                );
                INSERT INTO reply_runs SELECT * FROM reply_runs_v28;
                DROP TABLE reply_runs_v28;
                CREATE INDEX idx_reply_runs_stage_updated ON reply_runs(stage, updated_at);
                CREATE INDEX idx_reply_runs_conversation_created
                    ON reply_runs(conversation_key, created_at DESC);
                CREATE INDEX idx_reply_runs_subject_created
                    ON reply_runs(subject_user_qq, created_at DESC)
                    WHERE subject_user_qq IS NOT NULL;
                CREATE UNIQUE INDEX idx_reply_runs_one_active_conversation
                    ON reply_runs(conversation_key)
                    WHERE stage NOT IN (
                        'shadow_completed', 'completed', 'suppressed', 'failed', 'cancelled'
                    );
                """
            )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError("reply runtime migration failed foreign-key check")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA legacy_alter_table = OFF")
            connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _backfill_privacy_subject_links(connection: sqlite3.Connection) -> None:
        statements = (
            (
                "control_command",
                "control_commands",
                "id",
                "arguments_json",
                "created_at",
            ),
            (
                "approval_request",
                "approval_payloads",
                "approval_id",
                "payload_json",
                None,
            ),
            (
                "audit_log",
                "audit_log",
                "id",
                "details_json",
                "created_at",
            ),
        )
        for record_type, table, id_column, json_column, created_column in statements:
            created_expression = (
                f"source.{created_column}"
                if created_column
                else "COALESCE((SELECT created_at FROM approval_requests "
                "WHERE id = source.approval_id), CURRENT_TIMESTAMP)"
            )
            connection.execute(
                f"""
                INSERT OR IGNORE INTO privacy_subject_links(
                    subject_user_qq, record_type, record_id, source_path, created_at
                )
                SELECT CAST(tree.atom AS TEXT), ?, CAST(source.{id_column} AS TEXT),
                       tree.fullkey, {created_expression}
                FROM {table} AS source, json_tree(source.{json_column}) AS tree
                WHERE tree.key IN ('user_qq', 'target_qq', 'subject_user_qq')
                  AND tree.type IN ('text', 'integer')
                  AND length(CAST(tree.atom AS TEXT)) BETWEEN 5 AND 15
                  AND CAST(tree.atom AS TEXT) NOT GLOB '*[^0-9]*'
                """,
                (record_type,),
            )

    async def create_admin_session(
        self,
        *,
        session_id: str,
        token_hash: str,
        expires_at: str,
        created_from: str,
    ) -> None:
        await asyncio.to_thread(
            self._create_admin_session_sync,
            session_id,
            token_hash,
            expires_at,
            created_from,
        )

    def _create_admin_session_sync(
        self,
        session_id: str,
        token_hash: str,
        expires_at: str,
        created_from: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO admin_sessions(
                    id, token_hash, created_at, expires_at, created_from
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (session_id, token_hash, _utc_now(), expires_at, created_from),
            )

    async def validate_admin_session(self, token_hash: str) -> bool:
        return await asyncio.to_thread(self._validate_admin_session_sync, token_hash)

    def _validate_admin_session_sync(self, token_hash: str) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE admin_sessions SET last_used_at = ?
                WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?
                """,
                (now, token_hash, now),
            )
            return result.rowcount == 1

    async def revoke_admin_session(self, token_hash: str) -> bool:
        return await asyncio.to_thread(self._revoke_admin_session_sync, token_hash)

    def _revoke_admin_session_sync(self, token_hash: str) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE admin_sessions SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at IS NULL
                """,
                (_utc_now(), token_hash),
            )
            return result.rowcount == 1

    async def revoke_all_admin_sessions(self) -> int:
        return await asyncio.to_thread(self._revoke_all_admin_sessions_sync)

    def _revoke_all_admin_sessions_sync(self) -> int:
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE admin_sessions SET revoked_at = ? WHERE revoked_at IS NULL",
                (_utc_now(),),
            )
            return result.rowcount

    async def start_inference_run(
        self,
        *,
        run_id: str,
        source_message_id: str,
        conversation_key: str,
        actor_qq: str,
        mode: str,
        prompt_hash: str,
        model_route: str,
        bot_qq: str = "",
    ) -> None:
        await asyncio.to_thread(
            self._start_inference_run_sync,
            run_id,
            source_message_id,
            conversation_key,
            actor_qq,
            mode,
            prompt_hash,
            model_route,
            bot_qq,
        )

    def _start_inference_run_sync(
        self,
        run_id: str,
        source_message_id: str,
        conversation_key: str,
        actor_qq: str,
        mode: str,
        prompt_hash: str,
        model_route: str,
        bot_qq: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO inference_runs(
                    id, source_message_id, conversation_key, actor_qq, bot_qq,
                    mode, status, prompt_hash, model_route, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)
                """,
                (
                    run_id,
                    source_message_id,
                    conversation_key,
                    actor_qq,
                    bot_qq,
                    mode,
                    prompt_hash,
                    model_route,
                    _utc_now(),
                ),
            )

    async def finish_inference_success(
        self,
        *,
        run_id: str,
        candidate_id: str,
        source_message_id: str,
        conversation_kind: str,
        target_id: str,
        content: str,
        provider_request_id: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        safety_flags: tuple[str, ...],
        bubbles: tuple[str, ...] = (),
    ) -> None:
        await asyncio.to_thread(
            self._finish_inference_success_sync,
            run_id,
            candidate_id,
            source_message_id,
            conversation_kind,
            target_id,
            content,
            provider_request_id,
            input_tokens,
            output_tokens,
            safety_flags,
            bubbles,
        )

    def _finish_inference_success_sync(
        self,
        run_id: str,
        candidate_id: str,
        source_message_id: str,
        conversation_kind: str,
        target_id: str,
        content: str,
        provider_request_id: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        safety_flags: tuple[str, ...],
        bubbles: tuple[str, ...] = (),
    ) -> None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE inference_runs SET status = 'completed',
                    provider_request_id = ?, input_tokens = ?, output_tokens = ?,
                    completed_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (provider_request_id, input_tokens, output_tokens, now, run_id),
            )
            connection.execute(
                """
                INSERT INTO reply_candidates(
                    id, inference_run_id, source_message_id,
                    conversation_kind, target_id, content,
                    status, safety_flags_json, bubbles_json, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, 'shadow', ?, ?, ?)
                """,
                (
                    candidate_id,
                    run_id,
                    source_message_id,
                    conversation_kind,
                    target_id,
                    content,
                    _json(safety_flags),
                    _json(bubbles) if bubbles else None,
                    now,
                ),
            )
            connection.commit()

    async def finish_inference_failure(
        self,
        run_id: str,
        *,
        error_type: str,
        error_message: str,
    ) -> None:
        await asyncio.to_thread(
            self._finish_inference_failure_sync,
            run_id,
            error_type,
            error_message,
        )

    def _finish_inference_failure_sync(
        self,
        run_id: str,
        error_type: str,
        error_message: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE inference_runs SET status = 'failed', error_type = ?,
                    error_message = ?, completed_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (error_type, error_message[:1000], _utc_now(), run_id),
            )

    async def reply_candidates(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._reply_candidates_sync, status)

    def _reply_candidates_sync(self, status: str | None) -> list[dict[str, Any]]:
        query = """
            SELECT reply_candidates.*, inference_runs.prompt_hash,
                   inference_runs.model_route, inference_runs.status AS inference_status,
                   inference_runs.input_tokens, inference_runs.output_tokens
            FROM reply_candidates
            JOIN inference_runs ON inference_runs.id = reply_candidates.inference_run_id
        """
        parameters: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE reply_candidates.status = ?"
            parameters = (status,)
        query += " ORDER BY reply_candidates.created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_candidate_dict(row) for row in rows]

    async def inference_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._inference_runs_sync, limit)

    def _inference_runs_sync(self, limit: int) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 500))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM inference_runs ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    async def inference_run(self, run_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._inference_run_sync, run_id)

    def _inference_run_sync(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM inference_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                return None
            candidate = connection.execute(
                "SELECT * FROM reply_candidates WHERE inference_run_id = ?",
                (run_id,),
            ).fetchone()
        result = dict(run)
        if candidate is None:
            result["candidate"] = None
            return result
        result["candidate"] = _candidate_dict(candidate)
        return result

    async def inbound_message(self, message_id: str) -> UnifiedMessage | None:
        return await asyncio.to_thread(self._inbound_message_sync, message_id)

    def _inbound_message_sync(self, message_id: str) -> UnifiedMessage | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT messages.*, conversations.kind, conversations.peer_id
                FROM messages
                JOIN conversations
                  ON conversations.conversation_key = messages.conversation_key
                WHERE messages.id = ? OR messages.source_message_id = ?
                ORDER BY messages.created_at DESC
                LIMIT 1
                """,
                (message_id, message_id),
            ).fetchone()
        if row is None:
            return None
        raw = json.loads(row["raw_json"])
        segments_data = json.loads(row["segments_json"])
        segments = tuple(
            MessageSegment(type=str(item.get("type", "unknown")), data=dict(item.get("data") or {}))
            for item in segments_data
            if isinstance(item, dict)
        )
        occurred_at = datetime.fromisoformat(row["occurred_at"])
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        return UnifiedMessage(
            id=row["id"],
            event_key=row["event_key"],
            source_message_id=row["source_message_id"],
            bot_qq=row["bot_qq"],
            direction=MessageDirection(row["direction"]),
            conversation_kind=ConversationKind(row["kind"]),
            conversation_id=str(row["peer_id"]),
            sender_id=row["sender_id"],
            occurred_at=occurred_at,
            segments=segments,
            raw_event=raw if isinstance(raw, dict) else {},
            reply_to_message_id=row["reply_to_message_id"],
        )

    async def recent_inbound_image_urls(
        self,
        *,
        conversation_key: str,
        sender_id: str,
        since: datetime,
        limit: int = 50,
    ) -> tuple[str, ...]:
        pairs = await self.recent_inbound_image_pairs(
            conversation_key=conversation_key,
            sender_id=sender_id,
            since=since,
            limit=limit,
        )
        return tuple(url for url, _file_id in pairs if url)

    async def recent_inbound_image_pairs(
        self,
        *,
        conversation_key: str,
        sender_id: str,
        since: datetime,
        limit: int = 50,
    ) -> tuple[tuple[str, str], ...]:
        return await asyncio.to_thread(
            self._recent_inbound_image_pairs_sync,
            conversation_key,
            sender_id,
            since,
            limit,
        )

    def _recent_inbound_image_pairs_sync(
        self,
        conversation_key: str,
        sender_id: str,
        since: datetime,
        limit: int,
    ) -> tuple[tuple[str, str], ...]:
        since_iso = since.astimezone(UTC).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT segments_json
                FROM messages
                WHERE conversation_key = ?
                  AND sender_id = ?
                  AND direction = 'inbound'
                  AND occurred_at >= ?
                ORDER BY occurred_at DESC
                LIMIT ?
                """,
                (conversation_key, sender_id, since_iso, max(1, min(limit, 50))),
            ).fetchall()
        for row in rows:
            for item in json.loads(row["segments_json"]):
                if not isinstance(item, dict) or item.get("type") != "image":
                    continue
                data = item.get("data") if isinstance(item.get("data"), dict) else {}
                url = safe_model_image_url(data.get("url")) or safe_model_image_url(
                    data.get("file")
                )
                file_id = safe_napcat_image_file_id(data.get("file"))
                if url or file_id:
                    return ((url or "", file_id or ""),)
        return ()

    async def reserve_model_call(
        self,
        *,
        route: str,
        request_id: str,
        day_key: str,
        now: datetime,
        lease_expires_at: datetime,
        daily_request_limit: int,
        daily_token_limit: int | None,
        reserved_tokens: int,
        failure_threshold: int,
        cooldown_seconds: int,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._reserve_model_call_sync,
            route,
            request_id,
            day_key,
            now,
            lease_expires_at,
            daily_request_limit,
            daily_token_limit,
            reserved_tokens,
            failure_threshold,
            cooldown_seconds,
        )

    def _reserve_model_call_sync(
        self,
        route: str,
        request_id: str,
        day_key: str,
        now: datetime,
        lease_expires_at: datetime,
        daily_request_limit: int,
        daily_token_limit: int | None,
        reserved_tokens: int,
        failure_threshold: int,
        cooldown_seconds: int,
    ) -> dict[str, Any]:
        now_iso = now.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_stale_model_calls_sync(connection, route=route, now_iso=now_iso)
            existing = connection.execute(
                "SELECT status FROM model_call_events WHERE route = ? AND request_id = ?",
                (route, request_id),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return {"allowed": False, "reason": "duplicate_request_id"}

            circuit = self._model_circuit_snapshot_sync(
                connection,
                route=route,
                now=now,
                failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds,
            )
            if circuit["state"] in {"open", "half_open"}:
                reason = "circuit_open" if circuit["state"] == "open" else "circuit_half_open"
                self._insert_model_rejection_sync(
                    connection,
                    route=route,
                    request_id=request_id,
                    day_key=day_key,
                    reserved_tokens=reserved_tokens,
                    reason=reason,
                    now_iso=now_iso,
                )
                connection.commit()
                return {
                    "allowed": False,
                    "reason": reason,
                    "open_until": circuit["open_until"],
                }

            usage = self._model_usage_snapshot_sync(
                connection,
                route=route,
                day_key=day_key,
            )
            if usage["requests_used"] >= daily_request_limit:
                self._insert_model_rejection_sync(
                    connection,
                    route=route,
                    request_id=request_id,
                    day_key=day_key,
                    reserved_tokens=reserved_tokens,
                    reason="daily_request_budget",
                    now_iso=now_iso,
                )
                connection.commit()
                return {"allowed": False, "reason": "daily_request_budget", **usage}
            if (
                daily_token_limit is not None
                and usage["tokens_used"] + reserved_tokens > daily_token_limit
            ):
                self._insert_model_rejection_sync(
                    connection,
                    route=route,
                    request_id=request_id,
                    day_key=day_key,
                    reserved_tokens=reserved_tokens,
                    reason="daily_token_budget",
                    now_iso=now_iso,
                )
                connection.commit()
                return {"allowed": False, "reason": "daily_token_budget", **usage}

            connection.execute(
                """
                INSERT INTO model_call_events(
                    id, route, request_id, day_key, status, reserved_tokens,
                    lease_expires_at, created_at
                ) VALUES(?, ?, ?, ?, 'running', ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    route,
                    request_id,
                    day_key,
                    reserved_tokens,
                    lease_expires_at.isoformat(),
                    now_iso,
                ),
            )
            connection.commit()
            return {"allowed": True, "reason": None, **usage}

    @staticmethod
    def _insert_model_rejection_sync(
        connection: sqlite3.Connection,
        *,
        route: str,
        request_id: str,
        day_key: str,
        reserved_tokens: int,
        reason: str,
        now_iso: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO model_call_events(
                id, route, request_id, day_key, status, reserved_tokens,
                rejection_reason, created_at, completed_at
            ) VALUES(?, ?, ?, ?, 'rejected', ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                route,
                request_id,
                day_key,
                reserved_tokens,
                reason,
                now_iso,
                now_iso,
            ),
        )

    async def finish_model_call_success(
        self,
        *,
        route: str,
        request_id: str,
        input_tokens: int | None,
        output_tokens: int | None,
        completed_at: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._finish_model_call_success_sync,
            route,
            request_id,
            input_tokens,
            output_tokens,
            completed_at,
        )

    def _finish_model_call_success_sync(
        self,
        route: str,
        request_id: str,
        input_tokens: int | None,
        output_tokens: int | None,
        completed_at: datetime,
    ) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE model_call_events
                SET status = 'succeeded', input_tokens = ?, output_tokens = ?,
                    lease_expires_at = NULL, completed_at = ?
                WHERE route = ? AND request_id = ? AND status = 'running'
                """,
                (
                    input_tokens,
                    output_tokens,
                    completed_at.isoformat(),
                    route,
                    request_id,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("model call reservation is not active")

    async def finish_model_call_failure(
        self,
        *,
        route: str,
        request_id: str,
        failure_type: str,
        completed_at: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._finish_model_call_failure_sync,
            route,
            request_id,
            failure_type,
            completed_at,
        )

    def _finish_model_call_failure_sync(
        self,
        route: str,
        request_id: str,
        failure_type: str,
        completed_at: datetime,
    ) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE model_call_events
                SET status = 'failed', failure_type = ?, lease_expires_at = NULL,
                    completed_at = ?
                WHERE route = ? AND request_id = ? AND status = 'running'
                """,
                (failure_type[:200], completed_at.isoformat(), route, request_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("model call reservation is not active")

    async def model_protection_snapshot(
        self,
        *,
        route: str,
        day_key: str,
        now: datetime,
        failure_threshold: int,
        cooldown_seconds: int,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._model_protection_snapshot_sync,
            route,
            day_key,
            now,
            failure_threshold,
            cooldown_seconds,
        )

    def _model_protection_snapshot_sync(
        self,
        route: str,
        day_key: str,
        now: datetime,
        failure_threshold: int,
        cooldown_seconds: int,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_stale_model_calls_sync(
                connection,
                route=route,
                now_iso=now.isoformat(),
            )
            usage = self._model_usage_snapshot_sync(
                connection,
                route=route,
                day_key=day_key,
            )
            circuit = self._model_circuit_snapshot_sync(
                connection,
                route=route,
                now=now,
                failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds,
            )
            rejected = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM model_call_events
                    WHERE route = ? AND day_key = ? AND status = 'rejected'
                    """,
                    (route, day_key),
                ).fetchone()[0]
            )
            connection.commit()
        return {**usage, **circuit, "requests_rejected": rejected}

    @staticmethod
    def _expire_stale_model_calls_sync(
        connection: sqlite3.Connection,
        *,
        route: str,
        now_iso: str,
    ) -> None:
        connection.execute(
            """
            UPDATE model_call_events
            SET status = 'failed', failure_type = 'ModelCallLeaseExpired',
                lease_expires_at = NULL, completed_at = ?
            WHERE route = ? AND status = 'running'
                AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
            """,
            (now_iso, route, now_iso),
        )

    @staticmethod
    def _model_usage_snapshot_sync(
        connection: sqlite3.Connection,
        *,
        route: str,
        day_key: str,
    ) -> dict[str, int]:
        row = connection.execute(
            """
            SELECT COUNT(*) AS requests_used,
                COALESCE(SUM(
                    CASE
                        WHEN COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)
                             > reserved_tokens
                        THEN COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)
                        ELSE reserved_tokens
                    END
                ), 0) AS tokens_used
            FROM model_call_events
            WHERE route = ? AND day_key = ?
                AND status IN ('running', 'succeeded', 'failed')
            """,
            (route, day_key),
        ).fetchone()
        return {
            "requests_used": int(row["requests_used"]),
            "tokens_used": int(row["tokens_used"]),
        }

    @staticmethod
    def _model_circuit_snapshot_sync(
        connection: sqlite3.Connection,
        *,
        route: str,
        now: datetime,
        failure_threshold: int,
        cooldown_seconds: int,
    ) -> dict[str, Any]:
        last_success = connection.execute(
            """
            SELECT MAX(completed_at) FROM model_call_events
            WHERE route = ? AND status = 'succeeded'
            """,
            (route,),
        ).fetchone()[0]
        failures = connection.execute(
            """
            SELECT COUNT(*) AS failure_count, MAX(completed_at) AS last_failure_at
            FROM model_call_events
            WHERE route = ? AND status = 'failed'
                AND (? IS NULL OR completed_at > ?)
            """,
            (route, last_success, last_success),
        ).fetchone()
        consecutive_failures = int(failures["failure_count"])
        last_failure_at = failures["last_failure_at"]
        active_calls = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM model_call_events
                WHERE route = ? AND status = 'running'
                    AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
                """,
                (route, now.isoformat()),
            ).fetchone()[0]
        )
        state = "closed"
        open_until: str | None = None
        if consecutive_failures >= failure_threshold and last_failure_at is not None:
            open_until_at = datetime.fromisoformat(last_failure_at) + timedelta(
                seconds=cooldown_seconds
            )
            open_until = open_until_at.isoformat()
            if now < open_until_at:
                state = "open"
            elif active_calls:
                state = "half_open"
            else:
                state = "half_open_ready"
        return {
            "state": state,
            "consecutive_failures": consecutive_failures,
            "last_failure_at": last_failure_at,
            "open_until": open_until,
            "active_calls": active_calls,
        }

    async def model_call_events(
        self,
        *,
        route: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._model_call_events_sync, route, limit)

    def _model_call_events_sync(
        self,
        route: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 500))
        query = "SELECT * FROM model_call_events"
        parameters: tuple[Any, ...]
        if route is None:
            parameters = (safe_limit,)
        else:
            query += " WHERE route = ?"
            parameters = (route, safe_limit)
        query += " ORDER BY created_at DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    async def create_image_task(
        self,
        *,
        task_id: str,
        prompt: str,
        intended_use: str,
        status: ImageTaskStatus,
        requested_by: str,
        request_source: str,
        model_route: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._create_image_task_sync,
            task_id,
            prompt,
            intended_use,
            status,
            requested_by,
            request_source,
            model_route,
        )

    def _create_image_task_sync(
        self,
        task_id: str,
        prompt: str,
        intended_use: str,
        status: ImageTaskStatus,
        requested_by: str,
        request_source: str,
        model_route: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO image_generation_tasks(
                    id, prompt, intended_use, status, requested_by,
                    request_source, model_route, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    prompt,
                    intended_use,
                    status.value,
                    requested_by,
                    request_source,
                    model_route,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('image.task_created', 'image_task', ?, ?, ?)
                """,
                (
                    task_id,
                    _json(
                        {
                            "intended_use": intended_use,
                            "requested_by": requested_by,
                            "request_source": request_source,
                            "status": status.value,
                        }
                    ),
                    now,
                ),
            )
            connection.commit()
        result = self._image_task_sync(task_id)
        if result is None:
            raise RuntimeError("created image task could not be read")
        return result

    async def image_tasks(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._image_tasks_sync, status, limit)

    def _image_tasks_sync(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 500))
        query = """
            SELECT image_generation_tasks.*,
                   COUNT(image_artifacts.id) AS artifact_count,
                   approval_requests.status AS approval_status,
                   approval_requests.approval_code,
                   approval_requests.expires_at AS approval_expires_at
            FROM image_generation_tasks
            LEFT JOIN image_artifacts
              ON image_artifacts.task_id = image_generation_tasks.id
            LEFT JOIN approval_requests
              ON approval_requests.id = image_generation_tasks.approval_id
        """
        parameters: tuple[Any, ...]
        if status is None:
            parameters = (safe_limit,)
        else:
            query += " WHERE image_generation_tasks.status = ?"
            parameters = (status, safe_limit)
        query += (
            " GROUP BY image_generation_tasks.id "
            "ORDER BY image_generation_tasks.created_at DESC LIMIT ?"
        )
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    async def image_task(self, task_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._image_task_sync, task_id)

    def _image_task_sync(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT image_generation_tasks.*,
                       approval_requests.status AS approval_status,
                       approval_requests.approval_code,
                       approval_requests.expires_at AS approval_expires_at
                FROM image_generation_tasks
                LEFT JOIN approval_requests
                  ON approval_requests.id = image_generation_tasks.approval_id
                WHERE image_generation_tasks.id = ?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            artifacts = connection.execute(
                """
                SELECT id, task_id, content_sha256, media_type,
                       byte_count, width, height, created_at
                FROM image_artifacts WHERE task_id = ? ORDER BY created_at, id
                """,
                (task_id,),
            ).fetchall()
            reviews = connection.execute(
                """
                SELECT artifact_id, verdict, labels_json, engine, created_at
                FROM image_artifact_reviews WHERE task_id = ? ORDER BY created_at, artifact_id
                """,
                (task_id,),
            ).fetchall()
        return {
            **dict(row),
            "artifacts": [dict(item) for item in artifacts],
            "reviews": [
                {
                    "artifact_id": item["artifact_id"],
                    "verdict": item["verdict"],
                    "labels": json.loads(item["labels_json"]),
                    "engine": item["engine"],
                    "created_at": item["created_at"],
                }
                for item in reviews
            ],
        }

    async def claim_image_task(self, *, task_id: str, request_id: str) -> bool:
        return await asyncio.to_thread(self._claim_image_task_sync, task_id, request_id)

    def _claim_image_task_sync(self, task_id: str, request_id: str) -> bool:
        now = _utc_now()
        allowed = (
            ImageTaskStatus.DRAFT.value,
            ImageTaskStatus.AWAITING_MODEL_CONFIG.value,
            ImageTaskStatus.FAILED.value,
        )
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE image_generation_tasks
                SET status = ?, current_request_id = ?, error_type = NULL,
                    error_message = NULL, started_at = ?, completed_at = NULL,
                    updated_at = ?
                WHERE id = ? AND status IN (?, ?, ?)
                """,
                (
                    ImageTaskStatus.GENERATING.value,
                    request_id,
                    now,
                    now,
                    task_id,
                    *allowed,
                ),
            )
        return updated.rowcount == 1

    async def fail_image_task(
        self,
        *,
        task_id: str,
        request_id: str,
        error_type: str,
        error_message: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._fail_image_task_sync,
            task_id,
            request_id,
            error_type,
            error_message,
        )

    def _fail_image_task_sync(
        self,
        task_id: str,
        request_id: str,
        error_type: str,
        error_message: str,
    ) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE image_generation_tasks
                SET status = ?, error_type = ?, error_message = ?,
                    completed_at = ?, updated_at = ?
                WHERE id = ? AND current_request_id = ? AND status = ?
                """,
                (
                    ImageTaskStatus.FAILED.value,
                    error_type[:200],
                    error_message[:1000],
                    now,
                    now,
                    task_id,
                    request_id,
                    ImageTaskStatus.GENERATING.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        action, subject_type, subject_id, details_json, created_at
                    ) VALUES('image.generation_failed', 'image_task', ?, ?, ?)
                    """,
                    (task_id, _json({"error_type": error_type[:200]}), now),
                )
            connection.commit()
        return updated.rowcount == 1

    async def complete_image_task(
        self,
        *,
        task_id: str,
        request_id: str,
        provider_request_id: str | None,
        artifacts: tuple[dict[str, Any], ...],
        approval_id: str,
        approval_code: str,
        requested_to: str,
        report_id: str,
        reviews: tuple[dict[str, Any], ...] = (),
        blocked: bool = False,
    ) -> bool:
        return await asyncio.to_thread(
            self._complete_image_task_sync,
            task_id,
            request_id,
            provider_request_id,
            artifacts,
            approval_id,
            approval_code,
            requested_to,
            report_id,
            reviews,
            blocked,
        )

    def _complete_image_task_sync(
        self,
        task_id: str,
        request_id: str,
        provider_request_id: str | None,
        artifacts: tuple[dict[str, Any], ...],
        approval_id: str,
        approval_code: str,
        requested_to: str,
        report_id: str,
        reviews: tuple[dict[str, Any], ...] = (),
        blocked: bool = False,
    ) -> bool:
        now_at = datetime.now(UTC)
        now = now_at.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT intended_use FROM image_generation_tasks
                WHERE id = ? AND current_request_id = ? AND status = ?
                """,
                (task_id, request_id, ImageTaskStatus.GENERATING.value),
            ).fetchone()
            if active is None:
                connection.rollback()
                return False
            if not blocked:
                connection.execute(
                    """
                    INSERT INTO approval_requests(
                        id, request_type, subject_id, approval_code, status,
                        requested_to, expires_at, created_at
                    ) VALUES(?, 'image_generation.use', ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        approval_id,
                        task_id,
                        approval_code,
                        requested_to,
                        (now_at + timedelta(minutes=30)).isoformat(),
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO approval_payloads(approval_id, payload_json) VALUES(?, ?)",
                    (approval_id, _json({"task_id": task_id})),
                )
            connection.executemany(
                """
                INSERT INTO image_artifacts(
                    id, task_id, storage_path, content_sha256, media_type,
                    byte_count, width, height, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        item["id"],
                        task_id,
                        item["storage_path"],
                        item["content_sha256"],
                        item["media_type"],
                        item["byte_count"],
                        item["width"],
                        item["height"],
                        now,
                    )
                    for item in artifacts
                ),
            )
            if reviews:
                connection.executemany(
                    """
                    INSERT INTO image_artifact_reviews(
                        artifact_id, task_id, verdict, labels_json, engine, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            item["artifact_id"],
                            task_id,
                            item["verdict"],
                            _json(item.get("labels") or []),
                            item["engine"],
                            now,
                        )
                        for item in reviews
                    ),
                )
            status = (
                ImageTaskStatus.REVIEW_BLOCKED.value
                if blocked
                else ImageTaskStatus.PENDING_APPROVAL.value
            )
            connection.execute(
                """
                UPDATE image_generation_tasks
                SET status = ?, provider_request_id = ?, approval_id = ?,
                    completed_at = ?, updated_at = ?
                WHERE id = ? AND current_request_id = ? AND status = ?
                """,
                (
                    status,
                    provider_request_id,
                    None if blocked else approval_id,
                    now,
                    now,
                    task_id,
                    request_id,
                    ImageTaskStatus.GENERATING.value,
                ),
            )
            if blocked:
                record_owner_report_on_connection(
                    connection,
                    report_id=report_id,
                    severity="info",
                    category="image_generation",
                    title="图片生成被内容审核拦截",
                    body=(
                        f"任务 {task_id} 已生成 {len(artifacts)} 个本地产物，"
                        f"但内容审核未通过，未进入使用审批。"
                    ),
                    related_type="image_task",
                    related_id=task_id,
                    now=datetime.fromisoformat(now),
                )
            else:
                record_owner_report_on_connection(
                    connection,
                    report_id=report_id,
                    severity="action_required",
                    category="image_generation",
                    title="图片生成结果待审批",
                    body=(
                        f"任务 {task_id} 已生成 {len(artifacts)} 个本地产物；"
                        f"用途 {active['intended_use']}。确认码 {approval_code}。"
                    ),
                    related_type="image_task",
                    related_id=task_id,
                    now=datetime.fromisoformat(now),
                )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('image.preview_ready', 'image_task', ?, ?, ?)
                """,
                (
                    task_id,
                    _json(
                        {
                            "artifact_count": len(artifacts),
                            "approval_id": None if blocked else approval_id,
                            "report_id": report_id,
                            "blocked": blocked,
                        }
                    ),
                    now,
                ),
            )
            connection.commit()
        return True

    async def decide_image_task(
        self,
        *,
        task_id: str,
        status: ImageTaskStatus,
        actor_qq: str | None,
    ) -> bool:
        return await asyncio.to_thread(
            self._decide_image_task_sync,
            task_id,
            status,
            actor_qq,
        )

    def _decide_image_task_sync(
        self,
        task_id: str,
        status: ImageTaskStatus,
        actor_qq: str | None,
    ) -> bool:
        if status not in {
            ImageTaskStatus.APPROVED,
            ImageTaskStatus.REJECTED,
            ImageTaskStatus.APPROVAL_EXPIRED,
        }:
            raise ValueError("unsupported image task decision")
        now = _utc_now()
        approved_by = actor_qq if status is ImageTaskStatus.APPROVED else None
        approved_at = now if status is ImageTaskStatus.APPROVED else None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE image_generation_tasks
                SET status = ?, approved_by = ?, approved_at = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    approved_by,
                    approved_at,
                    now,
                    task_id,
                    ImageTaskStatus.PENDING_APPROVAL.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE owner_reports
                    SET status = 'acknowledged', acknowledged_at = ?
                    WHERE related_type = 'image_task' AND related_id = ?
                        AND status = 'pending'
                    """,
                    (now, task_id),
                )
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        action, subject_type, subject_id, details_json, created_at
                    ) VALUES('image.task_decided', 'image_task', ?, ?, ?)
                    """,
                    (
                        task_id,
                        _json({"status": status.value, "actor_qq": actor_qq}),
                        now,
                    ),
                )
            connection.commit()
        return updated.rowcount == 1

    async def renew_image_approval(
        self,
        *,
        task_id: str,
        approval_id: str,
        approval_code: str,
        requested_to: str,
        report_id: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._renew_image_approval_sync,
            task_id,
            approval_id,
            approval_code,
            requested_to,
            report_id,
        )

    def _renew_image_approval_sync(
        self,
        task_id: str,
        approval_id: str,
        approval_code: str,
        requested_to: str,
        report_id: str,
    ) -> bool:
        now_at = datetime.now(UTC)
        now = now_at.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                """
                SELECT image_generation_tasks.approval_id,
                       approval_requests.status AS approval_status
                FROM image_generation_tasks
                LEFT JOIN approval_requests
                  ON approval_requests.id = image_generation_tasks.approval_id
                WHERE image_generation_tasks.id = ? AND image_generation_tasks.status IN (?, ?)
                """,
                (
                    task_id,
                    ImageTaskStatus.APPROVAL_EXPIRED.value,
                    ImageTaskStatus.PENDING_APPROVAL.value,
                ),
            ).fetchone()
            if task is None:
                connection.rollback()
                return False
            if task["approval_id"] and task["approval_status"] == ApprovalStatus.PENDING.value:
                connection.execute(
                    """
                    UPDATE approval_requests
                    SET status = ?, decided_at = ? WHERE id = ? AND status = ?
                    """,
                    (
                        ApprovalStatus.EXPIRED.value,
                        now,
                        task["approval_id"],
                        ApprovalStatus.PENDING.value,
                    ),
                )
            connection.execute(
                """
                INSERT INTO approval_requests(
                    id, request_type, subject_id, approval_code, status,
                    requested_to, expires_at, created_at
                ) VALUES(?, 'image_generation.use', ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    approval_id,
                    task_id,
                    approval_code,
                    requested_to,
                    (now_at + timedelta(minutes=30)).isoformat(),
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO approval_payloads(approval_id, payload_json) VALUES(?, ?)",
                (approval_id, _json({"task_id": task_id, "renewed": True})),
            )
            updated = connection.execute(
                """
                UPDATE image_generation_tasks
                SET status = ?, approval_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    ImageTaskStatus.PENDING_APPROVAL.value,
                    approval_id,
                    now,
                    task_id,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return False
            record_owner_report_on_connection(
                connection,
                report_id=report_id,
                severity="action_required",
                category="image_generation",
                title="图片审批已续期",
                body=f"任务 {task_id} 的使用审批已重新发起。确认码 {approval_code}。",
                related_type="image_task",
                related_id=task_id,
                now=now_at,
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('image.approval_renewed', 'image_task', ?, ?, ?)
                """,
                (task_id, _json({"approval_id": approval_id, "report_id": report_id}), now),
            )
            connection.commit()
        return True

    async def image_artifact_paths(self) -> list[str]:
        return await asyncio.to_thread(self._image_artifact_paths_sync)

    def _image_artifact_paths_sync(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT storage_path FROM image_artifacts").fetchall()
        return [str(row["storage_path"]) for row in rows]

    async def record_image_orphan_scan(
        self,
        *,
        scan_id: str,
        orphan_count: int,
        missing_count: int,
        details: dict[str, Any],
        created_by: str,
        report_id: str | None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._record_image_orphan_scan_sync,
            scan_id,
            orphan_count,
            missing_count,
            details,
            created_by,
            report_id,
        )

    def _record_image_orphan_scan_sync(
        self,
        scan_id: str,
        orphan_count: int,
        missing_count: int,
        details: dict[str, Any],
        created_by: str,
        report_id: str | None,
    ) -> dict[str, Any]:
        now_at = datetime.now(UTC)
        now = now_at.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO image_orphan_scans(
                    id, orphan_count, missing_count, details_json, created_by, created_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (scan_id, orphan_count, missing_count, _json(details), created_by, now),
            )
            if report_id is not None:
                record_owner_report_on_connection(
                    connection,
                    report_id=report_id,
                    severity="info",
                    category="image_orphan_scan",
                    title="图片孤儿文件巡检完成",
                    body=(f"发现 {orphan_count} 个未登记文件、{missing_count} 个缺失产物。"),
                    related_type="image_orphan_scan",
                    related_id=scan_id,
                    now=now_at,
                )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('image.orphan_scan', 'image_orphan_scan', ?, ?, ?)
                """,
                (
                    scan_id,
                    _json(
                        {
                            "orphan_count": orphan_count,
                            "missing_count": missing_count,
                            "created_by": created_by,
                        }
                    ),
                    now,
                ),
            )
            connection.commit()
        return {
            "id": scan_id,
            "orphan_count": orphan_count,
            "missing_count": missing_count,
            "orphans": list(details.get("orphans") or []),
            "missing": list(details.get("missing") or []),
            "created_by": created_by,
            "created_at": now,
        }

    async def image_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._image_artifact_sync, artifact_id)

    def _image_artifact_sync(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT image_artifacts.*, image_generation_tasks.status AS task_status
                FROM image_artifacts
                JOIN image_generation_tasks ON image_generation_tasks.id = image_artifacts.task_id
                WHERE image_artifacts.id = ?
                """,
                (artifact_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    async def owner_reports(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._owner_reports_sync, status, limit)

    def _owner_reports_sync(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 500))
        query = "SELECT * FROM owner_reports"
        parameters: tuple[Any, ...]
        if status is None:
            parameters = (safe_limit,)
        else:
            query += " WHERE status = ?"
            parameters = (status, safe_limit)
        query += " ORDER BY created_at DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    async def acknowledge_owner_report(self, report_id: str, *, actor_qq: str) -> bool:
        return await asyncio.to_thread(
            self._acknowledge_owner_report_sync,
            report_id,
            actor_qq,
        )

    def _acknowledge_owner_report_sync(self, report_id: str, actor_qq: str) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE owner_reports
                SET status = 'acknowledged', acknowledged_at = ?
                WHERE id = ? AND status != 'acknowledged'
                """,
                (now, report_id),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        action, subject_type, subject_id, details_json, created_at
                    )
                    VALUES('owner_report.acknowledged', 'owner_report', ?, ?, ?)
                    """,
                    (report_id, _json({"actor_qq": actor_qq}), now),
                )
            connection.commit()
        return updated.rowcount == 1

    async def healthcheck(self) -> bool:
        return await asyncio.to_thread(self._healthcheck_sync)

    def _healthcheck_sync(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1").fetchone()[0] == 1

    async def store_inbound(self, message: UnifiedMessage) -> bool:
        return await asyncio.to_thread(self._store_inbound_sync, message)

    def _store_inbound_sync(self, message: UnifiedMessage) -> bool:
        received_at = _utc_now()
        segments_json = _json([segment.as_onebot() for segment in message.segments])
        raw_json = _json(message.raw_event)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO inbound_events(
                    event_key, bot_qq, post_type, payload_json, received_at
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (message.event_key, message.bot_qq, "message", raw_json, received_at),
            )
            if inserted.rowcount == 0:
                connection.rollback()
                return False

            connection.execute(
                """
                INSERT INTO users(qq_id, first_seen_at, last_seen_at)
                VALUES(?, ?, ?)
                ON CONFLICT(qq_id) DO UPDATE SET last_seen_at = excluded.last_seen_at
                """,
                (
                    message.sender_id,
                    message.occurred_at.isoformat(),
                    message.occurred_at.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO conversations(
                    conversation_key, kind, peer_id, created_at, last_activity_at
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(conversation_key) DO UPDATE SET
                    last_activity_at = excluded.last_activity_at
                """,
                (
                    message.conversation_key,
                    message.conversation_kind.value,
                    message.conversation_id,
                    message.occurred_at.isoformat(),
                    message.occurred_at.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO messages(
                    id, event_key, source_message_id, bot_qq, direction,
                    conversation_key, sender_id, occurred_at, plain_text,
                    segments_json, raw_json, reply_to_message_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.event_key,
                    message.source_message_id,
                    message.bot_qq,
                    message.direction.value,
                    message.conversation_key,
                    message.sender_id,
                    message.occurred_at.isoformat(),
                    message.plain_text,
                    segments_json,
                    raw_json,
                    message.reply_to_message_id,
                    received_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('message.ingested', 'message', ?, ?, ?)
                """,
                (
                    message.id,
                    _json({"conversation_key": message.conversation_key}),
                    received_at,
                ),
            )
            connection.commit()
            return True

    async def store_control_command(self, command: OwnerCommand) -> bool:
        return await asyncio.to_thread(self._store_control_command_sync, command)

    def _store_control_command_sync(self, command: OwnerCommand) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO control_commands(
                    id, source_message_id, actor_qq, action,
                    arguments_json, status, created_at
                ) VALUES(?, ?, ?, ?, ?, 'received', ?)
                """,
                (
                    command.id,
                    command.source_message_id,
                    command.actor_qq,
                    command.kind.value,
                    _json({"source": command.source.value, **command.arguments}),
                    _utc_now(),
                ),
            )
            return result.rowcount == 1

    async def finish_control_command(
        self,
        command_id: str,
        *,
        status: str,
        result: dict[str, Any],
    ) -> None:
        await asyncio.to_thread(
            self._finish_control_command_sync,
            command_id,
            status,
            result,
        )

    def _finish_control_command_sync(
        self,
        command_id: str,
        status: str,
        result: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE control_commands
                SET status = ?, result_json = ?, handled_at = ?
                WHERE id = ?
                """,
                (status, _json(result), _utc_now(), command_id),
            )

    async def control_commands(
        self,
        *,
        action: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._control_commands_sync,
            action,
            status,
            limit,
        )

    def _control_commands_sync(
        self,
        action: str | None,
        status: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if action is not None:
            clauses.append("action = ?")
            parameters.append(action)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        query = "SELECT * FROM control_commands"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(max(1, min(limit, 500)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["arguments"] = json.loads(item.pop("arguments_json"))
            raw_result = item.pop("result_json")
            item["result"] = json.loads(raw_result) if raw_result else None
            results.append(item)
        return results

    async def set_friend_state(
        self,
        *,
        user_qq: str,
        state: FriendState,
        updated_by: str,
        reason: str,
    ) -> None:
        await asyncio.to_thread(
            self._set_friend_state_sync,
            user_qq,
            state,
            updated_by,
            reason,
        )

    def _set_friend_state_sync(
        self,
        user_qq: str,
        state: FriendState,
        updated_by: str,
        reason: str,
    ) -> None:
        now = _utc_now()
        evidence_id = str(uuid5(NAMESPACE_URL, f"owner-relationship:{user_qq}:{state.value}:{now}"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO user_relationships(
                    user_qq, friend_state, state_source, confidence,
                    first_observed_at, updated_at, owner_overridden_by
                ) VALUES(?, ?, 'owner_override', 1, ?, ?, ?)
                ON CONFLICT(user_qq) DO UPDATE SET
                    friend_state = excluded.friend_state,
                    state_source = 'owner_override',
                    confidence = 1,
                    updated_at = excluded.updated_at,
                    owner_overridden_by = excluded.owner_overridden_by
                """,
                (user_qq, state.value, now, now, updated_by),
            )
            connection.execute(
                """
                INSERT INTO identity_evidence(
                    id, user_qq, evidence_type, source, value_json,
                    observed_at, created_at
                ) VALUES(?, ?, 'owner_override', ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    user_qq,
                    updated_by,
                    _json({"state": state.value, "reason": reason}),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('friend.state_overridden', 'user', ?, ?, ?)
                """,
                (user_qq, _json({"state": state.value, "reason": reason}), now),
            )
            connection.commit()

    async def restore_evidence_friend_state(
        self,
        *,
        user_qq: str,
        updated_by: str,
        reason: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._restore_evidence_friend_state_sync,
            user_qq,
            updated_by,
            reason,
        )

    def _restore_evidence_friend_state_sync(
        self,
        user_qq: str,
        updated_by: str,
        reason: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        evidence_id = str(uuid5(NAMESPACE_URL, f"owner-relationship-release:{user_qq}:{now}"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM user_relationships WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
            evidence = connection.execute(
                """
                SELECT evidence_type, value_json, observed_at
                FROM identity_evidence
                WHERE user_qq = ?
                  AND evidence_type IN ('baseline_snapshot', 'friend_add_event')
                ORDER BY observed_at DESC, created_at DESC LIMIT 1
                """,
                (user_qq,),
            ).fetchone()

            if evidence is None:
                state = FriendState.UNKNOWN
                source = "insufficient_evidence"
                confidence = 0.0
            elif evidence["evidence_type"] == "baseline_snapshot":
                state = FriendState.EXISTING_FRIEND
                source = "baseline_snapshot"
                confidence = 1.0
            else:
                value = json.loads(evidence["value_json"])
                if value.get("baseline_id"):
                    state = FriendState.NEW_FRIEND
                    source = "friend_add_after_baseline"
                    confidence = 1.0
                else:
                    state = FriendState.UNKNOWN
                    source = "friend_add_seen_before_baseline"
                    confidence = 0.5

            first_observed_at = (
                current["first_observed_at"]
                if current is not None
                else (evidence["observed_at"] if evidence is not None else now)
            )
            previous = {
                "friend_state": current["friend_state"] if current is not None else None,
                "state_source": current["state_source"] if current is not None else None,
            }
            connection.execute(
                """
                INSERT INTO user_relationships(
                    user_qq, friend_state, state_source, confidence,
                    first_observed_at, updated_at, owner_overridden_by
                ) VALUES(?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(user_qq) DO UPDATE SET
                    friend_state = excluded.friend_state,
                    state_source = excluded.state_source,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at,
                    owner_overridden_by = NULL
                """,
                (user_qq, state.value, source, confidence, first_observed_at, now),
            )
            connection.execute(
                """
                INSERT INTO identity_evidence(
                    id, user_qq, evidence_type, source, value_json,
                    observed_at, created_at
                ) VALUES(?, ?, 'owner_override_released', ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    user_qq,
                    updated_by,
                    _json(
                        {
                            "previous": previous,
                            "restored_state": state.value,
                            "restored_source": source,
                            "reason": reason,
                        }
                    ),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('friend.override_released', 'user', ?, ?, ?)
                """,
                (
                    user_qq,
                    _json(
                        {
                            "previous": previous,
                            "restored_state": state.value,
                            "restored_source": source,
                            "reason": reason,
                        }
                    ),
                    now,
                ),
            )
            connection.commit()
        return {
            "user_qq": user_qq,
            "friend_state": state.value,
            "state_source": source,
            "confidence": confidence,
            "previous": previous,
        }

    async def set_user_frozen(
        self,
        *,
        user_qq: str,
        frozen: bool,
        updated_by: str,
    ) -> None:
        await asyncio.to_thread(self._set_user_frozen_sync, user_qq, frozen, updated_by)

    def _set_user_frozen_sync(self, user_qq: str, frozen: bool, updated_by: str) -> None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO user_relationships(
                    user_qq, friend_state, state_source, confidence,
                    first_observed_at, updated_at, data_frozen
                ) VALUES(?, 'unknown', 'insufficient_evidence', 0, ?, ?, ?)
                ON CONFLICT(user_qq) DO UPDATE SET
                    data_frozen = excluded.data_frozen,
                    updated_at = excluded.updated_at
                """,
                (user_qq, now, now, int(frozen)),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('privacy.freeze_changed', 'user', ?, ?, ?)
                """,
                (user_qq, _json({"frozen": frozen, "updated_by": updated_by}), now),
            )
            connection.commit()

    async def list_known_users(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_known_users_sync, limit)

    def _list_known_users_sync(self, limit: int) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 500))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT
                        r.user_qq AS user_qq,
                        r.friend_state AS friend_state,
                        r.state_source AS state_source,
                        r.confidence AS confidence,
                        r.data_frozen AS data_frozen,
                        r.updated_at AS updated_at,
                        COALESCE(h.mode, 'deny') AS history_mode,
                        u.last_seen_at AS last_seen_at,
                        u.first_seen_at AS first_seen_at,
                        COALESCE(l.label, '') AS operator_label
                    FROM user_relationships r
                    LEFT JOIN users u ON u.qq_id = r.user_qq
                    LEFT JOIN history_access_policies h ON h.user_qq = r.user_qq
                    LEFT JOIN operator_labels l
                        ON l.subject_kind = 'user' AND l.subject_id = r.user_qq
                    UNION ALL
                    SELECT
                        u.qq_id AS user_qq,
                        COALESCE(u.relationship_state, 'unknown') AS friend_state,
                        'insufficient_evidence' AS state_source,
                        0 AS confidence,
                        0 AS data_frozen,
                        u.last_seen_at AS updated_at,
                        COALESCE(h.mode, 'deny') AS history_mode,
                        u.last_seen_at AS last_seen_at,
                        u.first_seen_at AS first_seen_at,
                        COALESCE(l.label, '') AS operator_label
                    FROM users u
                    LEFT JOIN user_relationships r ON r.user_qq = u.qq_id
                    LEFT JOIN history_access_policies h ON h.user_qq = u.qq_id
                    LEFT JOIN operator_labels l
                        ON l.subject_kind = 'user' AND l.subject_id = u.qq_id
                    WHERE r.user_qq IS NULL
                ) AS known_users
                ORDER BY COALESCE(last_seen_at, updated_at) DESC, user_qq
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    async def user_detail(self, user_qq: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._user_detail_sync, user_qq)

    def _user_detail_sync(self, user_qq: str) -> dict[str, Any]:
        with self._connect() as connection:
            relationship = connection.execute(
                "SELECT * FROM user_relationships WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
            history = connection.execute(
                "SELECT * FROM history_access_policies WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
            qzone_profile = connection.execute(
                "SELECT * FROM qzone_profile_access_policies WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
            snapshot = connection.execute(
                """
                SELECT * FROM qzone_profile_snapshots
                WHERE user_qq = ?
                ORDER BY fetched_at DESC LIMIT 1
                """,
                (user_qq,),
            ).fetchone()
            evidence = connection.execute(
                """
                SELECT id, evidence_type, source, value_json, observed_at
                FROM identity_evidence
                WHERE user_qq = ?
                ORDER BY observed_at DESC LIMIT 20
                """,
                (user_qq,),
            ).fetchall()
            reply_style = connection.execute(
                "SELECT * FROM user_reply_style_policies WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
            label_row = connection.execute(
                """
                SELECT label FROM operator_labels
                WHERE subject_kind = 'user' AND subject_id = ?
                """,
                (user_qq,),
            ).fetchone()
        return {
            "user_qq": user_qq,
            "operator_label": str(label_row["label"]) if label_row else "",
            "relationship": dict(relationship) if relationship else None,
            "history_policy": dict(history)
            if history
            else {
                "user_qq": user_qq,
                "mode": HistoryAccessMode.DENY.value,
                "source": "global_default",
            },
            "qzone_profile_policy": dict(qzone_profile)
            if qzone_profile
            else {
                "user_qq": user_qq,
                "mode": QzoneProfileAccessMode.DENY.value,
                "source": "global_default",
            },
            "latest_collection": collection_card(
                _snapshot_view(dict(snapshot)) if snapshot else None
            ),
            "identity_evidence": [
                {**dict(row), "value": json.loads(row["value_json"])} for row in evidence
            ],
            "reply_style": {
                **dict(reply_style),
                "persisted": True,
            }
            if reply_style
            else {
                "user_qq": user_qq,
                "min_bubbles": 1,
                "max_bubbles": 1,
                "sentence_min_chars": 12,
                "sentence_max_chars": 80,
                "persisted": False,
            },
        }

    async def list_operator_labels(self) -> list[dict[str, str]]:
        return await asyncio.to_thread(self._list_operator_labels_sync)

    def _list_operator_labels_sync(self) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT subject_kind, subject_id, label, updated_at, updated_by
                FROM operator_labels
                ORDER BY subject_kind, subject_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    async def upsert_operator_label(
        self,
        *,
        subject_kind: str,
        subject_id: str,
        label: str,
        updated_by: str,
    ) -> dict[str, str]:
        return await asyncio.to_thread(
            self._upsert_operator_label_sync,
            subject_kind,
            subject_id,
            label,
            updated_by,
        )

    def _upsert_operator_label_sync(
        self,
        subject_kind: str,
        subject_id: str,
        label: str,
        updated_by: str,
    ) -> dict[str, str]:
        now = _utc_now()
        with self._connect() as connection:
            if not label:
                connection.execute(
                    """
                    DELETE FROM operator_labels
                    WHERE subject_kind = ? AND subject_id = ?
                    """,
                    (subject_kind, subject_id),
                )
                return {
                    "subject_kind": subject_kind,
                    "subject_id": subject_id,
                    "label": "",
                    "updated_at": now,
                    "updated_by": updated_by,
                }
            connection.execute(
                """
                INSERT INTO operator_labels(
                    subject_kind, subject_id, label, updated_at, updated_by
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(subject_kind, subject_id) DO UPDATE SET
                    label = excluded.label,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (subject_kind, subject_id, label, now, updated_by),
            )
            row = connection.execute(
                """
                SELECT subject_kind, subject_id, label, updated_at, updated_by
                FROM operator_labels
                WHERE subject_kind = ? AND subject_id = ?
                """,
                (subject_kind, subject_id),
            ).fetchone()
        return (
            dict(row)
            if row
            else {
                "subject_kind": subject_kind,
                "subject_id": subject_id,
                "label": label,
                "updated_at": now,
                "updated_by": updated_by,
            }
        )

    async def register_knowledge_document(
        self,
        *,
        document_id: str,
        job_id: str,
        user_qq: str,
        purpose: str,
        original_filename: str,
        content_sha256: str,
        byte_count: int,
        text_length: int,
        document_status: str,
        job_status: str,
        storage_path: str,
        media_type: str,
        detected_format: str,
        chunks: tuple[str, ...],
        created_by: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._register_knowledge_document_sync,
            document_id,
            job_id,
            user_qq,
            purpose,
            original_filename,
            content_sha256,
            byte_count,
            text_length,
            document_status,
            job_status,
            storage_path,
            media_type,
            detected_format,
            chunks,
            created_by,
        )

    def _register_knowledge_document_sync(
        self,
        document_id: str,
        job_id: str,
        user_qq: str,
        purpose: str,
        original_filename: str,
        content_sha256: str,
        byte_count: int,
        text_length: int,
        document_status: str,
        job_status: str,
        storage_path: str,
        media_type: str,
        detected_format: str,
        chunks: tuple[str, ...],
        created_by: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO knowledge_documents(
                    id, subject_user_qq, purpose, original_filename,
                    content_sha256, text_length, status, created_by, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    user_qq,
                    purpose,
                    original_filename,
                    content_sha256,
                    text_length,
                    document_status,
                    created_by,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO document_blobs(
                    document_id, storage_path, media_type,
                    detected_format, byte_count, created_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    storage_path,
                    media_type,
                    detected_format,
                    byte_count,
                    now,
                ),
            )
            connection.executemany(
                """
                INSERT INTO document_chunks(
                    id, document_id, chunk_index, content_text,
                    content_sha256, char_count, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        str(uuid4()),
                        document_id,
                        index,
                        content,
                        hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        len(content),
                        now,
                    )
                    for index, content in enumerate(chunks)
                ),
            )
            connection.execute(
                """
                INSERT INTO knowledge_processing_jobs(
                    id, document_id, user_qq, purpose, status,
                    progress, requested_by, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    document_id,
                    user_qq,
                    purpose,
                    job_status,
                    10 if chunks else 0,
                    created_by,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('knowledge.document_staged', 'knowledge_document', ?, ?, ?)
                """,
                (
                    document_id,
                    _json(
                        {
                            "job_id": job_id,
                            "user_qq": user_qq,
                            "purpose": purpose,
                            "byte_count": byte_count,
                            "chunk_count": len(chunks),
                        }
                    ),
                    now,
                ),
            )
            connection.commit()
        return {
            "document_id": document_id,
            "job_id": job_id,
            "document_status": document_status,
            "job_status": job_status,
            "byte_count": byte_count,
            "text_length": text_length,
            "chunk_count": len(chunks),
            "sha256": content_sha256,
        }

    async def knowledge_jobs(self, user_qq: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._knowledge_jobs_sync, user_qq)

    def _knowledge_jobs_sync(self, user_qq: str | None) -> list[dict[str, Any]]:
        query = """
            SELECT knowledge_processing_jobs.*,
                   knowledge_documents.original_filename,
                   knowledge_documents.content_sha256,
                   knowledge_documents.text_length,
                   document_blobs.byte_count,
                   document_blobs.detected_format,
                   (SELECT COUNT(*) FROM document_chunks
                    WHERE document_chunks.document_id = knowledge_documents.id) AS chunk_count,
                   (SELECT COUNT(*) FROM knowledge_chunk_analyses
                    WHERE knowledge_chunk_analyses.job_id =
                          knowledge_processing_jobs.id) AS analyzed_chunk_count,
                   (SELECT state FROM knowledge_job_checkpoints
                    WHERE knowledge_job_checkpoints.job_id =
                          knowledge_processing_jobs.id) AS checkpoint_state,
                   (SELECT attempt_count FROM knowledge_job_checkpoints
                    WHERE knowledge_job_checkpoints.job_id =
                          knowledge_processing_jobs.id) AS attempt_count,
                   (SELECT status FROM approval_requests
                    WHERE request_type = 'knowledge.preview'
                      AND subject_id = knowledge_processing_jobs.id
                    ORDER BY created_at DESC LIMIT 1) AS approval_status,
                   (SELECT approval_code FROM approval_requests
                    WHERE request_type = 'knowledge.preview'
                      AND subject_id = knowledge_processing_jobs.id
                    ORDER BY created_at DESC LIMIT 1) AS approval_code
            FROM knowledge_processing_jobs
            JOIN knowledge_documents
              ON knowledge_documents.id = knowledge_processing_jobs.document_id
            JOIN document_blobs
              ON document_blobs.document_id = knowledge_documents.id
        """
        parameters: tuple[str, ...] = ()
        if user_qq is not None:
            query += " WHERE knowledge_processing_jobs.user_qq = ?"
            parameters = (user_qq,)
        query += " ORDER BY knowledge_processing_jobs.created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._decode_knowledge_job(row) for row in rows]

    async def knowledge_job(self, job_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._knowledge_job_sync, job_id)

    def _knowledge_job_sync(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT knowledge_processing_jobs.*,
                       knowledge_documents.original_filename,
                       knowledge_documents.content_sha256,
                       knowledge_documents.text_length,
                       document_blobs.byte_count,
                       document_blobs.detected_format,
                       (SELECT COUNT(*) FROM document_chunks
                        WHERE document_chunks.document_id = knowledge_documents.id) AS chunk_count,
                       (SELECT COUNT(*) FROM knowledge_chunk_analyses
                        WHERE knowledge_chunk_analyses.job_id =
                              knowledge_processing_jobs.id) AS analyzed_chunk_count,
                       (SELECT state FROM knowledge_job_checkpoints
                        WHERE knowledge_job_checkpoints.job_id =
                              knowledge_processing_jobs.id) AS checkpoint_state,
                       (SELECT attempt_count FROM knowledge_job_checkpoints
                        WHERE knowledge_job_checkpoints.job_id =
                              knowledge_processing_jobs.id) AS attempt_count,
                       (SELECT status FROM approval_requests
                        WHERE request_type = 'knowledge.preview'
                          AND subject_id = knowledge_processing_jobs.id
                        ORDER BY created_at DESC LIMIT 1) AS approval_status,
                       (SELECT approval_code FROM approval_requests
                        WHERE request_type = 'knowledge.preview'
                          AND subject_id = knowledge_processing_jobs.id
                        ORDER BY created_at DESC LIMIT 1) AS approval_code
                FROM knowledge_processing_jobs
                JOIN knowledge_documents
                  ON knowledge_documents.id = knowledge_processing_jobs.document_id
                JOIN document_blobs
                  ON document_blobs.document_id = knowledge_documents.id
                WHERE knowledge_processing_jobs.id = ?
                """,
                (job_id,),
            ).fetchone()
        return self._decode_knowledge_job(row) if row else None

    @staticmethod
    def _decode_knowledge_job(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        raw_preview = result.pop("result_preview_json", None)
        result["result_preview"] = json.loads(raw_preview) if raw_preview else None
        return result

    async def claim_knowledge_job(
        self,
        *,
        job_id: str,
        lease_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> dict[str, Any] | None:
        claimed = await asyncio.to_thread(
            self._claim_knowledge_job_sync,
            job_id,
            lease_token,
            now,
            lease_expires_at,
        )
        if not claimed:
            return None
        return await self.knowledge_processing_bundle(job_id)

    def _claim_knowledge_job_sync(
        self,
        job_id: str,
        lease_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        now_iso = now.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT status FROM knowledge_processing_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if job is None or job["status"] not in {
                "queued",
                "awaiting_model_config",
                "processing",
                "failed",
            }:
                connection.rollback()
                return False
            checkpoint = connection.execute(
                "SELECT * FROM knowledge_job_checkpoints WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if (
                checkpoint is not None
                and checkpoint["state"] == "processing"
                and checkpoint["lease_token"] != lease_token
                and checkpoint["lease_expires_at"] is not None
                and checkpoint["lease_expires_at"] > now_iso
            ):
                connection.rollback()
                return False
            connection.execute(
                """
                INSERT INTO knowledge_job_checkpoints(
                    job_id, state, lease_token, lease_expires_at,
                    attempt_count, last_error_type, updated_at
                ) VALUES(?, 'processing', ?, ?, 1, NULL, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    state = 'processing',
                    lease_token = excluded.lease_token,
                    lease_expires_at = excluded.lease_expires_at,
                    attempt_count = knowledge_job_checkpoints.attempt_count + 1,
                    last_error_type = NULL,
                    updated_at = excluded.updated_at
                """,
                (job_id, lease_token, lease_expires_at.isoformat(), now_iso),
            )
            connection.execute(
                """
                UPDATE knowledge_processing_jobs
                SET status = 'processing', error = NULL,
                    started_at = COALESCE(started_at, ?)
                WHERE id = ?
                """,
                (now_iso, job_id),
            )
            connection.commit()
        return True

    async def knowledge_processing_bundle(self, job_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._knowledge_processing_bundle_sync, job_id)

    def _knowledge_processing_bundle_sync(self, job_id: str) -> dict[str, Any] | None:
        job = self._knowledge_job_sync(job_id)
        if job is None:
            return None
        with self._connect() as connection:
            chunks = connection.execute(
                """
                SELECT id, document_id, chunk_index, content_text,
                       content_sha256, char_count
                FROM document_chunks
                WHERE document_id = ? ORDER BY chunk_index
                """,
                (job["document_id"],),
            ).fetchall()
            analyses = connection.execute(
                """
                SELECT id, job_id, chunk_id, chunk_index, purpose,
                       result_json, provider_request_id, created_at, updated_at
                FROM knowledge_chunk_analyses
                WHERE job_id = ? ORDER BY chunk_index
                """,
                (job_id,),
            ).fetchall()
            checkpoint = connection.execute(
                "SELECT * FROM knowledge_job_checkpoints WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return {
            "job": job,
            "chunks": [dict(row) for row in chunks],
            "analyses": [
                {**dict(row), "result": json.loads(row["result_json"])} for row in analyses
            ],
            "checkpoint": dict(checkpoint) if checkpoint is not None else None,
        }

    async def knowledge_worker_candidates(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._knowledge_worker_candidates_sync, limit)

    def _knowledge_worker_candidates_sync(self, limit: int) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 500))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT knowledge_processing_jobs.id,
                       knowledge_processing_jobs.status,
                       knowledge_processing_jobs.created_at,
                       knowledge_processing_jobs.purpose,
                       knowledge_job_checkpoints.state AS checkpoint_state,
                       COALESCE(knowledge_job_checkpoints.attempt_count, 0) AS attempt_count,
                       knowledge_job_checkpoints.updated_at AS checkpoint_updated_at,
                       knowledge_job_checkpoints.lease_expires_at
                FROM knowledge_processing_jobs
                LEFT JOIN knowledge_job_checkpoints
                  ON knowledge_job_checkpoints.job_id = knowledge_processing_jobs.id
                WHERE knowledge_processing_jobs.status IN (
                    'queued', 'awaiting_model_config', 'processing'
                )
                ORDER BY knowledge_processing_jobs.created_at, knowledge_processing_jobs.id
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    async def store_knowledge_chunk_analysis(
        self,
        *,
        job_id: str,
        lease_token: str,
        lease_expires_at: datetime,
        chunk_id: str,
        chunk_index: int,
        purpose: str,
        result: dict[str, Any],
        provider_request_id: str | None,
    ) -> bool:
        return await asyncio.to_thread(
            self._store_knowledge_chunk_analysis_sync,
            job_id,
            lease_token,
            lease_expires_at,
            chunk_id,
            chunk_index,
            purpose,
            result,
            provider_request_id,
        )

    def _store_knowledge_chunk_analysis_sync(
        self,
        job_id: str,
        lease_token: str,
        lease_expires_at: datetime,
        chunk_id: str,
        chunk_index: int,
        purpose: str,
        result: dict[str, Any],
        provider_request_id: str | None,
    ) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            checkpoint = connection.execute(
                """
                SELECT state, lease_token FROM knowledge_job_checkpoints
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if (
                checkpoint is None
                or checkpoint["state"] != "processing"
                or checkpoint["lease_token"] != lease_token
            ):
                connection.rollback()
                return False
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO knowledge_chunk_analyses(
                    id, job_id, chunk_id, chunk_index, purpose,
                    result_json, provider_request_id, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    job_id,
                    chunk_id,
                    chunk_index,
                    purpose,
                    _json(result),
                    provider_request_id,
                    now,
                    now,
                ),
            )
            total = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM document_chunks
                    WHERE document_id = (
                        SELECT document_id FROM knowledge_processing_jobs WHERE id = ?
                    )
                    """,
                    (job_id,),
                ).fetchone()[0]
            )
            completed = int(
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_chunk_analyses WHERE job_id = ?",
                    (job_id,),
                ).fetchone()[0]
            )
            progress = min(90, 10 + int((completed / max(total, 1)) * 80))
            connection.execute(
                """
                UPDATE knowledge_job_checkpoints
                SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND lease_token = ?
                """,
                (lease_expires_at.isoformat(), now, job_id, lease_token),
            )
            connection.execute(
                "UPDATE knowledge_processing_jobs SET progress = ? WHERE id = ?",
                (progress, job_id),
            )
            connection.commit()
        return inserted.rowcount == 1

    async def renew_knowledge_job_lease(
        self,
        *,
        job_id: str,
        lease_token: str,
        lease_expires_at: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._renew_knowledge_job_lease_sync,
            job_id,
            lease_token,
            lease_expires_at,
        )

    def _renew_knowledge_job_lease_sync(
        self,
        job_id: str,
        lease_token: str,
        lease_expires_at: datetime,
    ) -> bool:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE knowledge_job_checkpoints
                SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND state = 'processing' AND lease_token = ?
                """,
                (lease_expires_at.isoformat(), _utc_now(), job_id, lease_token),
            )
        return updated.rowcount == 1

    async def complete_knowledge_job(
        self,
        *,
        job_id: str,
        lease_token: str,
        preview: dict[str, Any],
        approval_id: str,
        approval_code: str,
        requested_to: str,
        report_id: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._complete_knowledge_job_sync,
            job_id,
            lease_token,
            preview,
            approval_id,
            approval_code,
            requested_to,
            report_id,
        )

    def _complete_knowledge_job_sync(
        self,
        job_id: str,
        lease_token: str,
        preview: dict[str, Any],
        approval_id: str,
        approval_code: str,
        requested_to: str,
        report_id: str,
    ) -> bool:
        now_at = datetime.now(UTC)
        now = now_at.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                """
                SELECT document_id FROM knowledge_processing_jobs
                WHERE id = ? AND status = 'processing'
                """,
                (job_id,),
            ).fetchone()
            checkpoint = connection.execute(
                """
                SELECT state, lease_token FROM knowledge_job_checkpoints
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if (
                job is None
                or checkpoint is None
                or checkpoint["state"] != "processing"
                or checkpoint["lease_token"] != lease_token
            ):
                connection.rollback()
                return False
            connection.execute(
                """
                INSERT INTO approval_requests(
                    id, request_type, subject_id, approval_code, status,
                    requested_to, expires_at, created_at
                ) VALUES(?, 'knowledge.preview', ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    approval_id,
                    job_id,
                    approval_code,
                    requested_to,
                    (now_at + timedelta(minutes=30)).isoformat(),
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO approval_payloads(approval_id, payload_json) VALUES(?, ?)",
                (approval_id, _json({"job_id": job_id})),
            )
            connection.execute(
                """
                UPDATE knowledge_processing_jobs
                SET status = 'awaiting_approval', progress = 100,
                    result_preview_json = ?, error = NULL, completed_at = ?
                WHERE id = ?
                """,
                (_json(preview), now, job_id),
            )
            connection.execute(
                """
                UPDATE knowledge_job_checkpoints
                SET state = 'completed', lease_token = NULL,
                    lease_expires_at = NULL, last_error_type = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (now, job_id),
            )
            connection.execute(
                """
                UPDATE knowledge_documents SET status = 'preview_ready'
                WHERE id = ?
                """,
                (job["document_id"],),
            )
            record_owner_report_on_connection(
                connection,
                report_id=report_id,
                severity="action_required",
                category="knowledge_processing",
                title="长资料炼化结果待审批",
                body=f"任务 {job_id} 已生成预览。确认码 {approval_code}。",
                related_type="knowledge_job",
                related_id=job_id,
                now=datetime.fromisoformat(now) if isinstance(now, str) else now,
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('knowledge.processing_completed', 'knowledge_job', ?, ?, ?)
                """,
                (
                    job_id,
                    _json(
                        {
                            "status": "awaiting_approval",
                            "approval_id": approval_id,
                            "report_id": report_id,
                        }
                    ),
                    now,
                ),
            )
            connection.commit()
        return True

    async def fail_knowledge_job(
        self,
        *,
        job_id: str,
        lease_token: str,
        error_type: str,
        error_message: str,
        retryable: bool,
    ) -> bool:
        return await asyncio.to_thread(
            self._fail_knowledge_job_sync,
            job_id,
            lease_token,
            error_type,
            error_message,
            retryable,
        )

    def _fail_knowledge_job_sync(
        self,
        job_id: str,
        lease_token: str,
        error_type: str,
        error_message: str,
        retryable: bool,
    ) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE knowledge_job_checkpoints
                SET state = 'failed', lease_token = NULL, lease_expires_at = NULL,
                    last_error_type = ?, updated_at = ?
                WHERE job_id = ? AND state = 'processing' AND lease_token = ?
                """,
                (error_type[:200], now, job_id, lease_token),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return False
            connection.execute(
                """
                UPDATE knowledge_processing_jobs
                SET status = ?, error = ?
                WHERE id = ?
                """,
                ("queued" if retryable else "failed", error_message[:1000], job_id),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('knowledge.processing_failed', 'knowledge_job', ?, ?, ?)
                """,
                (
                    job_id,
                    _json({"error_type": error_type[:200], "retryable": retryable}),
                    now,
                ),
            )
            connection.commit()
        return True

    async def store_knowledge_preview(
        self,
        job_id: str,
        preview: dict[str, Any],
    ) -> bool:
        return await asyncio.to_thread(self._store_knowledge_preview_sync, job_id, preview)

    def _store_knowledge_preview_sync(
        self,
        job_id: str,
        preview: dict[str, Any],
    ) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE knowledge_processing_jobs
                SET status = 'awaiting_approval', progress = 100,
                    result_preview_json = ?, completed_at = ?
                WHERE id = ? AND status IN ('processing', 'queued', 'awaiting_model_config')
                """,
                (_json(preview), _utc_now(), job_id),
            )
            return result.rowcount == 1

    async def approve_knowledge_preview(
        self,
        *,
        job_id: str,
        approved_by: str,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._approve_knowledge_preview_sync,
            job_id,
            approved_by,
        )

    def _approve_knowledge_preview_sync(
        self,
        job_id: str,
        approved_by: str,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                """
                SELECT * FROM knowledge_processing_jobs
                WHERE id = ? AND status = 'awaiting_approval'
                """,
                (job_id,),
            ).fetchone()
            if job is None or not job["result_preview_json"]:
                connection.rollback()
                return None
            approval = connection.execute(
                """
                SELECT id, status, expires_at FROM approval_requests
                WHERE request_type = 'knowledge.preview' AND subject_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if approval is not None:
                if approval["status"] != ApprovalStatus.PENDING.value:
                    connection.rollback()
                    return None
                if datetime.fromisoformat(approval["expires_at"]) < datetime.now(UTC):
                    connection.execute(
                        """
                        UPDATE approval_requests
                        SET status = 'expired', decided_at = ? WHERE id = ?
                        """,
                        (now, approval["id"]),
                    )
                    connection.execute(
                        """
                        UPDATE knowledge_processing_jobs SET status = 'approval_expired'
                        WHERE id = ?
                        """,
                        (job_id,),
                    )
                    connection.commit()
                    return None
            preview = json.loads(job["result_preview_json"])
            user_qq = job["user_qq"]
            purpose = job["purpose"]
            if purpose == "user_understanding":
                version = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(version), 0) + 1
                        FROM user_understanding_profiles WHERE user_qq = ?
                        """,
                        (user_qq,),
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    UPDATE user_understanding_profiles SET status = 'superseded'
                    WHERE user_qq = ? AND status = 'active'
                    """,
                    (user_qq,),
                )
                result_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO user_understanding_profiles(
                        id, user_qq, source_document_id, summary_json,
                        evidence_json, version, status, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (
                        result_id,
                        user_qq,
                        job["document_id"],
                        _json(preview["summary"]),
                        _json(preview.get("evidence", [])),
                        version,
                        now,
                    ),
                )
            else:
                version = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(version), 0) + 1
                        FROM persona_profiles
                        WHERE scope_type = 'private_user' AND scope_id = ?
                          AND source_type = 'document_derived'
                        """,
                        (user_qq,),
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    UPDATE persona_profiles SET status = 'superseded'
                    WHERE scope_type = 'private_user' AND scope_id = ?
                      AND source_type = 'document_derived' AND status = 'active'
                    """,
                    (user_qq,),
                )
                result_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO persona_profiles(
                        id, scope_type, scope_id, source_type,
                        source_document_id, name, developer_definition,
                        traits_json, version, status, created_by, created_at
                    ) VALUES(?, 'private_user', ?, 'document_derived', ?, ?, '', ?,
                             ?, 'active', ?, ?)
                    """,
                    (
                        result_id,
                        user_qq,
                        job["document_id"],
                        "资料派生人格",
                        _json(preview["traits"]),
                        version,
                        approved_by,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO persona_profile_evidence(profile_id, evidence_json, created_at)
                    VALUES(?, ?, ?)
                    """,
                    (result_id, _json(preview.get("evidence", [])), now),
                )
            connection.execute(
                """
                UPDATE knowledge_processing_jobs SET status = 'approved'
                WHERE id = ?
                """,
                (job_id,),
            )
            if approval is not None:
                connection.execute(
                    """
                    UPDATE approval_requests
                    SET status = 'approved', decided_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now, approval["id"]),
                )
            connection.execute(
                """
                UPDATE owner_reports
                SET status = 'acknowledged', acknowledged_at = ?
                WHERE related_type = 'knowledge_job' AND related_id = ?
                  AND status = 'pending'
                """,
                (now, job_id),
            )
            connection.execute(
                """
                UPDATE knowledge_documents SET status = 'approved', completed_at = ?
                WHERE id = ?
                """,
                (now, job["document_id"]),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('knowledge.preview_approved', 'knowledge_job', ?, ?, ?)
                """,
                (
                    job_id,
                    _json(
                        {
                            "purpose": purpose,
                            "result_id": result_id,
                            "approved_by": approved_by,
                        }
                    ),
                    now,
                ),
            )
            connection.commit()
        return {
            "job_id": job_id,
            "status": "approved",
            "purpose": purpose,
            "result_id": result_id,
            "version": version,
        }

    async def reject_knowledge_preview(self, *, job_id: str, rejected_by: str) -> bool:
        return await asyncio.to_thread(
            self._reject_knowledge_preview_sync,
            job_id,
            rejected_by,
        )

    def _reject_knowledge_preview_sync(self, job_id: str, rejected_by: str) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                """
                SELECT document_id FROM knowledge_processing_jobs
                WHERE id = ? AND status = 'awaiting_approval'
                """,
                (job_id,),
            ).fetchone()
            if job is None:
                connection.rollback()
                return False
            connection.execute(
                "UPDATE knowledge_processing_jobs SET status = 'rejected' WHERE id = ?",
                (job_id,),
            )
            connection.execute(
                "UPDATE knowledge_documents SET status = 'preview_rejected' WHERE id = ?",
                (job["document_id"],),
            )
            connection.execute(
                """
                UPDATE approval_requests
                SET status = 'rejected', decided_at = ?
                WHERE request_type = 'knowledge.preview' AND subject_id = ?
                  AND status = 'pending'
                """,
                (now, job_id),
            )
            connection.execute(
                """
                UPDATE owner_reports
                SET status = 'acknowledged', acknowledged_at = ?
                WHERE related_type = 'knowledge_job' AND related_id = ?
                  AND status = 'pending'
                """,
                (now, job_id),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('knowledge.preview_rejected', 'knowledge_job', ?, ?, ?)
                """,
                (job_id, _json({"rejected_by": rejected_by}), now),
            )
            connection.commit()
        return True

    async def expire_knowledge_preview(self, job_id: str) -> bool:
        return await asyncio.to_thread(self._expire_knowledge_preview_sync, job_id)

    def _expire_knowledge_preview_sync(self, job_id: str) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE knowledge_processing_jobs SET status = 'approval_expired'
                WHERE id = ? AND status = 'awaiting_approval'
                """,
                (job_id,),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE owner_reports
                    SET status = 'acknowledged', acknowledged_at = ?
                    WHERE related_type = 'knowledge_job' AND related_id = ?
                      AND status = 'pending'
                    """,
                    (now, job_id),
                )
            connection.commit()
        return updated.rowcount == 1

    async def save_manual_persona(
        self,
        *,
        profile_id: str,
        scope: ProfileScope,
        scope_id: str,
        name: str,
        developer_definition: str,
        created_by: str,
    ) -> PersonaProfile:
        return await asyncio.to_thread(
            self._save_manual_persona_sync,
            profile_id,
            scope,
            scope_id,
            name,
            developer_definition,
            created_by,
        )

    def _save_manual_persona_sync(
        self,
        profile_id: str,
        scope: ProfileScope,
        scope_id: str,
        name: str,
        developer_definition: str,
        created_by: str,
    ) -> PersonaProfile:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1
                FROM persona_profiles
                WHERE scope_type = ? AND scope_id = ? AND source_type = 'manual'
                """,
                (scope.value, scope_id),
            ).fetchone()
            version = int(row[0])
            connection.execute(
                """
                UPDATE persona_profiles SET status = 'superseded'
                WHERE scope_type = ? AND scope_id = ?
                  AND source_type = 'manual' AND status = 'active'
                """,
                (scope.value, scope_id),
            )
            connection.execute(
                """
                INSERT INTO persona_profiles(
                    id, scope_type, scope_id, source_type, name,
                    developer_definition, traits_json, version,
                    status, created_by, created_at
                ) VALUES(?, ?, ?, 'manual', ?, ?, '{}', ?, 'active', ?, ?)
                """,
                (
                    profile_id,
                    scope.value,
                    scope_id,
                    name,
                    developer_definition,
                    version,
                    created_by,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('persona.manual_saved', 'persona_profile', ?, ?, ?)
                """,
                (
                    profile_id,
                    _json(
                        {
                            "scope": scope.value,
                            "scope_id": scope_id,
                            "version": version,
                        }
                    ),
                    now,
                ),
            )
            connection.commit()
        return PersonaProfile(
            id=profile_id,
            scope=scope,
            scope_id=scope_id,
            source=ProfileSource.MANUAL,
            developer_definition=developer_definition,
            traits={},
            version=version,
        )

    async def active_persona_inputs(
        self,
        user_qq: str,
    ) -> tuple[tuple[PersonaProfile, ...], tuple[UserUnderstanding, ...]]:
        return await asyncio.to_thread(self._active_persona_inputs_sync, user_qq)

    def _active_persona_inputs_sync(
        self,
        user_qq: str,
    ) -> tuple[tuple[PersonaProfile, ...], tuple[UserUnderstanding, ...]]:
        with self._connect() as connection:
            profile_rows = connection.execute(
                """
                SELECT * FROM persona_profiles
                WHERE status = 'active'
                  AND ((scope_type = 'global' AND scope_id = '*')
                       OR (scope_type = 'private_user' AND scope_id = ?))
                ORDER BY version ASC
                """,
                (user_qq,),
            ).fetchall()
            understanding_rows = connection.execute(
                """
                SELECT * FROM user_understanding_profiles
                WHERE user_qq = ? AND status = 'active'
                ORDER BY version ASC
                """,
                (user_qq,),
            ).fetchall()
        profiles = tuple(
            PersonaProfile(
                id=row["id"],
                scope=ProfileScope(row["scope_type"]),
                scope_id=row["scope_id"],
                source=ProfileSource(row["source_type"]),
                developer_definition=row["developer_definition"],
                traits=json.loads(row["traits_json"]),
                version=int(row["version"]),
            )
            for row in profile_rows
        )
        understandings = tuple(
            UserUnderstanding(
                id=row["id"],
                user_qq=row["user_qq"],
                summary=json.loads(row["summary_json"]),
                evidence=tuple(json.loads(row["evidence_json"])),
                version=int(row["version"]),
            )
            for row in understanding_rows
        )
        return profiles, understandings

    async def persona_profiles(self, *, user_qq: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._persona_profiles_sync, user_qq)

    def _persona_profiles_sync(self, user_qq: str | None) -> list[dict[str, Any]]:
        query = "SELECT * FROM persona_profiles WHERE status = 'active'"
        parameters: tuple[str, ...] = ()
        if user_qq is not None:
            query += " AND (scope_id = '*' OR scope_id = ?)"
            parameters = (user_qq,)
        query += " ORDER BY scope_type, source_type, version DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [{**dict(row), "traits": json.loads(row["traits_json"])} for row in rows]

    async def create_memory(
        self,
        *,
        memory_id: str,
        user_qq: str,
        kind: MemoryKind,
        key: str,
        value: dict[str, Any],
        source: MemorySource,
        confidence: float,
        created_by: str,
        source_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._create_memory_sync,
            memory_id,
            user_qq,
            kind,
            key,
            value,
            source,
            confidence,
            created_by,
            source_id,
            expires_at,
        )

    def _create_memory_sync(
        self,
        memory_id: str,
        user_qq: str,
        kind: MemoryKind,
        key: str,
        value: dict[str, Any],
        source: MemorySource,
        confidence: float,
        created_by: str,
        source_id: str | None,
        expires_at: str | None,
    ) -> dict[str, Any]:
        now = _utc_now()
        desired_status = (
            MemoryStatus.ACTIVE if source is MemorySource.OWNER_MANUAL else MemoryStatus.CANDIDATE
        )
        encoded_value = _json(value)
        conflict_id: str | None = None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM memory_records
                WHERE user_qq = ? AND memory_kind = ? AND memory_key = ?
                  AND status = 'active'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (user_qq, kind.value, key),
            ).fetchone()
            if existing is not None and existing["value_json"] == encoded_value:
                connection.rollback()
                return {"id": existing["id"], "status": "active", "deduplicated": True}
            if existing is not None and desired_status is MemoryStatus.ACTIVE:
                desired_status = MemoryStatus.DISPUTED
                connection.execute(
                    "UPDATE memory_records SET status = 'disputed', updated_at = ? WHERE id = ?",
                    (now, existing["id"]),
                )
                conflict_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO memory_records(
                    id, user_qq, memory_kind, memory_key, value_json,
                    source_type, source_id, confidence, status,
                    expires_at, created_by, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    user_qq,
                    kind.value,
                    key,
                    encoded_value,
                    source.value,
                    source_id,
                    confidence,
                    desired_status.value,
                    expires_at,
                    created_by,
                    now,
                    now,
                ),
            )
            if conflict_id is not None and existing is not None:
                connection.execute(
                    """
                    INSERT INTO memory_conflicts(
                        id, user_qq, memory_key, left_memory_id,
                        right_memory_id, status, created_at
                    ) VALUES(?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (conflict_id, user_qq, key, existing["id"], memory_id, now),
                )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('memory.created', 'memory', ?, ?, ?)
                """,
                (
                    memory_id,
                    _json(
                        {
                            "user_qq": user_qq,
                            "kind": kind.value,
                            "key": key,
                            "status": desired_status.value,
                        }
                    ),
                    now,
                ),
            )
            if source_id is not None:
                connection.execute(
                    """
                    INSERT INTO memory_evidence(
                        id, memory_id, evidence_type, source_id,
                        metadata_json, created_at
                    ) VALUES(?, ?, ?, ?, '{}', ?)
                    """,
                    (str(uuid4()), memory_id, source.value, source_id, now),
                )
            connection.commit()
        return {
            "id": memory_id,
            "status": desired_status.value,
            "deduplicated": False,
            "conflict_id": conflict_id,
        }

    async def memory_records(self, user_qq: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._memory_records_sync, user_qq)

    def _memory_records_sync(self, user_qq: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_records WHERE user_qq = ?
                ORDER BY updated_at DESC
                """,
                (user_qq,),
            ).fetchall()
        return [{**dict(row), "value": json.loads(row["value_json"])} for row in rows]

    async def active_memory_records(self, user_qq: str) -> tuple[MemoryRecord, ...]:
        return await asyncio.to_thread(self._active_memory_records_sync, user_qq)

    def _active_memory_records_sync(self, user_qq: str) -> tuple[MemoryRecord, ...]:
        now = _utc_now()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_records
                WHERE user_qq = ? AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY updated_at DESC
                """,
                (user_qq, now),
            ).fetchall()
        return tuple(
            MemoryRecord(
                id=row["id"],
                user_qq=row["user_qq"],
                kind=MemoryKind(row["memory_kind"]),
                key=row["memory_key"],
                value=json.loads(row["value_json"]),
                source=MemorySource(row["source_type"]),
                confidence=float(row["confidence"]),
                status=MemoryStatus(row["status"]),
                expires_at=(
                    datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
                ),
            )
            for row in rows
        )

    async def forget_memory(self, memory_id: str, *, forgotten_by: str) -> bool:
        return await asyncio.to_thread(self._forget_memory_sync, memory_id, forgotten_by)

    def _forget_memory_sync(self, memory_id: str, forgotten_by: str) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                """
                UPDATE memory_records SET status = 'forgotten', updated_at = ?
                WHERE id = ? AND status != 'forgotten'
                """,
                (now, memory_id),
            )
            if result.rowcount == 1:
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        action, subject_type, subject_id, details_json, created_at
                    )
                    VALUES('memory.forgotten', 'memory', ?, ?, ?)
                    """,
                    (memory_id, _json({"forgotten_by": forgotten_by}), now),
                )
            connection.commit()
            return result.rowcount == 1

    async def pending_memory_conflicts(self, user_qq: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._pending_memory_conflicts_sync, user_qq)

    def _pending_memory_conflicts_sync(self, user_qq: str | None) -> list[dict[str, Any]]:
        query = """
            SELECT memory_conflicts.*,
                   left_record.value_json AS left_value_json,
                   right_record.value_json AS right_value_json
            FROM memory_conflicts
            JOIN memory_records AS left_record
              ON left_record.id = memory_conflicts.left_memory_id
            JOIN memory_records AS right_record
              ON right_record.id = memory_conflicts.right_memory_id
            WHERE memory_conflicts.status = 'pending'
        """
        parameters: tuple[str, ...] = ()
        if user_qq is not None:
            query += " AND memory_conflicts.user_qq = ?"
            parameters = (user_qq,)
        query += " ORDER BY memory_conflicts.created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                **dict(row),
                "left_value": json.loads(row["left_value_json"]),
                "right_value": json.loads(row["right_value_json"]),
            }
            for row in rows
        ]

    async def resolve_memory_conflict(
        self,
        conflict_id: str,
        *,
        resolution: MemoryConflictResolution,
        resolved_by: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._resolve_memory_conflict_sync,
            conflict_id,
            resolution,
            resolved_by,
        )

    def _resolve_memory_conflict_sync(
        self,
        conflict_id: str,
        resolution: MemoryConflictResolution,
        resolved_by: str,
    ) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            conflict = connection.execute(
                "SELECT * FROM memory_conflicts WHERE id = ? AND status = 'pending'",
                (conflict_id,),
            ).fetchone()
            if conflict is None:
                connection.rollback()
                return False
            left_id = conflict["left_memory_id"]
            right_id = conflict["right_memory_id"]
            if resolution is MemoryConflictResolution.KEEP_LEFT:
                active_id, inactive_id = left_id, right_id
                connection.execute(
                    "UPDATE memory_records SET status = 'active', updated_at = ? WHERE id = ?",
                    (now, active_id),
                )
                connection.execute(
                    "UPDATE memory_records SET status = 'superseded', updated_at = ? WHERE id = ?",
                    (now, inactive_id),
                )
            elif resolution is MemoryConflictResolution.KEEP_RIGHT:
                active_id, inactive_id = right_id, left_id
                connection.execute(
                    "UPDATE memory_records SET status = 'active', updated_at = ? WHERE id = ?",
                    (now, active_id),
                )
                connection.execute(
                    "UPDATE memory_records SET status = 'superseded', updated_at = ? WHERE id = ?",
                    (now, inactive_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE memory_records SET status = 'forgotten', updated_at = ?
                    WHERE id IN (?, ?)
                    """,
                    (now, left_id, right_id),
                )
            connection.execute(
                """
                UPDATE memory_conflicts
                SET status = 'resolved', resolved_by = ?, resolution = ?, resolved_at = ?
                WHERE id = ?
                """,
                (resolved_by, resolution.value, now, conflict_id),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('memory.conflict_resolved', 'memory_conflict', ?, ?, ?)
                """,
                (
                    conflict_id,
                    _json({"resolution": resolution.value, "resolved_by": resolved_by}),
                    now,
                ),
            )
            connection.commit()
            return True

    async def create_approval(
        self,
        *,
        approval_id: str,
        request_type: str,
        subject_id: str,
        approval_code: str,
        requested_to: str,
        payload: dict[str, Any],
        lifetime_minutes: int = 30,
    ) -> None:
        await asyncio.to_thread(
            self._create_approval_sync,
            approval_id,
            request_type,
            subject_id,
            approval_code,
            requested_to,
            payload,
            lifetime_minutes,
        )

    def _create_approval_sync(
        self,
        approval_id: str,
        request_type: str,
        subject_id: str,
        approval_code: str,
        requested_to: str,
        payload: dict[str, Any],
        lifetime_minutes: int,
    ) -> None:
        created = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO approval_requests(
                    id, request_type, subject_id, approval_code, status,
                    requested_to, expires_at, created_at
                ) VALUES(?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    approval_id,
                    request_type,
                    subject_id,
                    approval_code,
                    requested_to,
                    (created + timedelta(minutes=lifetime_minutes)).isoformat(),
                    created.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO approval_payloads(approval_id, payload_json) VALUES(?, ?)",
                (approval_id, _json(payload)),
            )
            connection.commit()

    async def approval_by_code(self, code: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._approval_by_code_sync, code)

    def _approval_by_code_sync(self, code: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT approval_requests.*, approval_payloads.payload_json
                FROM approval_requests
                JOIN approval_payloads ON approval_payloads.approval_id = approval_requests.id
                WHERE approval_code = ?
                """,
                (code,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    async def decide_approval(self, approval_id: str, status: ApprovalStatus) -> None:
        await asyncio.to_thread(self._decide_approval_sync, approval_id, status)

    def _decide_approval_sync(self, approval_id: str, status: ApprovalStatus) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE approval_requests SET status = ?, decided_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (status.value, _utc_now(), approval_id),
            )

    async def pending_approvals(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._pending_approvals_sync)

    def _pending_approvals_sync(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT approval_requests.id, request_type, subject_id, approval_code,
                       requested_to, expires_at, created_at,
                       approval_payloads.payload_json
                FROM approval_requests
                JOIN approval_payloads
                  ON approval_payloads.approval_id = approval_requests.id
                WHERE status = 'pending'
                ORDER BY created_at ASC
                """
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    async def ops_current_counts(self) -> dict[str, int]:
        return await asyncio.to_thread(self._ops_current_counts_sync)

    def _ops_current_counts_sync(self) -> dict[str, int]:
        with self._connect() as connection:

            def count(sql: str, params: tuple[Any, ...] = ()) -> int:
                return int(connection.execute(sql, params).fetchone()[0])

            return {
                "pending_approvals": count(
                    "SELECT COUNT(*) FROM approval_requests WHERE status = 'pending'"
                ),
                "urgent_reports": count(
                    """
                    SELECT COUNT(*) FROM owner_reports
                    WHERE status = 'pending' AND severity IN ('action_required', 'critical')
                    """
                ),
                "outbox_pending": count(
                    "SELECT COUNT(*) FROM outbox WHERE status IN ('pending', 'sending')"
                ),
                "outbox_failed": count("SELECT COUNT(*) FROM outbox WHERE status = 'failed'"),
                "qzone_publishing": count(
                    "SELECT COUNT(*) FROM qzone_posts WHERE status IN ('publishing', 'deleting')"
                ),
                "qzone_uncertain": count(
                    """
                    SELECT COUNT(*) FROM qzone_posts
                    WHERE status IN ('delivery_uncertain', 'delete_uncertain')
                    """
                ),
                "qzone_pending_delete": count(
                    """
                    SELECT COUNT(*) FROM qzone_posts
                    WHERE status IN ('pending_delete_approval', 'delete_approved')
                    """
                ),
                "report_delivery_failed": count(
                    """
                    SELECT COUNT(*) FROM owner_report_runtime
                    WHERE delivery_status = 'failed'
                    """
                ),
            }

    async def ops_window_counts(self, start_iso: str, end_iso: str) -> dict[str, int]:
        return await asyncio.to_thread(self._ops_window_counts_sync, start_iso, end_iso)

    def _ops_window_counts_sync(self, start_iso: str, end_iso: str) -> dict[str, int]:
        bounds = (start_iso, end_iso)
        with self._connect() as connection:

            def count(sql: str, params: tuple[Any, ...] = bounds) -> int:
                return int(connection.execute(sql, params).fetchone()[0])

            return {
                "inbound": count(
                    """
                    SELECT COUNT(*) FROM messages
                    WHERE direction = 'inbound' AND created_at >= ? AND created_at < ?
                    """
                ),
                "commands": count(
                    """
                    SELECT COUNT(*) FROM control_commands
                    WHERE created_at >= ? AND created_at < ?
                    """
                ),
                "approvals": count(
                    """
                    SELECT COUNT(*) FROM approval_requests
                    WHERE status IN ('approved', 'rejected')
                      AND decided_at IS NOT NULL
                      AND decided_at >= ? AND decided_at < ?
                    """
                ),
                "shadow_completed": count(
                    """
                    SELECT COUNT(*) FROM inference_runs
                    WHERE status = 'completed' AND created_at >= ? AND created_at < ?
                    """
                ),
                "shadow_failed": count(
                    """
                    SELECT COUNT(*) FROM inference_runs
                    WHERE status = 'failed' AND created_at >= ? AND created_at < ?
                    """
                ),
                "shadow_skipped": 0,
            }

    async def ops_activity_rows(self, limit: int) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._ops_activity_rows_sync, limit)

    def _ops_activity_rows_sync(self, limit: int) -> list[dict[str, Any]]:
        fetch = max(1, min(limit, 50)) * 3
        with self._connect() as connection:
            audits = connection.execute(
                """
                SELECT action, subject_type, subject_id, created_at
                FROM audit_log
                ORDER BY created_at DESC LIMIT ?
                """,
                (fetch,),
            ).fetchall()
            commands = connection.execute(
                """
                SELECT id, action, actor_qq, status, created_at
                FROM control_commands
                ORDER BY created_at DESC LIMIT ?
                """,
                (fetch,),
            ).fetchall()
            reports = connection.execute(
                """
                SELECT id, title, severity, status, created_at
                FROM owner_reports
                ORDER BY created_at DESC LIMIT ?
                """,
                (fetch,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in audits:
            items.append(
                {
                    "source": "audit",
                    "subject_type": row["subject_type"],
                    "at": row["created_at"],
                    "title": row["action"],
                    "detail": f"{row['subject_type']}:{row['subject_id']}",
                }
            )
        for row in commands:
            items.append(
                {
                    "source": "control_command",
                    "subject_type": "control",
                    "at": row["created_at"],
                    "title": row["action"],
                    "detail": f"{row['actor_qq']} · {row['status']}",
                }
            )
        for row in reports:
            items.append(
                {
                    "source": "owner_report",
                    "subject_type": "owner_report",
                    "at": row["created_at"],
                    "title": row["title"],
                    "detail": f"{row['severity']} · {row['status']}",
                }
            )
        items.sort(key=lambda item: str(item["at"]), reverse=True)
        return items[: max(1, min(limit, 50))]

    async def stage_friend_baseline_candidate(
        self,
        *,
        candidate_id: str,
        bot_qq: str,
        friend_ids: Iterable[str],
        lifetime_minutes: int = 30,
    ) -> dict[str, Any]:
        normalized = tuple(
            sorted({str(value).strip() for value in friend_ids if str(value).strip()})
        )
        return await asyncio.to_thread(
            self._stage_friend_baseline_candidate_sync,
            candidate_id,
            bot_qq,
            normalized,
            lifetime_minutes,
        )

    def _stage_friend_baseline_candidate_sync(
        self,
        candidate_id: str,
        bot_qq: str,
        friend_ids: tuple[str, ...],
        lifetime_minutes: int,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        snapshot_hash = hashlib.sha256("\n".join(friend_ids).encode()).hexdigest()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO friend_baseline_candidates(
                    id, bot_qq, friend_ids_json, friend_count, snapshot_hash,
                    fetched_at, expires_at, status, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?)
                """,
                (
                    candidate_id,
                    bot_qq,
                    _json(friend_ids),
                    len(friend_ids),
                    snapshot_hash,
                    now.isoformat(),
                    (now + timedelta(minutes=lifetime_minutes)).isoformat(),
                    now.isoformat(),
                ),
            )
        return {
            "id": candidate_id,
            "friend_count": len(friend_ids),
            "snapshot_hash": snapshot_hash,
            "fetched_at": now.isoformat(),
        }

    async def baseline_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._baseline_candidate_sync, candidate_id)

    def _baseline_candidate_sync(self, candidate_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM friend_baseline_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["friend_ids"] = json.loads(result.pop("friend_ids_json"))
        return result

    async def finish_baseline_candidate(self, candidate_id: str, status: str) -> None:
        await asyncio.to_thread(self._finish_baseline_candidate_sync, candidate_id, status)

    def _finish_baseline_candidate_sync(self, candidate_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE friend_baseline_candidates SET status = ? WHERE id = ?",
                (status, candidate_id),
            )

    async def create_privacy_request(
        self,
        *,
        request_id: str,
        user_qq: str,
        request_kind: str,
        requested_by: str,
        reason: str,
    ) -> None:
        await asyncio.to_thread(
            self._create_privacy_request_sync,
            request_id,
            user_qq,
            request_kind,
            requested_by,
            reason,
        )

    def _create_privacy_request_sync(
        self,
        request_id: str,
        user_qq: str,
        request_kind: str,
        requested_by: str,
        reason: str,
    ) -> None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO privacy_requests(
                    id, user_qq, request_kind, status,
                    requested_by, reason, created_at
                ) VALUES(?, ?, ?, 'pending', ?, ?, ?)
                """,
                (request_id, user_qq, request_kind, requested_by, reason, now),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('privacy.request_created', 'privacy_request', ?, ?, ?)
                """,
                (
                    request_id,
                    _json({"user_qq": user_qq, "request_kind": request_kind}),
                    now,
                ),
            )

    async def privacy_requests(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._privacy_requests_sync, status)

    def _privacy_requests_sync(self, status: str | None) -> list[dict[str, Any]]:
        query = "SELECT * FROM privacy_requests"
        parameters: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw_result = item.pop("result_json")
            item["result"] = json.loads(raw_result) if raw_result else None
            result.append(item)
        return result

    async def privacy_request(self, request_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._privacy_request_sync, request_id)

    def _privacy_request_sync(self, request_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM privacy_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
        return dict(row) if row else None

    async def privacy_artifacts(self, request_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._privacy_artifacts_sync, request_id)

    def _privacy_artifacts_sync(self, request_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT request_id, artifact_type, path, sha256, byte_count, created_at
                FROM privacy_job_artifacts
                WHERE request_id = ?
                ORDER BY CASE artifact_type
                    WHEN 'pre_delete_backup' THEN 0
                    WHEN 'export' THEN 0
                    ELSE 1 END,
                    created_at DESC
                """,
                (request_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    async def data_access_audit(
        self,
        *,
        user_qq: str | None = None,
        decision: str | None = None,
        data_class: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._data_access_audit_sync,
            user_qq,
            decision,
            data_class,
            limit,
        )

    def _data_access_audit_sync(
        self,
        user_qq: str | None,
        decision: str | None,
        data_class: str | None,
        limit: int,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        parameters: list[str] = []
        if user_qq:
            clauses.append("user_qq = ?")
            parameters.append(user_qq)
        if decision:
            clauses.append("decision = ?")
            parameters.append(decision)
        if data_class:
            clauses.append("data_class = ?")
            parameters.append(data_class)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        safe_limit = max(1, min(limit, 500))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, user_qq, data_class, purpose, accessor,
                       decision, policy_snapshot_json, created_at
                FROM data_access_log{where}
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (*parameters, safe_limit),
            ).fetchall()
            counts = connection.execute(
                f"""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN decision = 'allowed' THEN 1 ELSE 0 END) AS allowed,
                       SUM(CASE WHEN decision = 'denied' THEN 1 ELSE 0 END) AS denied
                FROM data_access_log{where}
                """,
                tuple(parameters),
            ).fetchone()
            class_rows = connection.execute(
                f"""
                SELECT data_class, COUNT(*) AS count
                FROM data_access_log{where}
                GROUP BY data_class ORDER BY count DESC, data_class
                """,
                tuple(parameters),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["policy"] = json.loads(item.pop("policy_snapshot_json"))
            items.append(item)
        return {
            "items": items,
            "summary": {
                "total": int(counts["total"] or 0),
                "allowed": int(counts["allowed"] or 0),
                "denied": int(counts["denied"] or 0),
                "data_classes": {str(row["data_class"]): int(row["count"]) for row in class_rows},
            },
        }

    async def claim_privacy_request(self, request_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._claim_privacy_request_sync, request_id)

    def _claim_privacy_request_sync(self, request_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                """
                UPDATE privacy_requests SET status = 'running'
                WHERE id = ? AND status = 'pending'
                """,
                (request_id,),
            )
            if result.rowcount != 1:
                connection.rollback()
                return None
            row = connection.execute(
                "SELECT * FROM privacy_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            connection.commit()
        return dict(row) if row else None

    async def finish_privacy_request(
        self,
        request_id: str,
        *,
        status: str,
        result: dict[str, Any],
    ) -> None:
        await asyncio.to_thread(
            self._finish_privacy_request_sync,
            request_id,
            status,
            result,
        )

    def _finish_privacy_request_sync(
        self,
        request_id: str,
        status: str,
        result: dict[str, Any],
    ) -> None:
        completed_at = _utc_now() if status in {"completed", "failed"} else None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE privacy_requests
                SET status = ?, completed_at = ?, result_json = ?
                WHERE id = ?
                """,
                (status, completed_at, _json(result), request_id),
            )

    async def privacy_export_snapshot(self, user_qq: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._privacy_export_snapshot_sync, user_qq)

    def _privacy_export_snapshot_sync(self, user_qq: str) -> dict[str, Any]:
        with self._connect() as connection:

            def rows(query: str, parameters: tuple[str, ...]) -> list[dict[str, Any]]:
                return [dict(row) for row in connection.execute(query, parameters).fetchall()]

            return {
                "user": rows("SELECT * FROM users WHERE qq_id = ?", (user_qq,)),
                "relationship": rows(
                    "SELECT * FROM user_relationships WHERE user_qq = ?", (user_qq,)
                ),
                "history_policy": rows(
                    "SELECT * FROM history_access_policies WHERE user_qq = ?", (user_qq,)
                ),
                "qzone_profile_access_policies": rows(
                    "SELECT * FROM qzone_profile_access_policies WHERE user_qq = ?",
                    (user_qq,),
                ),
                "qzone_profile_snapshots": rows(
                    """
                    SELECT * FROM qzone_profile_snapshots
                    WHERE user_qq = ? ORDER BY fetched_at ASC
                    """,
                    (user_qq,),
                ),
                "privacy_policies": rows(
                    """
                    SELECT * FROM privacy_scope_policies
                    WHERE scope_type = 'user' AND scope_id = ?
                    """,
                    (user_qq,),
                ),
                "identity_evidence": rows(
                    "SELECT * FROM identity_evidence WHERE user_qq = ?", (user_qq,)
                ),
                "user_reply_style_policy": rows(
                    "SELECT * FROM user_reply_style_policies WHERE user_qq = ?",
                    (user_qq,),
                ),
                "proactive_user_policy": rows(
                    "SELECT * FROM proactive_user_policies WHERE user_qq = ?",
                    (user_qq,),
                ),
                "proactive_message_tasks": rows(
                    """
                    SELECT * FROM proactive_message_tasks
                    WHERE target_qq = ? ORDER BY original_scheduled_for ASC
                    """,
                    (user_qq,),
                ),
                "proactive_delivery_events": rows(
                    """
                    SELECT proactive_delivery_events.* FROM proactive_delivery_events
                    JOIN proactive_message_tasks
                      ON proactive_message_tasks.id = proactive_delivery_events.task_id
                    WHERE proactive_message_tasks.target_qq = ?
                    ORDER BY proactive_delivery_events.occurred_at ASC
                    """,
                    (user_qq,),
                ),
                "proactive_approval_requests": rows(
                    """
                    SELECT approval_requests.* FROM approval_requests
                    JOIN proactive_message_tasks
                      ON proactive_message_tasks.id = approval_requests.subject_id
                    WHERE approval_requests.request_type LIKE 'proactive_message.%'
                      AND proactive_message_tasks.target_qq = ?
                    """,
                    (user_qq,),
                ),
                "proactive_approval_payloads": rows(
                    """
                    SELECT approval_payloads.* FROM approval_payloads
                    JOIN approval_requests
                      ON approval_requests.id = approval_payloads.approval_id
                    JOIN proactive_message_tasks
                      ON proactive_message_tasks.id = approval_requests.subject_id
                    WHERE approval_requests.request_type LIKE 'proactive_message.%'
                      AND proactive_message_tasks.target_qq = ?
                    """,
                    (user_qq,),
                ),
                "proactive_owner_reports": rows(
                    """
                    SELECT owner_reports.* FROM owner_reports
                    JOIN proactive_message_tasks
                      ON proactive_message_tasks.id = owner_reports.related_id
                    WHERE owner_reports.related_type = 'proactive_task'
                      AND proactive_message_tasks.target_qq = ?
                    """,
                    (user_qq,),
                ),
                "qzone_posts": rows(
                    """
                    SELECT * FROM qzone_posts
                    WHERE EXISTS (
                        SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                        WHERE CAST(json_each.value AS TEXT) = ?
                    )
                    ORDER BY created_at ASC
                    """,
                    (user_qq,),
                ),
                "qzone_post_runtime": rows(
                    """
                    SELECT qzone_post_runtime.* FROM qzone_post_runtime
                    JOIN qzone_posts ON qzone_posts.id = qzone_post_runtime.post_id
                    WHERE EXISTS (
                        SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                        WHERE CAST(json_each.value AS TEXT) = ?
                    )
                    """,
                    (user_qq,),
                ),
                "qzone_post_events": rows(
                    """
                    SELECT qzone_post_events.* FROM qzone_post_events
                    JOIN qzone_posts ON qzone_posts.id = qzone_post_events.post_id
                    WHERE EXISTS (
                        SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                        WHERE CAST(json_each.value AS TEXT) = ?
                    )
                    ORDER BY qzone_post_events.occurred_at ASC
                    """,
                    (user_qq,),
                ),
                "qzone_approval_requests": rows(
                    """
                    SELECT approval_requests.* FROM approval_requests
                    JOIN qzone_posts ON qzone_posts.id = approval_requests.subject_id
                    WHERE approval_requests.request_type LIKE 'qzone.%'
                      AND EXISTS (
                          SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                          WHERE CAST(json_each.value AS TEXT) = ?
                      )
                    """,
                    (user_qq,),
                ),
                "qzone_approval_payloads": rows(
                    """
                    SELECT approval_payloads.* FROM approval_payloads
                    JOIN approval_requests
                      ON approval_requests.id = approval_payloads.approval_id
                    JOIN qzone_posts ON qzone_posts.id = approval_requests.subject_id
                    WHERE approval_requests.request_type LIKE 'qzone.%'
                      AND EXISTS (
                          SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                          WHERE CAST(json_each.value AS TEXT) = ?
                      )
                    """,
                    (user_qq,),
                ),
                "qzone_owner_reports": rows(
                    """
                    SELECT owner_reports.* FROM owner_reports
                    JOIN qzone_posts ON qzone_posts.id = owner_reports.related_id
                    WHERE owner_reports.related_type = 'qzone_post'
                      AND EXISTS (
                          SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                          WHERE CAST(json_each.value AS TEXT) = ?
                      )
                    """,
                    (user_qq,),
                ),
                "qzone_audit_log": rows(
                    """
                    SELECT audit_log.* FROM audit_log
                    JOIN qzone_posts ON qzone_posts.id = audit_log.subject_id
                    WHERE audit_log.subject_type = 'qzone_post'
                      AND EXISTS (
                          SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                          WHERE CAST(json_each.value AS TEXT) = ?
                      )
                    """,
                    (user_qq,),
                ),
                "data_access_log": rows(
                    "SELECT * FROM data_access_log WHERE user_qq = ?", (user_qq,)
                ),
                "knowledge_documents": rows(
                    "SELECT * FROM knowledge_documents WHERE subject_user_qq = ?",
                    (user_qq,),
                ),
                "document_blobs": rows(
                    """
                    SELECT document_blobs.* FROM document_blobs
                    JOIN knowledge_documents
                      ON knowledge_documents.id = document_blobs.document_id
                    WHERE knowledge_documents.subject_user_qq = ?
                    """,
                    (user_qq,),
                ),
                "document_chunks": rows(
                    """
                    SELECT document_chunks.* FROM document_chunks
                    JOIN knowledge_documents
                      ON knowledge_documents.id = document_chunks.document_id
                    WHERE knowledge_documents.subject_user_qq = ?
                    ORDER BY document_chunks.document_id, document_chunks.chunk_index
                    """,
                    (user_qq,),
                ),
                "knowledge_processing_jobs": rows(
                    "SELECT * FROM knowledge_processing_jobs WHERE user_qq = ?",
                    (user_qq,),
                ),
                "knowledge_job_checkpoints": rows(
                    """
                    SELECT knowledge_job_checkpoints.* FROM knowledge_job_checkpoints
                    JOIN knowledge_processing_jobs
                      ON knowledge_processing_jobs.id = knowledge_job_checkpoints.job_id
                    WHERE knowledge_processing_jobs.user_qq = ?
                    """,
                    (user_qq,),
                ),
                "knowledge_chunk_analyses": rows(
                    """
                    SELECT knowledge_chunk_analyses.* FROM knowledge_chunk_analyses
                    JOIN knowledge_processing_jobs
                      ON knowledge_processing_jobs.id = knowledge_chunk_analyses.job_id
                    WHERE knowledge_processing_jobs.user_qq = ?
                    ORDER BY knowledge_chunk_analyses.job_id,
                             knowledge_chunk_analyses.chunk_index
                    """,
                    (user_qq,),
                ),
                "knowledge_approval_requests": rows(
                    """
                    SELECT approval_requests.* FROM approval_requests
                    JOIN knowledge_processing_jobs
                      ON knowledge_processing_jobs.id = approval_requests.subject_id
                    WHERE approval_requests.request_type = 'knowledge.preview'
                      AND knowledge_processing_jobs.user_qq = ?
                    """,
                    (user_qq,),
                ),
                "knowledge_approval_payloads": rows(
                    """
                    SELECT approval_payloads.* FROM approval_payloads
                    JOIN approval_requests
                      ON approval_requests.id = approval_payloads.approval_id
                    JOIN knowledge_processing_jobs
                      ON knowledge_processing_jobs.id = approval_requests.subject_id
                    WHERE approval_requests.request_type = 'knowledge.preview'
                      AND knowledge_processing_jobs.user_qq = ?
                    """,
                    (user_qq,),
                ),
                "knowledge_owner_reports": rows(
                    """
                    SELECT owner_reports.* FROM owner_reports
                    JOIN knowledge_processing_jobs
                      ON knowledge_processing_jobs.id = owner_reports.related_id
                    WHERE owner_reports.related_type = 'knowledge_job'
                      AND knowledge_processing_jobs.user_qq = ?
                    """,
                    (user_qq,),
                ),
                "owner_report_runtime": rows(
                    """
                    SELECT owner_report_runtime.* FROM owner_report_runtime
                    JOIN owner_reports
                      ON owner_reports.id = owner_report_runtime.report_id
                    WHERE owner_reports.related_id = ?
                       OR (
                           owner_reports.related_type = 'knowledge_job'
                           AND owner_reports.related_id IN (
                               SELECT id FROM knowledge_processing_jobs WHERE user_qq = ?
                           )
                       )
                       OR (
                           owner_reports.related_type = 'proactive_task'
                           AND owner_reports.related_id IN (
                               SELECT id FROM proactive_message_tasks WHERE target_qq = ?
                           )
                       )
                       OR (
                           owner_reports.related_type = 'qzone_post'
                           AND owner_reports.related_id IN (
                               SELECT id FROM qzone_posts
                               WHERE EXISTS (
                                   SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                                   WHERE CAST(json_each.value AS TEXT) = ?
                               )
                           )
                       )
                    """,
                    (user_qq, user_qq, user_qq, user_qq),
                ),
                "owner_report_events": rows(
                    """
                    SELECT owner_report_events.* FROM owner_report_events
                    JOIN owner_reports
                      ON owner_reports.id = owner_report_events.report_id
                    WHERE owner_reports.related_id = ?
                       OR (
                           owner_reports.related_type = 'knowledge_job'
                           AND owner_reports.related_id IN (
                               SELECT id FROM knowledge_processing_jobs WHERE user_qq = ?
                           )
                       )
                       OR (
                           owner_reports.related_type = 'proactive_task'
                           AND owner_reports.related_id IN (
                               SELECT id FROM proactive_message_tasks WHERE target_qq = ?
                           )
                       )
                       OR (
                           owner_reports.related_type = 'qzone_post'
                           AND owner_reports.related_id IN (
                               SELECT id FROM qzone_posts
                               WHERE EXISTS (
                                   SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                                   WHERE CAST(json_each.value AS TEXT) = ?
                               )
                           )
                       )
                    ORDER BY owner_report_events.occurred_at ASC
                    """,
                    (user_qq, user_qq, user_qq, user_qq),
                ),
                "user_understanding_profiles": rows(
                    "SELECT * FROM user_understanding_profiles WHERE user_qq = ?",
                    (user_qq,),
                ),
                "persona_profiles": rows(
                    """
                    SELECT * FROM persona_profiles
                    WHERE scope_type = 'private_user' AND scope_id = ?
                    """,
                    (user_qq,),
                ),
                "persona_profile_evidence": rows(
                    """
                    SELECT persona_profile_evidence.* FROM persona_profile_evidence
                    JOIN persona_profiles
                      ON persona_profiles.id = persona_profile_evidence.profile_id
                    WHERE persona_profiles.scope_type = 'private_user'
                      AND persona_profiles.scope_id = ?
                    """,
                    (user_qq,),
                ),
                "memory_records": rows(
                    "SELECT * FROM memory_records WHERE user_qq = ?",
                    (user_qq,),
                ),
                "memory_conflicts": rows(
                    "SELECT * FROM memory_conflicts WHERE user_qq = ?",
                    (user_qq,),
                ),
                "messages": rows(
                    """
                    SELECT * FROM messages
                    WHERE sender_id = ?
                       OR conversation_key IN (
                           SELECT conversation_key FROM conversations
                           WHERE kind = 'private' AND peer_id = ?
                       )
                    ORDER BY occurred_at ASC
                    """,
                    (user_qq, user_qq),
                ),
                "inference_runs": rows(
                    """
                    SELECT * FROM inference_runs
                    WHERE actor_qq = ?
                       OR conversation_key IN (
                           SELECT conversation_key FROM conversations
                           WHERE kind = 'private' AND peer_id = ?
                       )
                    ORDER BY created_at ASC
                    """,
                    (user_qq, user_qq),
                ),
                "reply_candidates": rows(
                    "SELECT * FROM reply_candidates WHERE target_id = ?",
                    (user_qq,),
                ),
                "daily_summaries": rows(
                    """
                    SELECT * FROM daily_summaries
                    WHERE EXISTS (
                        SELECT 1 FROM json_each(daily_summaries.source_peer_ids_json)
                        WHERE CAST(json_each.value AS TEXT) = ?
                    )
                       OR EXISTS (
                           SELECT 1 FROM json_each(daily_summaries.entries_json)
                           WHERE CAST(json_extract(json_each.value, '$.alias') AS TEXT) = ?
                       )
                    ORDER BY day_key ASC
                    """,
                    (user_qq, user_qq),
                ),
                "linked_control_commands": rows(
                    """
                    SELECT control_commands.* FROM control_commands
                    WHERE id IN (
                        SELECT record_id FROM privacy_subject_links
                        WHERE subject_user_qq = ? AND record_type = 'control_command'
                    )
                    ORDER BY created_at ASC
                    """,
                    (user_qq,),
                ),
                "linked_approval_requests": rows(
                    """
                    SELECT approval_requests.* FROM approval_requests
                    WHERE subject_id = ?
                       OR id IN (
                           SELECT record_id FROM privacy_subject_links
                           WHERE subject_user_qq = ? AND record_type = 'approval_request'
                       )
                    ORDER BY created_at ASC
                    """,
                    (user_qq, user_qq),
                ),
                "linked_audit_log": rows(
                    """
                    SELECT audit_log.* FROM audit_log
                    WHERE (subject_type = 'user' AND subject_id = ?)
                       OR CAST(id AS TEXT) IN (
                           SELECT record_id FROM privacy_subject_links
                           WHERE subject_user_qq = ? AND record_type = 'audit_log'
                       )
                    ORDER BY created_at ASC
                    """,
                    (user_qq, user_qq),
                ),
            }

    async def privacy_delete_impact(self, user_qq: str) -> dict[str, int]:
        return await asyncio.to_thread(self._privacy_delete_impact_sync, user_qq)

    def _privacy_delete_impact_sync(self, user_qq: str) -> dict[str, int]:
        queries = {
            "users": ("SELECT COUNT(*) FROM users WHERE qq_id = ?", (user_qq,)),
            "relationships": (
                "SELECT COUNT(*) FROM user_relationships WHERE user_qq = ?",
                (user_qq,),
            ),
            "identity_evidence": (
                "SELECT COUNT(*) FROM identity_evidence WHERE user_qq = ?",
                (user_qq,),
            ),
            "user_reply_style_policies": (
                "SELECT COUNT(*) FROM user_reply_style_policies WHERE user_qq = ?",
                (user_qq,),
            ),
            "proactive_user_policies": (
                "SELECT COUNT(*) FROM proactive_user_policies WHERE user_qq = ?",
                (user_qq,),
            ),
            "proactive_message_tasks": (
                "SELECT COUNT(*) FROM proactive_message_tasks WHERE target_qq = ?",
                (user_qq,),
            ),
            "proactive_delivery_events": (
                """
                SELECT COUNT(*) FROM proactive_delivery_events
                WHERE task_id IN (
                    SELECT id FROM proactive_message_tasks WHERE target_qq = ?
                )
                """,
                (user_qq,),
            ),
            "history_policies": (
                "SELECT COUNT(*) FROM history_access_policies WHERE user_qq = ?",
                (user_qq,),
            ),
            "qzone_profile_access_policies": (
                "SELECT COUNT(*) FROM qzone_profile_access_policies WHERE user_qq = ?",
                (user_qq,),
            ),
            "qzone_profile_snapshots": (
                "SELECT COUNT(*) FROM qzone_profile_snapshots WHERE user_qq = ?",
                (user_qq,),
            ),
            "privacy_policies": (
                """
                SELECT COUNT(*) FROM privacy_scope_policies
                WHERE scope_type = 'user' AND scope_id = ?
                """,
                (user_qq,),
            ),
            "data_access_log": (
                "SELECT COUNT(*) FROM data_access_log WHERE user_qq = ?",
                (user_qq,),
            ),
            "knowledge_documents": (
                "SELECT COUNT(*) FROM knowledge_documents WHERE subject_user_qq = ?",
                (user_qq,),
            ),
            "document_chunks": (
                """
                SELECT COUNT(*) FROM document_chunks
                WHERE document_id IN (
                    SELECT id FROM knowledge_documents WHERE subject_user_qq = ?
                )
                """,
                (user_qq,),
            ),
            "knowledge_processing_jobs": (
                "SELECT COUNT(*) FROM knowledge_processing_jobs WHERE user_qq = ?",
                (user_qq,),
            ),
            "knowledge_job_checkpoints": (
                """
                SELECT COUNT(*) FROM knowledge_job_checkpoints
                WHERE job_id IN (
                    SELECT id FROM knowledge_processing_jobs WHERE user_qq = ?
                )
                """,
                (user_qq,),
            ),
            "knowledge_chunk_analyses": (
                """
                SELECT COUNT(*) FROM knowledge_chunk_analyses
                WHERE job_id IN (
                    SELECT id FROM knowledge_processing_jobs WHERE user_qq = ?
                )
                """,
                (user_qq,),
            ),
            "user_understanding_profiles": (
                "SELECT COUNT(*) FROM user_understanding_profiles WHERE user_qq = ?",
                (user_qq,),
            ),
            "persona_profiles": (
                """
                SELECT COUNT(*) FROM persona_profiles
                WHERE scope_type = 'private_user' AND scope_id = ?
                """,
                (user_qq,),
            ),
            "persona_profile_evidence": (
                """
                SELECT COUNT(*) FROM persona_profile_evidence
                WHERE profile_id IN (
                    SELECT id FROM persona_profiles
                    WHERE scope_type = 'private_user' AND scope_id = ?
                )
                """,
                (user_qq,),
            ),
            "memory_records": (
                "SELECT COUNT(*) FROM memory_records WHERE user_qq = ?",
                (user_qq,),
            ),
            "memory_conflicts": (
                "SELECT COUNT(*) FROM memory_conflicts WHERE user_qq = ?",
                (user_qq,),
            ),
            "messages": (
                """
                SELECT COUNT(*) FROM messages
                WHERE sender_id = ?
                   OR conversation_key IN (
                       SELECT conversation_key FROM conversations
                       WHERE kind = 'private' AND peer_id = ?
                   )
                """,
                (user_qq, user_qq),
            ),
            "inference_runs": (
                """
                SELECT COUNT(*) FROM inference_runs
                WHERE actor_qq = ?
                   OR conversation_key IN (
                       SELECT conversation_key FROM conversations
                       WHERE kind = 'private' AND peer_id = ?
                   )
                """,
                (user_qq, user_qq),
            ),
            "reply_candidates": (
                "SELECT COUNT(*) FROM reply_candidates WHERE target_id = ?",
                (user_qq,),
            ),
            "outbox": ("SELECT COUNT(*) FROM outbox WHERE target_id = ?", (user_qq,)),
            "daily_summaries": (
                """
                SELECT COUNT(*) FROM daily_summaries
                WHERE EXISTS (
                    SELECT 1 FROM json_each(daily_summaries.source_peer_ids_json)
                    WHERE CAST(json_each.value AS TEXT) = ?
                )
                   OR EXISTS (
                       SELECT 1 FROM json_each(daily_summaries.entries_json)
                       WHERE CAST(json_extract(json_each.value, '$.alias') AS TEXT) = ?
                   )
                """,
                (user_qq, user_qq),
            ),
            "privacy_requests": (
                "SELECT COUNT(*) FROM privacy_requests WHERE user_qq = ?",
                (user_qq,),
            ),
            "control_commands": (
                """
                SELECT COUNT(*) FROM control_commands
                WHERE id IN (
                    SELECT record_id FROM privacy_subject_links
                    WHERE subject_user_qq = ? AND record_type = 'control_command'
                )
                """,
                (user_qq,),
            ),
            "approval_requests": (
                """
                SELECT COUNT(DISTINCT approval_requests.id)
                FROM approval_requests
                LEFT JOIN approval_payloads
                  ON approval_payloads.approval_id = approval_requests.id
                WHERE approval_requests.subject_id = ?
                   OR approval_requests.id IN (
                       SELECT record_id FROM privacy_subject_links
                       WHERE subject_user_qq = ? AND record_type = 'approval_request'
                   )
                   OR (
                       approval_requests.request_type = 'knowledge.preview'
                       AND approval_requests.subject_id IN (
                           SELECT id FROM knowledge_processing_jobs WHERE user_qq = ?
                       )
                   )
                   OR (
                       approval_requests.request_type LIKE 'proactive_message.%'
                       AND approval_requests.subject_id IN (
                           SELECT id FROM proactive_message_tasks WHERE target_qq = ?
                       )
                   )
                   OR (
                       approval_requests.request_type LIKE 'qzone.%'
                       AND approval_requests.subject_id IN (
                           SELECT id FROM qzone_posts
                           WHERE EXISTS (
                               SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                               WHERE CAST(json_each.value AS TEXT) = ?
                           )
                       )
                   )
                """,
                (user_qq, user_qq, user_qq, user_qq, user_qq),
            ),
            "audit_log": (
                """
                SELECT COUNT(*) FROM audit_log
                WHERE (subject_type = 'user' AND subject_id = ?)
                   OR CAST(id AS TEXT) IN (
                       SELECT record_id FROM privacy_subject_links
                       WHERE subject_user_qq = ? AND record_type = 'audit_log'
                   )
                """,
                (user_qq, user_qq),
            ),
            "owner_reports": (
                """
                SELECT COUNT(*) FROM owner_reports
                WHERE related_id = ?
                   OR (
                       related_type = 'knowledge_job'
                       AND related_id IN (
                           SELECT id FROM knowledge_processing_jobs WHERE user_qq = ?
                       )
                   )
                   OR (
                       related_type = 'proactive_task'
                       AND related_id IN (
                           SELECT id FROM proactive_message_tasks WHERE target_qq = ?
                       )
                   )
                   OR (
                       related_type = 'qzone_post'
                       AND related_id IN (
                           SELECT id FROM qzone_posts
                           WHERE EXISTS (
                               SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                               WHERE CAST(json_each.value AS TEXT) = ?
                           )
                       )
                   )
                """,
                (user_qq, user_qq, user_qq, user_qq),
            ),
            "owner_report_runtime": (
                """
                SELECT COUNT(*) FROM owner_report_runtime
                WHERE report_id IN (
                    SELECT id FROM owner_reports
                    WHERE related_id = ?
                       OR (
                           related_type = 'knowledge_job'
                           AND related_id IN (
                               SELECT id FROM knowledge_processing_jobs WHERE user_qq = ?
                           )
                       )
                       OR (
                           related_type = 'proactive_task'
                           AND related_id IN (
                               SELECT id FROM proactive_message_tasks WHERE target_qq = ?
                           )
                       )
                       OR (
                           related_type = 'qzone_post'
                           AND related_id IN (
                               SELECT id FROM qzone_posts
                               WHERE EXISTS (
                                   SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                                   WHERE CAST(json_each.value AS TEXT) = ?
                               )
                           )
                       )
                )
                """,
                (user_qq, user_qq, user_qq, user_qq),
            ),
            "owner_report_events": (
                """
                SELECT COUNT(*) FROM owner_report_events
                WHERE report_id IN (
                    SELECT id FROM owner_reports
                    WHERE related_id = ?
                       OR (
                           related_type = 'knowledge_job'
                           AND related_id IN (
                               SELECT id FROM knowledge_processing_jobs WHERE user_qq = ?
                           )
                       )
                       OR (
                           related_type = 'proactive_task'
                           AND related_id IN (
                               SELECT id FROM proactive_message_tasks WHERE target_qq = ?
                           )
                       )
                       OR (
                           related_type = 'qzone_post'
                           AND related_id IN (
                               SELECT id FROM qzone_posts
                               WHERE EXISTS (
                                   SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                                   WHERE CAST(json_each.value AS TEXT) = ?
                               )
                           )
                       )
                )
                """,
                (user_qq, user_qq, user_qq, user_qq),
            ),
            "targeted_qzone_posts": (
                """
                SELECT COUNT(*) FROM qzone_posts
                WHERE EXISTS (
                    SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                    WHERE CAST(json_each.value AS TEXT) = ?
                )
                """,
                (user_qq,),
            ),
            "qzone_post_runtime": (
                """
                SELECT COUNT(*) FROM qzone_post_runtime
                WHERE post_id IN (
                    SELECT id FROM qzone_posts
                    WHERE EXISTS (
                        SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                        WHERE CAST(json_each.value AS TEXT) = ?
                    )
                )
                """,
                (user_qq,),
            ),
            "qzone_post_events": (
                """
                SELECT COUNT(*) FROM qzone_post_events
                WHERE post_id IN (
                    SELECT id FROM qzone_posts
                    WHERE EXISTS (
                        SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                        WHERE CAST(json_each.value AS TEXT) = ?
                    )
                )
                """,
                (user_qq,),
            ),
        }
        with self._connect() as connection:
            return {
                name: int(connection.execute(query, parameters).fetchone()[0])
                for name, (query, parameters) in queries.items()
            }

    async def store_privacy_delete_preview(
        self,
        request_id: str,
        impact: dict[str, int],
    ) -> None:
        await asyncio.to_thread(
            self._store_privacy_delete_preview_sync,
            request_id,
            impact,
        )

    def _store_privacy_delete_preview_sync(
        self,
        request_id: str,
        impact: dict[str, int],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO privacy_delete_previews(request_id, impact_json, created_at)
                VALUES(?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    impact_json = excluded.impact_json,
                    created_at = excluded.created_at
                """,
                (request_id, _json(impact), _utc_now()),
            )

    async def store_privacy_artifact(
        self,
        *,
        request_id: str,
        owner_qq: str,
        artifact_type: str,
        path: str,
        sha256: str,
        byte_count: int,
    ) -> None:
        await asyncio.to_thread(
            self._store_privacy_artifact_sync,
            request_id,
            owner_qq,
            artifact_type,
            path,
            sha256,
            byte_count,
        )

    def _store_privacy_artifact_sync(
        self,
        request_id: str,
        owner_qq: str,
        artifact_type: str,
        path: str,
        sha256: str,
        byte_count: int,
    ) -> None:
        if not owner_qq.isdigit():
            raise ValueError("privacy artifact owner_qq must contain digits only")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO privacy_job_artifacts(
                    request_id, owner_qq, artifact_type, path, sha256, byte_count, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id, artifact_type) DO UPDATE SET
                    owner_qq = excluded.owner_qq,
                    path = excluded.path,
                    sha256 = excluded.sha256,
                    byte_count = excluded.byte_count,
                    created_at = excluded.created_at
                """,
                (request_id, owner_qq, artifact_type, path, sha256, byte_count, _utc_now()),
            )

    async def delete_user_data(self, user_qq: str, *, request_id: str) -> dict[str, int]:
        return await asyncio.to_thread(self._delete_user_data_sync, user_qq, request_id)

    def _delete_user_data_sync(self, user_qq: str, request_id: str) -> dict[str, int]:
        impact = self._privacy_delete_impact_sync(user_qq)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            event_keys = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT event_key FROM messages
                    WHERE sender_id = ?
                       OR conversation_key IN (
                           SELECT conversation_key FROM conversations
                           WHERE kind = 'private' AND peer_id = ?
                       )
                    """,
                    (user_qq, user_qq),
                ).fetchall()
            ]
            connection.execute(
                """
                DELETE FROM messages
                WHERE sender_id = ?
                   OR conversation_key IN (
                       SELECT conversation_key FROM conversations
                       WHERE kind = 'private' AND peer_id = ?
                   )
                """,
                (user_qq, user_qq),
            )
            run_ids = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT id FROM inference_runs
                    WHERE actor_qq = ?
                       OR conversation_key = ?
                       OR conversation_key LIKE ?
                    """,
                    (user_qq, f"private:{user_qq}", f"%:private:{user_qq}"),
                ).fetchall()
            ]
            connection.execute(
                "DELETE FROM reply_candidates WHERE target_id = ?",
                (user_qq,),
            )
            if run_ids:
                connection.executemany(
                    "DELETE FROM reply_candidates WHERE inference_run_id = ?",
                    ((run_id,) for run_id in run_ids),
                )
                connection.executemany(
                    "DELETE FROM inference_runs WHERE id = ?",
                    ((run_id,) for run_id in run_ids),
                )
            connection.execute(
                "DELETE FROM conversations WHERE kind = 'private' AND peer_id = ?",
                (user_qq,),
            )
            connection.execute(
                """
                UPDATE proactive_message_tasks
                SET outbox_id = NULL, approval_id = NULL
                WHERE target_qq = ?
                """,
                (user_qq,),
            )
            connection.execute("DELETE FROM outbox WHERE target_id = ?", (user_qq,))
            connection.execute(
                """
                DELETE FROM daily_summaries
                WHERE EXISTS (
                    SELECT 1 FROM json_each(daily_summaries.source_peer_ids_json)
                    WHERE CAST(json_each.value AS TEXT) = ?
                )
                   OR EXISTS (
                       SELECT 1 FROM json_each(daily_summaries.entries_json)
                       WHERE CAST(json_extract(json_each.value, '$.alias') AS TEXT) = ?
                   )
                """,
                (user_qq, user_qq),
            )
            targeted_qzone_ids = self._targeted_post_ids(connection, user_qq)
            if targeted_qzone_ids:
                connection.executemany(
                    "UPDATE qzone_post_runtime SET approval_id = NULL WHERE post_id = ?",
                    ((post_id,) for post_id in targeted_qzone_ids),
                )
            approval_ids = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT approval_requests.id
                    FROM approval_requests
                    LEFT JOIN approval_payloads
                      ON approval_payloads.approval_id = approval_requests.id
                    WHERE approval_requests.subject_id = ?
                       OR approval_requests.id IN (
                           SELECT record_id FROM privacy_subject_links
                           WHERE subject_user_qq = ? AND record_type = 'approval_request'
                       )
                       OR (
                           approval_requests.request_type = 'knowledge.preview'
                           AND approval_requests.subject_id IN (
                               SELECT id FROM knowledge_processing_jobs WHERE user_qq = ?
                           )
                       )
                       OR (
                           approval_requests.request_type LIKE 'proactive_message.%'
                           AND approval_requests.subject_id IN (
                               SELECT id FROM proactive_message_tasks WHERE target_qq = ?
                           )
                       )
                       OR (
                           approval_requests.request_type LIKE 'qzone.%'
                           AND approval_requests.subject_id IN (
                               SELECT id FROM qzone_posts
                               WHERE EXISTS (
                                   SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                                   WHERE CAST(json_each.value AS TEXT) = ?
                               )
                           )
                       )
                    """,
                    (user_qq, user_qq, user_qq, user_qq, user_qq),
                ).fetchall()
            ]
            if approval_ids:
                connection.executemany(
                    "DELETE FROM approval_payloads WHERE approval_id = ?",
                    ((approval_id,) for approval_id in approval_ids),
                )
                connection.executemany(
                    "DELETE FROM approval_requests WHERE id = ?",
                    ((approval_id,) for approval_id in approval_ids),
                )
            connection.execute(
                """
                DELETE FROM control_commands
                WHERE id IN (
                    SELECT record_id FROM privacy_subject_links
                    WHERE subject_user_qq = ? AND record_type = 'control_command'
                )
                """,
                (user_qq,),
            )
            connection.execute(
                """
                DELETE FROM owner_reports
                WHERE related_id = ?
                   OR (
                       related_type = 'knowledge_job'
                       AND related_id IN (
                           SELECT id FROM knowledge_processing_jobs WHERE user_qq = ?
                       )
                   )
                   OR (
                       related_type = 'proactive_task'
                       AND related_id IN (
                           SELECT id FROM proactive_message_tasks WHERE target_qq = ?
                       )
                   )
                   OR (
                       related_type = 'qzone_post'
                       AND related_id IN (
                           SELECT id FROM qzone_posts
                           WHERE EXISTS (
                               SELECT 1 FROM json_each(qzone_posts.target_uins_json)
                               WHERE CAST(json_each.value AS TEXT) = ?
                           )
                       )
                   )
                """,
                (user_qq, user_qq, user_qq, user_qq),
            )
            connection.execute(
                "DELETE FROM proactive_message_tasks WHERE target_qq = ?",
                (user_qq,),
            )
            connection.execute(
                "DELETE FROM user_reply_style_policies WHERE user_qq = ?",
                (user_qq,),
            )
            connection.execute(
                "DELETE FROM proactive_user_policies WHERE user_qq = ?",
                (user_qq,),
            )
            if targeted_qzone_ids:
                connection.executemany(
                    """
                    DELETE FROM audit_log
                    WHERE subject_type = 'qzone_post' AND subject_id = ?
                    """,
                    ((post_id,) for post_id in targeted_qzone_ids),
                )
                connection.executemany(
                    "DELETE FROM qzone_posts WHERE id = ?",
                    ((post_id,) for post_id in targeted_qzone_ids),
                )
            connection.execute("DELETE FROM identity_evidence WHERE user_qq = ?", (user_qq,))
            connection.execute("DELETE FROM history_access_policies WHERE user_qq = ?", (user_qq,))
            connection.execute(
                "DELETE FROM qzone_profile_access_policies WHERE user_qq = ?",
                (user_qq,),
            )
            connection.execute(
                "DELETE FROM qzone_profile_snapshots WHERE user_qq = ?",
                (user_qq,),
            )
            connection.execute(
                "DELETE FROM privacy_scope_policies WHERE scope_type = 'user' AND scope_id = ?",
                (user_qq,),
            )
            connection.execute("DELETE FROM data_access_log WHERE user_qq = ?", (user_qq,))
            connection.execute(
                "DELETE FROM persona_profiles WHERE scope_type = 'private_user' AND scope_id = ?",
                (user_qq,),
            )
            connection.execute("DELETE FROM memory_conflicts WHERE user_qq = ?", (user_qq,))
            memory_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM memory_records WHERE user_qq = ?",
                    (user_qq,),
                ).fetchall()
            ]
            if memory_ids:
                connection.executemany(
                    "DELETE FROM memory_evidence WHERE memory_id = ?",
                    ((memory_id,) for memory_id in memory_ids),
                )
            connection.execute("DELETE FROM memory_records WHERE user_qq = ?", (user_qq,))
            connection.execute(
                "DELETE FROM user_understanding_profiles WHERE user_qq = ?",
                (user_qq,),
            )
            document_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM knowledge_documents WHERE subject_user_qq = ?",
                    (user_qq,),
                ).fetchall()
            ]
            if document_ids:
                connection.executemany(
                    "DELETE FROM document_chunks WHERE document_id = ?",
                    ((document_id,) for document_id in document_ids),
                )
                connection.executemany(
                    "DELETE FROM document_blobs WHERE document_id = ?",
                    ((document_id,) for document_id in document_ids),
                )
            connection.execute(
                "DELETE FROM knowledge_processing_jobs WHERE user_qq = ?",
                (user_qq,),
            )
            connection.execute(
                "DELETE FROM knowledge_documents WHERE subject_user_qq = ?",
                (user_qq,),
            )
            connection.execute("DELETE FROM user_relationships WHERE user_qq = ?", (user_qq,))
            connection.execute("DELETE FROM users WHERE qq_id = ?", (user_qq,))
            connection.execute(
                "DELETE FROM audit_log WHERE subject_type = 'user' AND subject_id = ?",
                (user_qq,),
            )
            connection.execute(
                """
                DELETE FROM audit_log
                WHERE CAST(id AS TEXT) IN (
                    SELECT record_id FROM privacy_subject_links
                    WHERE subject_user_qq = ? AND record_type = 'audit_log'
                )
                """,
                (user_qq,),
            )
            connection.execute(
                "DELETE FROM privacy_subject_links WHERE subject_user_qq = ?",
                (user_qq,),
            )
            if event_keys:
                connection.executemany(
                    "DELETE FROM inbound_events WHERE event_key = ?",
                    ((event_key,) for event_key in event_keys),
                )
            connection.execute(
                """
                DELETE FROM inbound_events
                WHERE CAST(json_extract(payload_json, '$.user_id') AS TEXT) = ?
                """,
                (user_qq,),
            )
            redacted_identity = f"deleted:{request_id}"
            connection.execute(
                "UPDATE privacy_requests SET user_qq = ? WHERE user_qq = ?",
                (redacted_identity, user_qq),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('privacy.delete_completed', 'privacy_request', ?, ?, ?)
                """,
                (request_id, _json({"deleted_counts": impact}), _utc_now()),
            )
            connection.commit()
        return impact

    async def create_proactive_task_with_approval(
        self,
        *,
        task_id: str,
        target_qq: str,
        content: str,
        scheduled_for: datetime,
        timezone_name: str,
        missed_policy: MissedTaskPolicy,
        missed_grace_seconds: int,
        created_by: str,
        source: str,
        approval_id: str,
        approval_code: str,
        requested_to: str,
        default_policy: ProactiveUserPolicy,
        now: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._create_proactive_task_with_approval_sync,
            task_id,
            target_qq,
            content,
            scheduled_for,
            timezone_name,
            missed_policy,
            missed_grace_seconds,
            created_by,
            source,
            approval_id,
            approval_code,
            requested_to,
            default_policy,
            now,
        )

    def _create_proactive_task_with_approval_sync(
        self,
        task_id: str,
        target_qq: str,
        content: str,
        scheduled_for: datetime,
        timezone_name: str,
        missed_policy: MissedTaskPolicy,
        missed_grace_seconds: int,
        created_by: str,
        source: str,
        approval_id: str,
        approval_code: str,
        requested_to: str,
        default_policy: ProactiveUserPolicy,
        now: datetime,
    ) -> None:
        now_iso = now.astimezone(UTC).isoformat()
        scheduled_iso = scheduled_for.astimezone(UTC).isoformat()
        report_id = str(uuid4())
        event_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO proactive_user_policies(
                    user_qq, enabled, timezone, quiet_hours_enabled,
                    quiet_start, quiet_end, quiet_behavior, daily_limit,
                    minimum_interval_seconds, updated_by, updated_at
                ) VALUES(?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_qq,
                    default_policy.timezone,
                    int(default_policy.quiet_hours_enabled),
                    default_policy.quiet_start,
                    default_policy.quiet_end,
                    default_policy.quiet_behavior.value,
                    default_policy.daily_limit,
                    default_policy.minimum_interval_seconds,
                    created_by,
                    now_iso,
                ),
            )
            connection.execute(
                """
                INSERT INTO approval_requests(
                    id, request_type, subject_id, approval_code, status,
                    requested_to, expires_at, created_at
                ) VALUES(?, 'proactive_message.schedule', ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    approval_id,
                    task_id,
                    approval_code,
                    requested_to,
                    (now + timedelta(minutes=30)).isoformat(),
                    now_iso,
                ),
            )
            connection.execute(
                "INSERT INTO approval_payloads(approval_id, payload_json) VALUES(?, ?)",
                (
                    approval_id,
                    _json(
                        {
                            "task_id": task_id,
                            "target_qq": target_qq,
                            "scheduled_for": scheduled_iso,
                        }
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO proactive_message_tasks(
                    id, target_qq, content, status, original_scheduled_for,
                    scheduled_for, timezone, missed_policy, missed_grace_seconds,
                    created_by, source, approval_id, next_eligible_at,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    target_qq,
                    content,
                    ProactiveTaskStatus.PENDING_APPROVAL.value,
                    scheduled_iso,
                    scheduled_iso,
                    timezone_name,
                    missed_policy.value,
                    missed_grace_seconds,
                    created_by,
                    source,
                    approval_id,
                    scheduled_iso,
                    now_iso,
                    now_iso,
                ),
            )
            record_owner_report_on_connection(
                connection,
                report_id=report_id,
                severity="action_required",
                category="proactive_message",
                title="主动消息任务待审批",
                body=(
                    f"任务 {task_id} 将在 {scheduled_iso} 向 QQ {target_qq} 发送私聊；"
                    f"确认码 {approval_code}。用户主动消息权限默认关闭，须另行开启。"
                ),
                related_type="proactive_task",
                related_id=task_id,
                now=datetime.fromisoformat(now_iso),
            )
            connection.execute(
                """
                INSERT INTO proactive_delivery_events(
                    id, task_id, event_type, reason, details_json, occurred_at
                ) VALUES(?, ?, 'created', 'awaiting_owner_approval', ?, ?)
                """,
                (
                    event_id,
                    task_id,
                    _json({"approval_id": approval_id, "source": source}),
                    now_iso,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('proactive.task_created', 'proactive_task', ?, ?, ?)
                """,
                (
                    task_id,
                    _json(
                        {
                            "target_qq": target_qq,
                            "scheduled_for": scheduled_iso,
                            "missed_policy": missed_policy.value,
                        }
                    ),
                    now_iso,
                ),
            )
            connection.commit()

    async def proactive_task(self, task_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._proactive_task_sync, task_id)

    def _proactive_task_sync(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT proactive_message_tasks.*,
                       outbox.status AS outbox_status,
                       outbox.attempts AS outbox_attempts,
                       outbox.last_error AS outbox_last_error
                FROM proactive_message_tasks
                LEFT JOIN outbox ON outbox.id = proactive_message_tasks.outbox_id
                WHERE proactive_message_tasks.id = ?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            events = connection.execute(
                """
                SELECT id, event_type, reason, details_json, occurred_at
                FROM proactive_delivery_events
                WHERE task_id = ? ORDER BY occurred_at ASC
                """,
                (task_id,),
            ).fetchall()
        result = dict(row)
        result["events"] = [
            {**dict(event), "details": json.loads(event["details_json"])} for event in events
        ]
        for key in ("outbox_last_error",):
            if result.get(key):
                result[key] = str(result[key])[:500]
        return result

    async def proactive_tasks(
        self,
        *,
        target_qq: str | None,
        status: ProactiveTaskStatus | None,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._proactive_tasks_sync,
            target_qq,
            status,
            date_from,
            date_to,
            limit,
        )

    def _proactive_tasks_sync(
        self,
        target_qq: str | None,
        status: ProactiveTaskStatus | None,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if target_qq is not None:
            clauses.append("target_qq = ?")
            parameters.append(target_qq)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)
        if date_from is not None:
            clauses.append("original_scheduled_for >= ?")
            parameters.append(date_from.astimezone(UTC).isoformat())
        if date_to is not None:
            clauses.append("original_scheduled_for < ?")
            parameters.append(date_to.astimezone(UTC).isoformat())
        query = "SELECT * FROM proactive_message_tasks"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY original_scheduled_for ASC LIMIT ?"
        parameters.append(max(1, min(limit, 500)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return [dict(row) for row in rows]

    async def proactive_user_policy(self, user_qq: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._proactive_user_policy_sync, user_qq)

    def _proactive_user_policy_sync(self, user_qq: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM proactive_user_policies WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        result["quiet_hours_enabled"] = bool(result["quiet_hours_enabled"])
        result["auto_content_enabled"] = bool(result.get("auto_content_enabled", 0))
        result["send_diary"] = bool(result.get("send_diary", 0))
        result["persisted"] = True
        return result

    async def upsert_proactive_user_policy(
        self,
        policy: ProactiveUserPolicy,
        *,
        updated_by: str,
        now: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._upsert_proactive_user_policy_sync,
            policy,
            updated_by,
            now,
        )

    def _upsert_proactive_user_policy_sync(
        self,
        policy: ProactiveUserPolicy,
        updated_by: str,
        now: datetime,
    ) -> None:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO proactive_user_policies(
                    user_qq, enabled, timezone, quiet_hours_enabled,
                    quiet_start, quiet_end, quiet_behavior, daily_limit,
                    minimum_interval_seconds, auto_content_enabled, send_diary,
                    updated_by, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_qq) DO UPDATE SET
                    enabled = excluded.enabled,
                    timezone = excluded.timezone,
                    quiet_hours_enabled = excluded.quiet_hours_enabled,
                    quiet_start = excluded.quiet_start,
                    quiet_end = excluded.quiet_end,
                    quiet_behavior = excluded.quiet_behavior,
                    daily_limit = excluded.daily_limit,
                    minimum_interval_seconds = excluded.minimum_interval_seconds,
                    auto_content_enabled = excluded.auto_content_enabled,
                    send_diary = excluded.send_diary,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    policy.user_qq,
                    int(policy.enabled),
                    policy.timezone,
                    int(policy.quiet_hours_enabled),
                    policy.quiet_start,
                    policy.quiet_end,
                    policy.quiet_behavior.value,
                    policy.daily_limit,
                    policy.minimum_interval_seconds,
                    int(getattr(policy, "auto_content_enabled", False)),
                    int(getattr(policy, "send_diary", False)),
                    updated_by,
                    now_iso,
                ),
            )
            if policy.enabled:
                connection.execute(
                    """
                    UPDATE proactive_message_tasks
                    SET next_eligible_at = ?, updated_at = ?
                    WHERE target_qq = ? AND status = ?
                    """,
                    (
                        now_iso,
                        now_iso,
                        policy.user_qq,
                        ProactiveTaskStatus.POLICY_BLOCKED.value,
                    ),
                )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('proactive.policy_updated', 'user', ?, ?, ?)
                """,
                (
                    policy.user_qq,
                    _json(
                        {
                            "enabled": policy.enabled,
                            "timezone": policy.timezone,
                            "daily_limit": policy.daily_limit,
                            "auto_content_enabled": policy.auto_content_enabled,
                            "send_diary": policy.send_diary,
                            "updated_by": updated_by,
                        }
                    ),
                    now_iso,
                ),
            )
            connection.commit()

    async def reply_style_policy(self, user_qq: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._reply_style_policy_sync, user_qq)

    def _reply_style_policy_sync(self, user_qq: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM user_reply_style_policies WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["persisted"] = True
        return result

    async def upsert_reply_style_policy(
        self,
        policy: UserReplyStylePolicy,
        *,
        updated_by: str,
        now: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._upsert_reply_style_policy_sync,
            policy,
            updated_by,
            now,
        )

    def _upsert_reply_style_policy_sync(
        self,
        policy: UserReplyStylePolicy,
        updated_by: str,
        now: datetime,
    ) -> None:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO user_reply_style_policies(
                    user_qq, min_bubbles, max_bubbles,
                    sentence_min_chars, sentence_max_chars,
                    updated_by, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_qq) DO UPDATE SET
                    min_bubbles = excluded.min_bubbles,
                    max_bubbles = excluded.max_bubbles,
                    sentence_min_chars = excluded.sentence_min_chars,
                    sentence_max_chars = excluded.sentence_max_chars,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    policy.user_qq,
                    policy.min_bubbles,
                    policy.max_bubbles,
                    policy.sentence_min_chars,
                    policy.sentence_max_chars,
                    updated_by,
                    now_iso,
                ),
            )
            connection.commit()

    async def approve_proactive_task(
        self,
        task_id: str,
        *,
        actor_qq: str,
        approved_at: datetime,
        late: bool,
    ) -> bool:
        return await asyncio.to_thread(
            self._approve_proactive_task_sync,
            task_id,
            actor_qq,
            approved_at,
            late,
        )

    def _approve_proactive_task_sync(
        self,
        task_id: str,
        actor_qq: str,
        approved_at: datetime,
        late: bool,
    ) -> bool:
        expected = (
            ProactiveTaskStatus.REAPPROVAL_REQUIRED
            if late
            else ProactiveTaskStatus.PENDING_APPROVAL
        )
        now_iso = approved_at.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE proactive_message_tasks
                SET status = ?, approved_by = ?, approved_at = ?,
                    next_eligible_at = CASE
                        WHEN scheduled_for > ? THEN scheduled_for ELSE ? END,
                    hold_reason = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    ProactiveTaskStatus.SCHEDULED.value,
                    actor_qq,
                    now_iso,
                    now_iso,
                    now_iso,
                    "owner_reapproved_exception" if late else None,
                    now_iso,
                    task_id,
                    expected.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE owner_reports SET status = 'acknowledged', acknowledged_at = ?
                    WHERE related_type = 'proactive_task' AND related_id = ?
                      AND status = 'pending'
                    """,
                    (now_iso, task_id),
                )
                connection.execute(
                    """
                    INSERT INTO proactive_delivery_events(
                        id, task_id, event_type, reason, details_json, occurred_at
                    ) VALUES(?, ?, 'approved', ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        task_id,
                        "late_reapproval" if late else "initial_approval",
                        _json({"actor_qq": actor_qq}),
                        now_iso,
                    ),
                )
            connection.commit()
        return updated.rowcount == 1

    async def reject_proactive_task(
        self,
        task_id: str,
        *,
        actor_qq: str,
        decided_at: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._decide_proactive_task_sync,
            task_id,
            ProactiveTaskStatus.REJECTED,
            actor_qq,
            decided_at,
        )

    async def expire_proactive_task_approval(
        self,
        task_id: str,
        *,
        expired_at: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._decide_proactive_task_sync,
            task_id,
            ProactiveTaskStatus.APPROVAL_EXPIRED,
            None,
            expired_at,
        )

    def _decide_proactive_task_sync(
        self,
        task_id: str,
        status: ProactiveTaskStatus,
        actor_qq: str | None,
        decided_at: datetime,
    ) -> bool:
        now_iso = decided_at.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE proactive_message_tasks
                SET status = ?, hold_reason = ?, updated_at = ?
                WHERE id = ? AND status IN (?, ?)
                """,
                (
                    status.value,
                    status.value,
                    now_iso,
                    task_id,
                    ProactiveTaskStatus.PENDING_APPROVAL.value,
                    ProactiveTaskStatus.REAPPROVAL_REQUIRED.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    UPDATE owner_reports SET status = 'acknowledged', acknowledged_at = ?
                    WHERE related_type = 'proactive_task' AND related_id = ?
                      AND status = 'pending'
                    """,
                    (now_iso, task_id),
                )
                connection.execute(
                    """
                    INSERT INTO proactive_delivery_events(
                        id, task_id, event_type, reason, details_json, occurred_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        task_id,
                        status.value,
                        status.value,
                        _json({"actor_qq": actor_qq}),
                        now_iso,
                    ),
                )
            connection.commit()
        return updated.rowcount == 1

    async def cancel_proactive_task(self, task_id: str, *, actor_qq: str) -> bool:
        return await asyncio.to_thread(self._cancel_proactive_task_sync, task_id, actor_qq)

    def _cancel_proactive_task_sync(self, task_id: str, actor_qq: str) -> bool:
        now = _utc_now()
        cancellable = (
            ProactiveTaskStatus.PENDING_APPROVAL.value,
            ProactiveTaskStatus.SCHEDULED.value,
            ProactiveTaskStatus.EVALUATING.value,
            ProactiveTaskStatus.WAITING_QUIET_HOURS.value,
            ProactiveTaskStatus.WAITING_RATE_LIMIT.value,
            ProactiveTaskStatus.POLICY_BLOCKED.value,
            ProactiveTaskStatus.REAPPROVAL_REQUIRED.value,
            ProactiveTaskStatus.ENQUEUED.value,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, outbox_id FROM proactive_message_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None or row["status"] not in cancellable:
                connection.rollback()
                return False
            if row["outbox_id"]:
                outbox = connection.execute(
                    "SELECT status FROM outbox WHERE id = ?",
                    (row["outbox_id"],),
                ).fetchone()
                if outbox is not None and outbox["status"] not in {"pending", "failed"}:
                    connection.rollback()
                    return False
                connection.execute(
                    "UPDATE proactive_message_tasks SET outbox_id = NULL WHERE id = ?",
                    (task_id,),
                )
                connection.execute("DELETE FROM outbox WHERE id = ?", (row["outbox_id"],))
            connection.execute(
                """
                UPDATE proactive_message_tasks
                SET status = ?, cancelled_at = ?, hold_reason = 'owner_cancelled',
                    claim_expires_at = NULL, outbox_id = NULL, updated_at = ?
                WHERE id = ?
                """,
                (ProactiveTaskStatus.CANCELLED.value, now, now, task_id),
            )
            connection.execute(
                """
                UPDATE approval_requests
                SET status = 'expired', decided_at = ?
                WHERE subject_id = ? AND request_type LIKE 'proactive_message.%'
                  AND status = 'pending'
                """,
                (now, task_id),
            )
            connection.execute(
                """
                UPDATE owner_reports SET status = 'acknowledged', acknowledged_at = ?
                WHERE related_type = 'proactive_task' AND related_id = ?
                  AND status = 'pending'
                """,
                (now, task_id),
            )
            connection.execute(
                """
                INSERT INTO proactive_delivery_events(
                    id, task_id, event_type, reason, details_json, occurred_at
                ) VALUES(?, ?, 'cancelled', 'owner_cancelled', ?, ?)
                """,
                (str(uuid4()), task_id, _json({"actor_qq": actor_qq}), now),
            )
            connection.commit()
        return True

    async def claim_due_proactive_task(
        self,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._claim_due_proactive_task_sync, now, lease_seconds)

    def _claim_due_proactive_task_sync(
        self,
        now: datetime,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        now_iso = now.astimezone(UTC).isoformat()
        lease_iso = (now + timedelta(seconds=lease_seconds)).astimezone(UTC).isoformat()
        ready_statuses = (
            ProactiveTaskStatus.SCHEDULED.value,
            ProactiveTaskStatus.WAITING_QUIET_HOURS.value,
            ProactiveTaskStatus.WAITING_RATE_LIMIT.value,
            ProactiveTaskStatus.POLICY_BLOCKED.value,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE proactive_message_tasks
                SET status = ?, claim_expires_at = NULL,
                    hold_reason = 'recovered_expired_claim', updated_at = ?
                WHERE status = ? AND claim_expires_at <= ?
                """,
                (
                    ProactiveTaskStatus.SCHEDULED.value,
                    now_iso,
                    ProactiveTaskStatus.EVALUATING.value,
                    now_iso,
                ),
            )
            row = connection.execute(
                """
                SELECT proactive_message_tasks.*,
                       proactive_user_policies.enabled AS policy_enabled,
                       proactive_user_policies.timezone AS policy_timezone,
                       proactive_user_policies.quiet_hours_enabled,
                       proactive_user_policies.quiet_start,
                       proactive_user_policies.quiet_end,
                       proactive_user_policies.quiet_behavior,
                       proactive_user_policies.daily_limit,
                       proactive_user_policies.minimum_interval_seconds,
                       proactive_user_policies.send_diary AS send_diary
                FROM proactive_message_tasks
                JOIN proactive_user_policies
                  ON proactive_user_policies.user_qq = proactive_message_tasks.target_qq
                WHERE proactive_message_tasks.status IN (?, ?, ?, ?)
                  AND COALESCE(proactive_message_tasks.next_eligible_at,
                               proactive_message_tasks.scheduled_for) <= ?
                ORDER BY proactive_message_tasks.scheduled_for ASC
                LIMIT 1
                """,
                (*ready_statuses, now_iso),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            previous_first_evaluated_at = row["first_evaluated_at"]
            updated = connection.execute(
                """
                UPDATE proactive_message_tasks
                SET status = ?, claim_expires_at = ?,
                    first_evaluated_at = COALESCE(first_evaluated_at, ?),
                    last_evaluated_at = ?, evaluation_attempts = evaluation_attempts + 1,
                    updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    ProactiveTaskStatus.EVALUATING.value,
                    lease_iso,
                    now_iso,
                    now_iso,
                    now_iso,
                    row["id"],
                    row["status"],
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
        result = dict(row)
        result["policy_enabled"] = bool(result["policy_enabled"])
        result["quiet_hours_enabled"] = bool(result["quiet_hours_enabled"])
        result["send_diary"] = bool(result.get("send_diary", 0))
        result["was_previously_evaluated"] = previous_first_evaluated_at is not None
        return result

    async def hold_proactive_task(
        self,
        task_id: str,
        *,
        status: ProactiveTaskStatus,
        reason: str,
        next_eligible_at: datetime,
        now: datetime,
    ) -> bool:
        if status not in {
            ProactiveTaskStatus.WAITING_QUIET_HOURS,
            ProactiveTaskStatus.WAITING_RATE_LIMIT,
            ProactiveTaskStatus.POLICY_BLOCKED,
        }:
            raise ValueError("unsupported proactive hold status")
        return await asyncio.to_thread(
            self._hold_proactive_task_sync,
            task_id,
            status,
            reason,
            next_eligible_at,
            now,
        )

    def _hold_proactive_task_sync(
        self,
        task_id: str,
        status: ProactiveTaskStatus,
        reason: str,
        next_eligible_at: datetime,
        now: datetime,
    ) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT hold_reason FROM proactive_message_tasks
                WHERE id = ? AND status = ?
                """,
                (task_id, ProactiveTaskStatus.EVALUATING.value),
            ).fetchone()
            updated = connection.execute(
                """
                UPDATE proactive_message_tasks
                SET status = ?, hold_reason = ?, next_eligible_at = ?,
                    claim_expires_at = NULL, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    reason,
                    next_eligible_at.astimezone(UTC).isoformat(),
                    now_iso,
                    task_id,
                    ProactiveTaskStatus.EVALUATING.value,
                ),
            )
            if updated.rowcount == 1 and current is not None and current["hold_reason"] != reason:
                connection.execute(
                    """
                    INSERT INTO proactive_delivery_events(
                        id, task_id, event_type, reason, details_json, occurred_at
                    ) VALUES(?, ?, 'held', ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        task_id,
                        reason,
                        _json({"next_eligible_at": next_eligible_at.isoformat()}),
                        now_iso,
                    ),
                )
            connection.commit()
        return updated.rowcount == 1

    async def mark_proactive_missed(
        self,
        task_id: str,
        *,
        reason: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._terminal_proactive_evaluation_sync,
            task_id,
            ProactiveTaskStatus.MISSED,
            reason,
            now,
        )

    def _terminal_proactive_evaluation_sync(
        self,
        task_id: str,
        status: ProactiveTaskStatus,
        reason: str,
        now: datetime,
    ) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE proactive_message_tasks
                SET status = ?, hold_reason = ?, claim_expires_at = NULL, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    reason,
                    now_iso,
                    task_id,
                    ProactiveTaskStatus.EVALUATING.value,
                ),
            )
            if updated.rowcount == 1:
                connection.execute(
                    """
                    INSERT INTO proactive_delivery_events(
                        id, task_id, event_type, reason, details_json, occurred_at
                    ) VALUES(?, ?, ?, ?, '{}', ?)
                    """,
                    (str(uuid4()), task_id, status.value, reason, now_iso),
                )
                if status is ProactiveTaskStatus.MISSED:
                    record_owner_report_on_connection(
                        connection,
                        report_id=str(uuid4()),
                        severity="warning",
                        category="proactive_message",
                        title="主动消息已按策略跳过",
                        body=f"任务 {task_id} 未补发，原因：{reason}。",
                        related_type="proactive_task",
                        related_id=task_id,
                        now=datetime.fromisoformat(now_iso),
                    )
            connection.commit()
        return updated.rowcount == 1

    async def require_proactive_reapproval(
        self,
        task_id: str,
        *,
        reason: str,
        approval_id: str,
        approval_code: str,
        requested_to: str,
        now: datetime,
    ) -> bool:
        return await asyncio.to_thread(
            self._require_proactive_reapproval_sync,
            task_id,
            reason,
            approval_id,
            approval_code,
            requested_to,
            now,
        )

    def _require_proactive_reapproval_sync(
        self,
        task_id: str,
        reason: str,
        approval_id: str,
        approval_code: str,
        requested_to: str,
        now: datetime,
    ) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT target_qq FROM proactive_message_tasks WHERE id = ? AND status = ?",
                (task_id, ProactiveTaskStatus.EVALUATING.value),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            connection.execute(
                """
                INSERT INTO approval_requests(
                    id, request_type, subject_id, approval_code, status,
                    requested_to, expires_at, created_at
                ) VALUES(?, 'proactive_message.reapprove', ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    approval_id,
                    task_id,
                    approval_code,
                    requested_to,
                    (now + timedelta(minutes=30)).isoformat(),
                    now_iso,
                ),
            )
            connection.execute(
                "INSERT INTO approval_payloads(approval_id, payload_json) VALUES(?, ?)",
                (approval_id, _json({"task_id": task_id, "reason": reason})),
            )
            connection.execute(
                """
                UPDATE proactive_message_tasks
                SET status = ?, approval_id = ?, hold_reason = ?,
                    claim_expires_at = NULL, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    ProactiveTaskStatus.REAPPROVAL_REQUIRED.value,
                    approval_id,
                    reason,
                    now_iso,
                    task_id,
                    ProactiveTaskStatus.EVALUATING.value,
                ),
            )
            record_owner_report_on_connection(
                connection,
                report_id=str(uuid4()),
                severity="action_required",
                category="proactive_message",
                title="主动消息需要重新审批",
                body=f"任务 {task_id} 因 {reason} 未直接投递；确认码 {approval_code}。",
                related_type="proactive_task",
                related_id=task_id,
                now=datetime.fromisoformat(now_iso),
            )
            connection.execute(
                """
                INSERT INTO proactive_delivery_events(
                    id, task_id, event_type, reason, details_json, occurred_at
                ) VALUES(?, ?, 'reapproval_required', ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    task_id,
                    reason,
                    _json({"approval_id": approval_id}),
                    now_iso,
                ),
            )
            connection.commit()
        return True

    async def proactive_delivery_window(
        self,
        *,
        target_qq: str,
        day_start: datetime,
        day_end: datetime,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._proactive_delivery_window_sync,
            target_qq,
            day_start,
            day_end,
        )

    def _proactive_delivery_window_sync(
        self,
        target_qq: str,
        day_start: datetime,
        day_end: datetime,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS delivered_count, MAX(enqueued_at) AS last_enqueued_at
                FROM proactive_message_tasks
                WHERE target_qq = ?
                  AND status IN (?, ?)
                  AND enqueued_at >= ? AND enqueued_at < ?
                """,
                (
                    target_qq,
                    ProactiveTaskStatus.ENQUEUED.value,
                    ProactiveTaskStatus.SENT.value,
                    day_start.astimezone(UTC).isoformat(),
                    day_end.astimezone(UTC).isoformat(),
                ),
            ).fetchone()
        return dict(row)

    async def enqueue_proactive_task(self, task_id: str, *, now: datetime) -> bool:
        return await asyncio.to_thread(self._enqueue_proactive_task_sync, task_id, now)

    def _enqueue_proactive_task_sync(self, task_id: str, now: datetime) -> bool:
        now_iso = now.astimezone(UTC).isoformat()
        outbox_id = str(uuid5(NAMESPACE_URL, f"proactive-outbox:{task_id}"))
        idempotency_key = f"proactive:{task_id}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT target_qq, content FROM proactive_message_tasks
                WHERE id = ? AND status = ?
                """,
                (task_id, ProactiveTaskStatus.EVALUATING.value),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            connection.execute(
                """
                INSERT OR IGNORE INTO outbox(
                    id, idempotency_key, conversation_kind, target_id,
                    segments_json, status, available_at, created_at
                ) VALUES(?, ?, 'private', ?, ?, 'pending', ?, ?)
                """,
                (
                    outbox_id,
                    idempotency_key,
                    row["target_qq"],
                    _json([{"type": "text", "data": {"text": row["content"]}}]),
                    now_iso,
                    now_iso,
                ),
            )
            updated = connection.execute(
                """
                UPDATE proactive_message_tasks
                SET status = ?, outbox_id = ?, enqueued_at = ?,
                    claim_expires_at = NULL, hold_reason = NULL, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    ProactiveTaskStatus.ENQUEUED.value,
                    outbox_id,
                    now_iso,
                    now_iso,
                    task_id,
                    ProactiveTaskStatus.EVALUATING.value,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return False
            connection.execute(
                """
                INSERT INTO proactive_delivery_events(
                    id, task_id, event_type, reason, details_json, occurred_at
                ) VALUES(?, ?, 'enqueued', 'all_policy_checks_passed', ?, ?)
                """,
                (str(uuid4()), task_id, _json({"outbox_id": outbox_id}), now_iso),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('proactive.task_enqueued', 'proactive_task', ?, ?, ?)
                """,
                (task_id, _json({"outbox_id": outbox_id}), now_iso),
            )
            connection.commit()
        return True

    async def proactive_summary(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._proactive_summary_sync)

    def _proactive_summary_sync(self) -> dict[str, Any]:
        with self._connect() as connection:
            status_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM proactive_message_tasks GROUP BY status"
            ).fetchall()
            enabled_users = connection.execute(
                "SELECT COUNT(*) FROM proactive_user_policies WHERE enabled = 1"
            ).fetchone()[0]
            next_task = connection.execute(
                """
                SELECT original_scheduled_for FROM proactive_message_tasks
                WHERE status IN (?, ?, ?, ?) ORDER BY original_scheduled_for ASC LIMIT 1
                """,
                (
                    ProactiveTaskStatus.SCHEDULED.value,
                    ProactiveTaskStatus.WAITING_QUIET_HOURS.value,
                    ProactiveTaskStatus.WAITING_RATE_LIMIT.value,
                    ProactiveTaskStatus.POLICY_BLOCKED.value,
                ),
            ).fetchone()
        return {
            "by_status": {row["status"]: row["count"] for row in status_rows},
            "enabled_users": int(enabled_users),
            "next_scheduled_for": next_task[0] if next_task else None,
        }

    async def enqueue_outbound(self, message: OutboundMessage) -> bool:
        return await asyncio.to_thread(self._enqueue_outbound_sync, message)

    def _enqueue_outbound_sync(self, message: OutboundMessage) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO outbox(
                    id, idempotency_key, conversation_kind, target_id,
                    segments_json, status, available_at, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.idempotency_key,
                    message.conversation_kind.value,
                    message.target_id,
                    _json([segment.as_onebot() for segment in message.segments]),
                    message.status.value,
                    message.created_at.isoformat(),
                    message.created_at.isoformat(),
                ),
            )
            return result.rowcount == 1

    async def record_friend_baseline(
        self,
        *,
        bot_qq: str,
        friend_ids: Iterable[str],
        captured_at: datetime,
    ) -> str:
        normalized_ids = {
            str(friend_id).strip() for friend_id in friend_ids if str(friend_id).strip()
        }
        normalized = tuple(sorted(normalized_ids))
        return await asyncio.to_thread(
            self._record_friend_baseline_sync,
            bot_qq,
            normalized,
            captured_at,
        )

    def _record_friend_baseline_sync(
        self,
        bot_qq: str,
        friend_ids: tuple[str, ...],
        captured_at: datetime,
    ) -> str:
        captured_iso = captured_at.astimezone(UTC).isoformat()
        snapshot_hash = hashlib.sha256("\n".join(friend_ids).encode()).hexdigest()
        baseline_key = f"friend-baseline:{bot_qq}:{captured_iso}:{snapshot_hash}"
        baseline_id = str(uuid5(NAMESPACE_URL, baseline_key))
        created_at = _utc_now()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO friend_baselines(
                    id, bot_qq, captured_at, friend_count, snapshot_hash, status, created_at
                ) VALUES(?, ?, ?, ?, ?, 'completed', ?)
                """,
                (baseline_id, bot_qq, captured_iso, len(friend_ids), snapshot_hash, created_at),
            )
            for user_qq in friend_ids:
                connection.execute(
                    """
                    INSERT INTO user_relationships(
                        user_qq, friend_state, state_source, confidence,
                        first_observed_at, updated_at
                    ) VALUES(?, 'existing_friend', 'baseline_snapshot', 1, ?, ?)
                    ON CONFLICT(user_qq) DO UPDATE SET
                        friend_state = excluded.friend_state,
                        state_source = excluded.state_source,
                        confidence = excluded.confidence,
                        updated_at = excluded.updated_at
                    WHERE user_relationships.state_source != 'owner_override'
                    """,
                    (user_qq, captured_iso, created_at),
                )
                evidence_id = str(uuid5(NAMESPACE_URL, f"{baseline_id}:{user_qq}"))
                connection.execute(
                    """
                    INSERT OR IGNORE INTO identity_evidence(
                        id, user_qq, evidence_type, source, value_json,
                        observed_at, created_at
                    ) VALUES(?, ?, 'baseline_snapshot', ?, ?, ?, ?)
                    """,
                    (
                        evidence_id,
                        user_qq,
                        baseline_id,
                        _json({"present": True}),
                        captured_iso,
                        created_at,
                    ),
                )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('friend.baseline_recorded', 'friend_baseline', ?, ?, ?)
                """,
                (baseline_id, _json({"friend_count": len(friend_ids)}), created_at),
            )
            connection.commit()
        return baseline_id

    async def record_friend_add(
        self,
        *,
        bot_qq: str,
        user_qq: str,
        occurred_at: datetime,
        event_key: str,
        raw_event: dict[str, Any],
    ) -> tuple[FriendState, bool]:
        return await asyncio.to_thread(
            self._record_friend_add_sync,
            bot_qq,
            user_qq,
            occurred_at,
            event_key,
            raw_event,
        )

    def _record_friend_add_sync(
        self,
        bot_qq: str,
        user_qq: str,
        occurred_at: datetime,
        event_key: str,
        raw_event: dict[str, Any],
    ) -> tuple[FriendState, bool]:
        occurred_iso = occurred_at.astimezone(UTC).isoformat()
        created_at = _utc_now()
        evidence_id = str(uuid5(NAMESPACE_URL, f"friend-add:{event_key}"))

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT friend_state, state_source FROM user_relationships WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO inbound_events(
                    event_key, bot_qq, post_type, payload_json, received_at
                ) VALUES(?, ?, 'notice', ?, ?)
                """,
                (event_key, bot_qq, _json(raw_event), created_at),
            )
            if inserted.rowcount == 0:
                connection.rollback()
                state = FriendState(current["friend_state"]) if current else FriendState.UNKNOWN
                return state, False
            baseline = connection.execute(
                """
                SELECT id FROM friend_baselines
                WHERE bot_qq = ? AND status = 'completed' AND captured_at <= ?
                ORDER BY captured_at DESC LIMIT 1
                """,
                (bot_qq, occurred_iso),
            ).fetchone()
            if current is not None and current["state_source"] == "owner_override":
                state = FriendState(current["friend_state"])
                source = "owner_override"
                confidence = 1.0
            elif baseline is not None:
                state = FriendState.NEW_FRIEND
                source = "friend_add_after_baseline"
                confidence = 1.0
            else:
                state = FriendState.UNKNOWN
                source = "friend_add_seen_before_baseline"
                confidence = 0.5

            connection.execute(
                """
                INSERT INTO user_relationships(
                    user_qq, friend_state, state_source, confidence,
                    first_observed_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_qq) DO UPDATE SET
                    friend_state = excluded.friend_state,
                    state_source = excluded.state_source,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                WHERE user_relationships.state_source != 'owner_override'
                """,
                (user_qq, state.value, source, confidence, occurred_iso, created_at),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO identity_evidence(
                    id, user_qq, evidence_type, source, value_json,
                    observed_at, created_at
                ) VALUES(?, ?, 'friend_add_event', ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    user_qq,
                    event_key,
                    _json({"baseline_id": baseline["id"] if baseline else None}),
                    occurred_iso,
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('friend.add_observed', 'user', ?, ?, ?)
                """,
                (user_qq, _json({"friend_state": state.value}), created_at),
            )
            connection.commit()
        return state, True

    async def set_history_policy(
        self,
        *,
        user_qq: str,
        mode: HistoryAccessMode,
        updated_by: str,
        reason: str,
        max_messages: int | None = None,
        one_time_remaining: int = 0,
        selected_from: str | None = None,
        selected_to: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._set_history_policy_sync,
            user_qq,
            mode,
            updated_by,
            reason,
            max_messages,
            one_time_remaining,
            selected_from,
            selected_to,
        )

    def _set_history_policy_sync(
        self,
        user_qq: str,
        mode: HistoryAccessMode,
        updated_by: str,
        reason: str,
        max_messages: int | None,
        one_time_remaining: int,
        selected_from: str | None,
        selected_to: str | None,
    ) -> None:
        updated_at = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO history_access_policies(
                    user_qq, mode, selected_from, selected_to,
                    max_messages, one_time_remaining,
                    reason, updated_by, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_qq) DO UPDATE SET
                    mode = excluded.mode,
                    selected_from = excluded.selected_from,
                    selected_to = excluded.selected_to,
                    max_messages = excluded.max_messages,
                    one_time_remaining = excluded.one_time_remaining,
                    reason = excluded.reason,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    user_qq,
                    mode.value,
                    selected_from,
                    selected_to,
                    max_messages,
                    one_time_remaining,
                    reason,
                    updated_by,
                    updated_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('history.policy_changed', 'user', ?, ?, ?)
                """,
                (user_qq, _json({"mode": mode.value, "updated_by": updated_by}), updated_at),
            )

    async def identity_summary(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._identity_summary_sync)

    def _identity_summary_sync(self) -> dict[str, Any]:
        states = {state.value: 0 for state in FriendState}
        history_modes = {mode.value: 0 for mode in HistoryAccessMode}
        qzone_profile_modes = {mode.value: 0 for mode in QzoneProfileAccessMode}
        with self._connect() as connection:
            state_counts_sql = """
                SELECT friend_state, COUNT(*) AS count
                FROM user_relationships
                GROUP BY friend_state
            """
            for row in connection.execute(state_counts_sql):
                states[row["friend_state"]] = int(row["count"])
            relationship_count = sum(states.values())
            explicit_count = 0
            for row in connection.execute(
                "SELECT mode, COUNT(*) AS count FROM history_access_policies GROUP BY mode"
            ):
                count = int(row["count"])
                history_modes[row["mode"]] = count
                explicit_count += count
            history_modes[HistoryAccessMode.DENY.value] += max(
                0,
                relationship_count - explicit_count,
            )
            qzone_explicit = 0
            for row in connection.execute(
                """
                SELECT mode, COUNT(*) AS count
                FROM qzone_profile_access_policies GROUP BY mode
                """
            ):
                count = int(row["count"])
                qzone_profile_modes[row["mode"]] = count
                qzone_explicit += count
            qzone_profile_modes[QzoneProfileAccessMode.DENY.value] += max(
                0,
                relationship_count - qzone_explicit,
            )
            baseline = connection.execute(
                """
                SELECT captured_at, friend_count FROM friend_baselines
                WHERE status = 'completed' ORDER BY captured_at DESC LIMIT 1
                """
            ).fetchone()
            frozen = int(
                connection.execute(
                    "SELECT COUNT(*) FROM user_relationships WHERE data_frozen = 1"
                ).fetchone()[0]
            )
        return {
            "friend_states": states,
            "history_modes": history_modes,
            "qzone_profile_modes": qzone_profile_modes,
            "latest_baseline": dict(baseline) if baseline else None,
            "frozen_users": frozen,
        }

    async def authorize_history_access(
        self,
        *,
        user_qq: str,
        requested_count: int,
        include_media: bool,
        purpose: str,
        accessor: str,
        source_supports_range: bool,
        consume: bool = True,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._authorize_history_access_sync,
            user_qq,
            requested_count,
            include_media,
            purpose,
            accessor,
            source_supports_range,
            consume,
        )

    def _authorize_history_access_sync(
        self,
        user_qq: str,
        requested_count: int,
        include_media: bool,
        purpose: str,
        accessor: str,
        source_supports_range: bool,
        consume: bool,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            relationship = connection.execute(
                "SELECT data_frozen FROM user_relationships WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
            policy_row = connection.execute(
                "SELECT * FROM history_access_policies WHERE user_qq = ?",
                (user_qq,),
            ).fetchone()
            policy = (
                dict(policy_row)
                if policy_row
                else {
                    "mode": HistoryAccessMode.DENY.value,
                    "max_messages": None,
                    "allow_media": 0,
                    "one_time_remaining": 0,
                    "source": "global_default",
                }
            )

            allowed = True
            reason = "authorized"
            if relationship is not None and int(relationship["data_frozen"]) == 1:
                allowed, reason = False, "user_data_frozen"
            elif requested_count <= 0:
                allowed, reason = False, "invalid_message_count"
            elif policy["mode"] in {
                HistoryAccessMode.DENY.value,
                HistoryAccessMode.FILE_IMPORT_ONLY.value,
            }:
                allowed, reason = False, f"history_mode_{policy['mode']}"
            elif include_media and not bool(policy.get("allow_media", 0)):
                allowed, reason = False, "media_not_authorized"
            elif policy.get("max_messages") and requested_count > int(policy["max_messages"]):
                allowed, reason = False, "message_limit_exceeded"
            elif (
                policy["mode"] == HistoryAccessMode.SELECTED_RANGE.value
                and not source_supports_range
            ):
                allowed, reason = False, "source_cannot_enforce_selected_range"
            elif policy["mode"] == HistoryAccessMode.ONE_TIME.value:
                remaining = int(policy.get("one_time_remaining", 0))
                if remaining <= 0:
                    allowed, reason = False, "one_time_authorization_consumed"
                elif consume:
                    consumed = connection.execute(
                        """
                        UPDATE history_access_policies
                        SET one_time_remaining = one_time_remaining - 1,
                            updated_at = ?
                        WHERE user_qq = ? AND one_time_remaining > 0
                        """,
                        (now, user_qq),
                    )
                    if consumed.rowcount != 1:
                        allowed, reason = False, "one_time_authorization_consumed"

            if consume or not allowed:
                decision = "allowed" if allowed else "denied"
                connection.execute(
                    """
                    INSERT INTO data_access_log(
                        user_qq, data_class, purpose, accessor,
                        decision, policy_snapshot_json, created_at
                    ) VALUES(?, 'message_content', ?, ?, ?, ?, ?)
                    """,
                    (
                        user_qq,
                        purpose,
                        accessor,
                        decision,
                        _json(
                            {
                                "mode": policy["mode"],
                                "requested_count": requested_count,
                                "include_media": include_media,
                                "reason": reason,
                            }
                        ),
                        now,
                    ),
                )
            connection.commit()
        return {"allowed": allowed, "reason": reason, "mode": policy["mode"]}

    async def claim_outbound(self, *, max_attempts: int = 5) -> OutboundMessage | None:
        return await asyncio.to_thread(self._claim_outbound_sync, max_attempts)

    def _claim_outbound_sync(self, max_attempts: int) -> OutboundMessage | None:
        if not (1 <= max_attempts <= 20):
            raise ValueError("outbox max attempts must be between 1 and 20")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM outbox
                WHERE status IN ('pending', 'failed') AND available_at <= ?
                  AND attempts < ?
                  AND (
                    NOT EXISTS(
                      SELECT 1 FROM reply_delivery_evidence AS current_mapping
                      WHERE current_mapping.outbox_id = outbox.id
                        AND current_mapping.outcome = 'queued'
                    )
                    OR EXISTS(
                      SELECT 1 FROM reply_delivery_evidence AS current_mapping
                      JOIN reply_runs ON reply_runs.id = current_mapping.run_id
                      WHERE current_mapping.outbox_id = outbox.id
                        AND current_mapping.outcome = 'queued'
                        AND reply_runs.stage = 'awaiting_delivery'
                        AND NOT EXISTS(
                          SELECT 1 FROM reply_runtime_state
                          WHERE reply_runtime_state.bot_qq = reply_runs.bot_qq
                            AND reply_runtime_state.emergency_paused = 1
                        )
                        AND NOT EXISTS(
                          SELECT 1
                          FROM reply_delivery_evidence AS prior_mapping
                          JOIN outbox AS prior_outbox
                            ON prior_outbox.id = prior_mapping.outbox_id
                          WHERE prior_mapping.run_id = current_mapping.run_id
                            AND prior_mapping.outcome = 'queued'
                            AND prior_mapping.bubble_sequence
                                < current_mapping.bubble_sequence
                            AND prior_outbox.status != 'sent'
                        )
                    )
                  )
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (_utc_now(), max_attempts),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            connection.execute(
                "UPDATE outbox SET status = 'sending', attempts = attempts + 1 WHERE id = ?",
                (row["id"],),
            )
            connection.commit()

        segments = tuple(
            MessageSegment(type=item["type"], data=item.get("data", {}))
            for item in json.loads(row["segments_json"])
        )
        return OutboundMessage(
            id=row["id"],
            idempotency_key=row["idempotency_key"],
            conversation_kind=ConversationKind(row["conversation_kind"]),
            target_id=row["target_id"],
            segments=segments,
            status=OutboxStatus.SENDING,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    async def mark_outbound_sent(self, message_id: str, *, bot_qq: str = "") -> None:
        await asyncio.to_thread(self._mark_outbound_sent_sync, message_id, bot_qq)

    def _mark_outbound_sent_sync(self, message_id: str, bot_qq: str) -> None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE outbox SET status = 'sent', sent_at = ?, last_error = NULL WHERE id = ?",
                (now, message_id),
            )
            outbox = connection.execute(
                "SELECT * FROM outbox WHERE id = ?",
                (message_id,),
            ).fetchone()
            if outbox is not None:
                _mirror_outbound_message(connection, outbox, bot_qq, now)
            task = connection.execute(
                """
                SELECT id, content_source, scheduled_for, timezone
                FROM proactive_message_tasks
                WHERE outbox_id = ? AND status = ?
                """,
                (message_id, ProactiveTaskStatus.ENQUEUED.value),
            ).fetchone()
            if task is not None:
                connection.execute(
                    """
                    UPDATE proactive_message_tasks
                    SET status = ?, sent_at = ?, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        ProactiveTaskStatus.SENT.value,
                        now,
                        now,
                        task["id"],
                        ProactiveTaskStatus.ENQUEUED.value,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO proactive_delivery_events(
                        id, task_id, event_type, reason, details_json, occurred_at
                    ) VALUES(?, ?, 'sent', 'outbox_delivery_confirmed', ?, ?)
                    """,
                    (str(uuid4()), task["id"], _json({"outbox_id": message_id}), now),
                )
                if str(task["content_source"] or "") == "diary":
                    event = connection.execute(
                        """
                        SELECT details_json FROM proactive_delivery_events
                        WHERE task_id = ? AND event_type = 'enqueued'
                        ORDER BY occurred_at DESC LIMIT 1
                        """,
                        (task["id"],),
                    ).fetchone()
                    details: dict[str, Any] = {}
                    if event is not None:
                        try:
                            parsed = json.loads(event["details_json"])
                        except json.JSONDecodeError:
                            parsed = {}
                        if isinstance(parsed, dict):
                            details = parsed
                    day_key = str(details.get("day_key") or "").strip()
                    if not day_key:
                        scheduled = datetime.fromisoformat(str(task["scheduled_for"]))
                        if scheduled.tzinfo is None:
                            scheduled = scheduled.replace(tzinfo=UTC)
                        try:
                            zone = ZoneInfo(str(task["timezone"] or "UTC"))
                        except ZoneInfoNotFoundError:
                            zone = UTC
                        day_key = scheduled.astimezone(zone).date().isoformat()
                    connection.execute(
                        """
                        UPDATE diary_entries
                        SET status = 'sent', updated_at = ?
                        WHERE day_key = ? AND status = 'pending'
                        """,
                        (now, day_key),
                    )
            connection.commit()

    async def mark_outbound_failed(
        self,
        message_id: str,
        error: str,
        *,
        retry_base_seconds: int = 30,
    ) -> None:
        await asyncio.to_thread(
            self._mark_outbound_failed_sync,
            message_id,
            error,
            retry_base_seconds,
        )

    def _mark_outbound_failed_sync(
        self,
        message_id: str,
        error: str,
        retry_base_seconds: int,
    ) -> None:
        if not (1 <= retry_base_seconds <= 3600):
            raise ValueError("outbox retry base must be between 1 and 3600 seconds")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM outbox WHERE id = ?",
                (message_id,),
            ).fetchone()
            attempts = max(1, int(row["attempts"])) if row else 1
            delay = min(retry_base_seconds * (2 ** (attempts - 1)), 3600)
            connection.execute(
                """
                UPDATE outbox SET status = 'failed', last_error = ?, available_at = ?
                WHERE id = ? AND status = 'sending'
                """,
                (
                    error[:1000],
                    (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(),
                    message_id,
                ),
            )

    async def outbound_effect_scope(self, message_id: str) -> CapabilityScope:
        return await asyncio.to_thread(self._outbound_effect_scope_sync, message_id)

    def _outbound_effect_scope_sync(self, message_id: str) -> CapabilityScope:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    EXISTS(
                        SELECT 1 FROM reply_delivery_evidence
                        WHERE outbox_id = ? AND outcome = 'queued'
                    ) AS is_reply,
                    EXISTS(
                        SELECT 1 FROM proactive_message_tasks WHERE outbox_id = ?
                    ) AS is_proactive,
                    EXISTS(
                        SELECT 1 FROM owner_report_runtime WHERE outbox_id = ?
                    ) OR EXISTS(
                        SELECT 1 FROM outbox
                        WHERE id = ? AND idempotency_key LIKE 'owner_report:%'
                    ) AS is_owner_report
                """,
                (message_id, message_id, message_id, message_id),
            ).fetchone()
        matches = int(row["is_reply"]) + int(row["is_proactive"]) + int(row["is_owner_report"])
        if matches > 1:
            raise ValueError("outbox item has conflicting effect ownership")
        if row["is_proactive"]:
            return CapabilityScope.QQ_PROACTIVE
        if row["is_owner_report"]:
            return CapabilityScope.OWNER_REPORT
        return CapabilityScope.QQ_REPLY

    async def mark_outbound_readiness_blocked(self, message_id: str, reason: str) -> bool:
        return await asyncio.to_thread(
            self._mark_outbound_readiness_blocked_sync,
            message_id,
            reason,
        )

    def _mark_outbound_readiness_blocked_sync(self, message_id: str, reason: str) -> bool:
        now = _utc_now()
        safe_reason = reason[:200]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE outbox SET status = 'rejected', last_error = ?
                WHERE id = ? AND status = 'sending'
                """,
                (safe_reason, message_id),
            )
            if updated.rowcount == 1:
                task = connection.execute(
                    """
                    SELECT id FROM proactive_message_tasks
                    WHERE outbox_id = ? AND status = ?
                    """,
                    (message_id, ProactiveTaskStatus.ENQUEUED.value),
                ).fetchone()
                if task is not None:
                    connection.execute(
                        """
                        UPDATE proactive_message_tasks
                        SET status = ?, hold_reason = 'readiness_blocked', updated_at = ?
                        WHERE id = ? AND status = ?
                        """,
                        (
                            ProactiveTaskStatus.POLICY_BLOCKED.value,
                            now,
                            task["id"],
                            ProactiveTaskStatus.ENQUEUED.value,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO proactive_delivery_events(
                            id, task_id, event_type, reason, details_json, occurred_at
                        ) VALUES(?, ?, 'readiness_blocked', ?, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            task["id"],
                            safe_reason,
                            _json({"network_attempted": False, "automatic_retry": False}),
                            now,
                        ),
                    )
            connection.commit()
        return updated.rowcount == 1

    async def recover_sending_outbound(self) -> int:
        quarantined = await self.quarantine_interrupted_reply_deliveries()
        recovered = await asyncio.to_thread(self._recover_sending_outbound_sync)
        return quarantined + recovered

    def _recover_sending_outbound_sync(self) -> int:
        now = _utc_now()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE outbox
                SET status = 'failed', available_at = ?,
                    last_error = 'recovered_after_process_restart'
                WHERE status = 'sending'
                  AND NOT EXISTS(
                    SELECT 1 FROM reply_delivery_evidence AS mapping
                    WHERE mapping.outbox_id = outbox.id AND mapping.outcome = 'queued'
                  )
                """,
                (now,),
            )
        return updated.rowcount

    async def counts(self) -> dict[str, int]:
        return await asyncio.to_thread(self._counts_sync)

    def _counts_sync(self) -> dict[str, int]:
        tables: Iterable[str] = (
            "users",
            "conversations",
            "messages",
            "outbox",
            "control_commands",
            "qzone_posts",
            "approval_requests",
            "owner_reports",
            "friend_baselines",
            "user_relationships",
            "identity_evidence",
            "history_access_policies",
            "privacy_scope_policies",
            "privacy_requests",
            "data_access_log",
            "approval_payloads",
            "friend_baseline_candidates",
            "admin_sessions",
            "privacy_job_artifacts",
            "privacy_delete_previews",
            "knowledge_documents",
            "user_understanding_profiles",
            "persona_profiles",
            "persona_profile_evidence",
            "memory_records",
            "memory_evidence",
            "memory_conflicts",
            "document_blobs",
            "document_chunks",
            "knowledge_processing_jobs",
            "knowledge_job_checkpoints",
            "knowledge_chunk_analyses",
            "inference_runs",
            "reply_candidates",
            "user_reply_style_policies",
            "model_call_events",
            "image_generation_tasks",
            "image_artifacts",
            "image_artifact_reviews",
            "image_orphan_scans",
            "proactive_user_policies",
            "proactive_message_tasks",
            "proactive_delivery_events",
            "qzone_schedule_policy",
            "qzone_post_runtime",
            "qzone_post_events",
            "owner_report_delivery_policy",
            "owner_report_runtime",
            "owner_report_events",
            "qzone_profile_access_policies",
            "qzone_profile_snapshots",
            "worker_runtime_state",
            "instance_owner",
            "bots",
            "chat_quota_overrides",
            "chat_quota_notices",
            "daily_summaries",
            "diary_entries",
            "proactive_materials",
            "privacy_subject_links",
            "reply_runs",
            "reply_run_triggers",
            "reply_context_manifests",
            "reply_run_leases",
            "reply_delivery_evidence",
        )
        with self._connect() as connection:
            return {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in tables
            }

    def worker_paused_sync(self, worker_name: str) -> bool:
        _validate_worker_name(worker_name)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT paused FROM worker_runtime_state WHERE worker_name = ?",
                (worker_name,),
            ).fetchone()
        return bool(row["paused"]) if row else False

    async def record_system_audit(
        self, action: str, subject_id: str, details: dict[str, Any]
    ) -> None:
        await asyncio.to_thread(self._record_system_audit_sync, action, subject_id, details)

    def _record_system_audit_sync(
        self, action: str, subject_id: str, details: dict[str, Any]
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES(?, 'system_maintenance', ?, ?, ?)
                """,
                (action, subject_id, _json(details), _utc_now()),
            )

    def set_worker_paused_sync(
        self,
        worker_name: str,
        paused: bool,
        updated_by: str = "operator",
    ) -> None:
        _validate_worker_name(worker_name)
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO worker_runtime_state(
                    worker_name, paused, updated_by, updated_at
                ) VALUES(?, ?, ?, ?)
                ON CONFLICT(worker_name) DO UPDATE SET
                    paused = excluded.paused,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (worker_name, int(paused), updated_by, now),
            )
            connection.execute(
                """
                INSERT INTO audit_log(action, subject_type, subject_id, details_json, created_at)
                VALUES('worker.pause_changed', 'worker', ?, ?, ?)
                """,
                (worker_name, _json({"paused": paused, "updated_by": updated_by}), now),
            )


WORKER_RUNTIME_NAMES = frozenset(
    {"knowledge", "proactive", "qzone", "owner_reports", "outbox", "image_orphan"}
)


def _validate_worker_name(worker_name: str) -> None:
    if worker_name not in WORKER_RUNTIME_NAMES:
        raise ValueError(f"unknown worker: {worker_name}")
