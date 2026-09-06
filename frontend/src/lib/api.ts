import { clearSessionToken, getSessionToken } from "./auth";

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getSessionToken();
  const headers = new Headers(init.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (
    init.body &&
    !(init.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401) {
    clearSessionToken();
    if (!window.location.pathname.startsWith("/login")) {
      window.location.assign("/login");
    }
  }
  if (!response.ok) {
    let detail = response.statusText;
    let code: string | undefined;
    try {
      const body = (await response.json()) as { detail?: string | { code?: string; message?: string } };
      if (typeof body.detail === "string") detail = body.detail;
      if (body.detail && typeof body.detail === "object") {
        detail = body.detail.message || detail;
        code = body.detail.code;
      }
    } catch {
      /* ignore */
    }
    throw new ApiError(response.status, detail, code);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export type BotAccount = {
  qq: string;
  label: string;
  enabled: boolean;
  quota_user_default: number;
  quota_group_default: number;
  quota_user_reply: string;
};

export type Identity = {
  brand: string;
  creator_name: string;
  owner_qq?: string;
  bots?: BotAccount[];
  locked: boolean;
  accounts_editable?: boolean;
};

export type QuotaOverride = {
  bot_qq: string;
  peer_kind: string;
  peer_id: string;
  daily_limit: number;
  today_bonus: number;
  bonus_day: string;
  display_name: string;
};

export type OpsSnapshot = {
  timezone: string;
  day: string;
  mode: string;
  onebot: { connected: boolean; last_event_at: string | null };
  attention: {
    pending_approvals: number;
    urgent_reports: number;
    needs_owner: number;
  };
  inbound_today: number;
  anomalies: {
    total: number;
    qzone_uncertain: number;
    outbox_failed: number;
    circuit_open: number;
    report_delivery_failed: number;
  };
  lanes: {
    shadow: Record<string, string | number>;
    outbound: Record<string, string | number>;
    qzone: Record<string, string | number>;
  };
  workers: { key: string; name: string; state: string }[];
};

export type SeriesPoint = {
  day: string;
  inbound: number;
  commands: number;
  approvals: number;
};

export type ActivityItem = {
  source: string;
  at: string;
  title: string;
  detail: string;
  href: string;
};

export type ProtectionSnapshot = {
  state: string;
  requests_used: number;
  request_limit: number;
  tokens_used?: number | null;
  token_limit?: number | null;
};

export type ReplyRunSummary = {
  id: string;
  bot_qq: string;
  conversation_kind: "private" | "group";
  peer_id: string;
  subject_user_qq?: string | null;
  stage: string;
  terminal: boolean;
  attempt_count: number;
  trigger_count: number;
  manifest_count: number;
  latest_delivery_outcome?: string | null;
  failure?: {
    code?: string | null;
    category: string;
    summary: string;
  } | null;
  created_at: string;
  updated_at: string;
};

export type ReplyRunDetail = ReplyRunSummary & {
  timestamps: Record<string, string | null>;
  triggers: {
    sequence: number;
    message_id: string;
    sender_qq: string;
    direction: string;
    occurred_at: string;
    text_preview: string;
    text_truncated: boolean;
    segment_types: string[];
  }[];
  policy: {
    snapshot_keys: string[];
    blockers: { code: string; source_class: string }[];
  };
  context_manifests: {
    id: string;
    revision: number;
    budget_chars?: number | null;
    used_chars?: number | null;
    rendered_sha256?: string | null;
    created_at: string;
    sections: {
      section_id?: string;
      source_class?: string;
      scope?: string;
      subject_qq?: string | null;
      policy_decision?: string;
      record_ids: string[];
      truncated: boolean;
      omitted_chars: number;
    }[];
  }[];
  model?: {
    status: string;
    mode: string;
    model_route: string;
    provider_request_id?: string | null;
    input_tokens?: number | null;
    output_tokens?: number | null;
    error_type?: string | null;
    created_at: string;
    completed_at?: string | null;
    candidate?: {
      id: string;
      status: string;
      content_chars: number;
      safety_flags: string[];
    } | null;
  } | null;
  reply_plan?: {
    bubble_count: number;
    bubbles: {
      sequence: number;
      idempotency_key: string;
      segment_types: string[];
      text_chars: number;
    }[];
  } | null;
  delivery: {
    outbox_id: string;
    bubble_sequence: number;
    attempt: number;
    outcome: string;
    provider_message_id?: string | null;
    source?: string | null;
    observed_at: string;
  }[];
  lease: {
    present: boolean;
    expired: boolean;
    owner?: string;
    acquired_at?: string;
    heartbeat_at?: string;
    expires_at?: string;
  };
};

export type ReplyPipelineReadiness = {
  mode: string;
  production_activation_ready: boolean;
  safe_observation_ready: boolean;
  checks: Record<string, boolean>;
  blockers: { code: string; stage: string }[];
  safeguards: Record<string, boolean>;
  summary: {
    total_runs: number;
    active_runs: number;
    stages: Record<string, number>;
    delivery_outcomes: Record<string, number>;
    leases: { total: number; expired: number };
  };
  runtime?: ReplyRuntimeSummary | null;
};

export type ReplyActivationMode =
  | "observe_only"
  | "shadow"
  | "owner_approved"
  | "limited_auto"
  | "auto";

export type ReplyRuntimeEligibility = {
  bot_qq: string;
  conversation_key: string;
  enabled: boolean;
  revision: number;
  updated_at: string;
};

export type ReplyRuntimeWorker = {
  configured_enabled: boolean;
  loop_running: boolean;
  lifecycle: string;
  last_iteration_at?: string | null;
  last_claim_at?: string | null;
  last_progress_at?: string | null;
  last_failure_code?: string | null;
  recovered_count: number;
  last_result?: string | null;
  blockers: string[];
};

export type ReplyRuntimeSummary = {
  requested_mode: ReplyActivationMode;
  effective_mode: ReplyActivationMode;
  configuration_ceiling: ReplyActivationMode;
  persistent_pause: boolean;
  revision: number;
  approval_backlog: number;
  eligibility_count: number;
  blockers: string[];
  worker: Partial<ReplyRuntimeWorker>;
};

export type ReplyRuntimeStatus = {
  state: {
    bot_qq: string;
    requested_mode: ReplyActivationMode;
    emergency_paused: boolean;
    revision: number;
    lifecycle: string;
    last_iteration_at?: string | null;
    last_claim_at?: string | null;
    last_progress_at?: string | null;
    last_failure_code?: string | null;
    recovered_count: number;
  };
  requested_mode: ReplyActivationMode;
  effective_mode: ReplyActivationMode;
  configuration_ceiling: ReplyActivationMode;
  blockers: string[];
  eligibility: ReplyRuntimeEligibility[];
  approval_backlog: number;
  worker: ReplyRuntimeWorker;
};

export type ReplyRuntimeApproval = {
  id: string;
  run_id: string;
  runtime_revision: number;
  status: string;
  expires_at: string;
  created_at: string;
  decided_at?: string | null;
};

export type ReplyRuntimePreview = {
  confirmation_token: string;
  action: string;
  payload: Record<string, unknown>;
  runtime_revision: number;
  readiness_hash: string;
  expires_at: string;
};

export type ApprovalItem = {
  id: string;
  request_type: string;
  subject_id: string;
  approval_code: string;
  requested_to?: string;
  expires_at: string;
  created_at: string;
  payload?: Record<string, unknown>;
};

export type OwnerReport = {
  id: string;
  severity: string;
  category: string;
  title: string;
  body?: string;
  status: string;
  related_type?: string | null;
  related_id?: string | null;
  created_at: string;
  acknowledged_at?: string | null;
  delivery_status?: string | null;
  delivery_policy?: string | null;
  occurrence_count?: number;
  events?: { event_type: string; reason?: string; occurred_at: string }[];
};

export type ReportSummary = {
  delivery_route_enabled: boolean;
  outbound_enabled: boolean;
  worker: {
    configured_enabled: boolean;
    active: boolean;
    paused?: boolean;
  };
};

export type ControlCommandResult = {
  status: string;
  command_id: string;
  data: Record<string, unknown>;
};

export type KnownUser = {
  user_qq: string;
  friend_state: string;
  state_source: string;
  confidence?: number;
  data_frozen: number;
  history_mode: string;
  last_seen_at?: string | null;
  first_seen_at?: string | null;
  updated_at?: string | null;
  operator_label?: string;
};

export type OperatorLabel = {
  subject_kind: "user" | "group";
  subject_id: string;
  label: string;
  updated_at?: string;
  updated_by?: string;
};

export type UserIdentitySummary = {
  friend_states: Record<string, number>;
  history_modes: Record<string, number>;
  qzone_profile_modes?: Record<string, number>;
  frozen_users: number;
  latest_baseline?: { captured_at: string; friend_count: number } | null;
};

export type PersonaProfileItem = {
  id: string;
  scope_type: string;
  scope_id: string;
  source_type: string;
  name: string;
  developer_definition: string;
  traits?: Record<string, unknown>;
  version: number;
  status: string;
  created_by: string;
  created_at: string;
};

export type PersonaPreview = {
  core_identity: { brand: string; creator_name: string; locked: boolean };
  core_directives: string[];
  base_definition: string | null;
  private_definition: string | null;
  derived_persona: Record<string, unknown> | null;
  user_context: Record<string, unknown> | null;
  applied_profile_ids: string[];
};

export type MemoryRecordItem = {
  id: string;
  user_qq: string;
  memory_kind: string;
  memory_key: string;
  value: Record<string, unknown>;
  source_type: string;
  source_id?: string | null;
  confidence: number;
  status: string;
  expires_at?: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
};

export type MemoryConflictItem = {
  id: string;
  user_qq: string;
  memory_key: string;
  left_memory_id: string;
  right_memory_id: string;
  status: string;
  created_at: string;
  left_value: Record<string, unknown>;
  right_value: Record<string, unknown>;
};

export type MemoryContextItem = {
  id: string;
  kind: string;
  key: string;
  value: Record<string, unknown>;
  confidence: number;
  source: string;
};

export type ManualMemoryResult = {
  id: string;
  status: string;
  deduplicated: boolean;
  conflict_id?: string | null;
};

export type KnowledgeJobItem = {
  id: string;
  document_id: string;
  user_qq: string;
  purpose: string;
  status: string;
  progress: number;
  requested_by: string;
  error?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  original_filename?: string;
  content_sha256?: string;
  text_length?: number;
  byte_count?: number;
  detected_format?: string;
  chunk_count?: number;
  analyzed_chunk_count?: number;
  checkpoint_state?: string | null;
  attempt_count?: number | null;
  approval_status?: string | null;
  approval_code?: string | null;
  result_preview?: Record<string, unknown> | null;
};

export type KnowledgeUploadResult = {
  document_id: string;
  job_id: string;
  status: string;
  details: Record<string, unknown>;
};

export type KnowledgeWorkerSnapshot = {
  configured_enabled: boolean;
  processing_route_enabled: boolean;
  active: boolean;
  paused: boolean;
  loop_running: boolean;
  currently_processing: boolean;
  poll_seconds: number;
  max_attempts: number;
  retry_base_seconds: number;
  last_started_at?: string | null;
  last_finished_at?: string | null;
  last_job_id?: string | null;
  last_result?: string | null;
  last_error_type?: string | null;
  next_retry_at?: string | null;
  completed_jobs: number;
  failed_attempts: number;
  exhausted_jobs: number;
  changed?: boolean;
};

export type ConversationContextPreview = {
  core_identity: { brand: string; creator_name: string; locked: boolean };
  authenticated_creator: boolean;
  core_directives: string[];
  persona: {
    base_definition: string | null;
    private_definition: string | null;
    derived_persona: Record<string, unknown> | null;
    applied_profile_ids: string[];
  };
  user_reference: Record<string, unknown> | null;
  memories: MemoryContextItem[];
  isolation: {
    private_user_layers_allowed: boolean;
    user_reference_is_instruction: boolean;
    memory_is_instruction: boolean;
    core_identity_priority: number;
  };
};

export type InferenceRunItem = {
  id: string;
  source_message_id: string;
  conversation_key: string;
  actor_qq: string;
  bot_qq?: string;
  mode: string;
  status: string;
  prompt_hash: string;
  model_route: string;
  provider_request_id?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  error_type?: string | null;
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
};

export type ReplyCandidateItem = {
  id: string;
  inference_run_id: string;
  source_message_id: string;
  conversation_kind: string;
  target_id: string;
  content: string;
  bubbles?: string[];
  status: string;
  safety_flags: string[] | Record<string, unknown>;
  created_at: string;
  decided_at?: string | null;
  decided_by?: string | null;
  prompt_hash?: string;
  model_route?: string;
  inference_status?: string;
  input_tokens?: number | null;
  output_tokens?: number | null;
};

export type InferenceRunDetail = InferenceRunItem & {
  candidate: ReplyCandidateItem | null;
};

export type ShadowReplayResult = {
  status: string;
  run_id?: string | null;
  candidate_id?: string | null;
  reason?: string | null;
  report_id?: string | null;
  outbox: boolean;
};

export type QzoneVisibility = 1 | 4 | 16 | 64 | 128;

export type QzonePostEvent = {
  id: string;
  event_type: string;
  reason?: string | null;
  occurred_at: string;
  details?: Record<string, unknown>;
};

export type QzonePostItem = {
  id: string;
  content: string;
  status: string;
  visibility: number;
  target_uins: string[];
  timezone?: string;
  qzone_tid?: string | null;
  scheduled_for?: string | null;
  scheduled_for_local?: string | null;
  original_scheduled_for?: string | null;
  original_scheduled_for_local?: string | null;
  next_eligible_at_local?: string | null;
  hold_reason?: string | null;
  last_error?: string | null;
  missed_policy?: string | null;
  approval_code?: string | null;
  created_at?: string;
  published_at?: string | null;
  events?: QzonePostEvent[];
};

export type QzoneWorkerSnapshot = {
  configured_enabled: boolean;
  publish_route_enabled: boolean;
  active: boolean;
  paused: boolean;
  automatic_network_retry: boolean;
  last_post_id?: string | null;
  last_result?: string | null;
  last_reason?: string | null;
  published_count: number;
  held_count: number;
  uncertain_count: number;
  missed_count: number;
  deleted_count: number;
  delete_uncertain_count: number;
  changed?: boolean;
};

export type QzoneSummary = {
  publisher: QzoneWorkerSnapshot;
  requires_owner_approval: boolean;
  uses_real_calendar_time: boolean;
  automatic_network_retry: boolean;
  summary: {
    by_status: Record<string, number>;
    next_scheduled_for?: string | null;
  };
};

export type QzonePolicy = {
  timezone: string;
  quiet_hours_enabled: boolean;
  quiet_start: string;
  quiet_end: string;
  quiet_behavior: string;
  daily_limit: number;
  minimum_interval_seconds: number;
  persisted?: boolean;
};

export type ProactiveTaskItem = {
  id: string;
  target_qq: string;
  content: string;
  status: string;
  timezone?: string;
  missed_policy?: string;
  scheduled_for?: string | null;
  scheduled_for_local?: string | null;
  original_scheduled_for_local?: string | null;
  next_eligible_at_local?: string | null;
  hold_reason?: string | null;
  approval_code?: string | null;
  created_at?: string;
  events?: {
    id: string;
    event_type: string;
    reason?: string | null;
    occurred_at: string;
  }[];
};

export type ProactiveUserPolicy = {
  user_qq: string;
  enabled: boolean;
  timezone: string;
  quiet_hours_enabled: boolean;
  quiet_start: string;
  quiet_end: string;
  quiet_behavior: string;
  daily_limit: number;
  minimum_interval_seconds: number;
  auto_content_enabled?: boolean;
  send_diary?: boolean;
  persisted?: boolean;
};

export type ProactiveSchedulerSnapshot = {
  configured_enabled: boolean;
  active: boolean;
  paused: boolean;
  uses_real_calendar_time?: boolean;
  last_task_id?: string | null;
  last_result?: string | null;
  last_reason?: string | null;
  enqueued_tasks: number;
  held_tasks: number;
  missed_tasks: number;
  reapproval_tasks: number;
  changed?: boolean;
};

export type OutboxWorkerSnapshot = {
  configured_enabled: boolean;
  active: boolean;
  paused: boolean;
  sent_count: number;
  failed_count: number;
  recovered_after_restart?: number;
  last_result?: string | null;
  changed?: boolean;
  sent?: number;
};

export type ProactiveSummary = {
  scheduler: ProactiveSchedulerSnapshot;
  delivery: OutboxWorkerSnapshot;
  outbound_enabled: boolean;
  requires_owner_approval: boolean;
  uses_real_calendar_time: boolean;
  summary: Record<string, unknown>;
};

export type PrivacyStatus = {
  default_history_access: string;
  live_history_read_enabled: boolean;
  qzone_profile_collection_enabled: boolean;
  privacy_jobs_enabled: boolean;
  document_import_enabled: boolean;
  document_ocr_enabled: boolean;
  image_content_review_enabled: boolean;
  image_orphan_scan_enabled: boolean;
  shadow_inference_enabled: boolean;
  knowledge_processing_enabled: boolean;
  frozen_users: number;
};

export type ControlCommandItem = {
  id: string;
  actor_qq: string;
  action: string;
  status: string;
  created_at: string;
  arguments?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
};

export type PrivacyRequestItem = {
  id: string;
  user_qq: string;
  request_kind: string;
  status: string;
  requested_by: string;
  reason: string;
  created_at: string;
  completed_at?: string | null;
  result?: Record<string, unknown> | null;
};

export type DataAccessItem = {
  id: number;
  user_qq: string;
  data_class: string;
  purpose: string;
  accessor: string;
  decision: "allowed" | "denied";
  policy: Record<string, unknown>;
  created_at: string;
};

export type DataAccessAudit = {
  items: DataAccessItem[];
  summary: {
    total: number;
    allowed: number;
    denied: number;
    data_classes: Record<string, number>;
  };
};

export type PrivacyArtifactVerification = {
  request_id: string;
  verified: boolean;
  reason: string;
  artifact: {
    artifact_type: string;
    path: string;
    sha256: string;
    byte_count: number;
    created_at: string;
  };
  bundle?: {
    format: string;
    generated_at?: string | null;
    data_classes: number;
    row_count: number;
  };
  file_archive?: {
    format: string;
    file_count: number;
    source_bytes: number;
  };
  artifacts?: Array<{
    verified: boolean;
    reason: string;
    artifact: PrivacyArtifactVerification["artifact"];
    bundle?: PrivacyArtifactVerification["bundle"];
    file_archive?: PrivacyArtifactVerification["file_archive"];
  }>;
  recovery: {
    automatic_restore_supported: boolean;
    mode: string;
    requires_new_owner_approval: boolean;
    blockers: string[];
  };
};

export type ReadinessProfile = "local_start" | "offline_shadow" | "controlled_real_effect";
export type CapabilityScope = "local_runtime" | "offline_inference" | "chat_model" | "vision_model" | "image_model" | "stats_model" | "qq_reply" | "qq_proactive" | "owner_report" | "qzone_publish" | "qzone_profile" | "live_history";
export type ReadinessIssue = { code: string; probe_code: string; capability_scope: CapabilityScope; safe_detail: string };
export type ReadinessProbe = { probe_code: string; status: "pass" | "warning" | "blocked" | "unknown" | "stale"; recorded_status: string; capability_scope: CapabilityScope; observed_at: string; expires_at: string; fresh: boolean; source: string; source_revision: string; safe_detail: string; remediation_code: string };
export type ReadinessSummaryItem = { profile: ReadinessProfile; capability_scope: CapabilityScope; evaluated: boolean; status: "not_evaluated" | "passed" | "blocked"; recorded_status?: string; revision?: number; evaluated_at?: string; evidence_expires_at?: string | null; stale_probe_count?: number; blocker_codes: string[]; warning_codes: string[]; correlation_id?: string };
export type ReadinessSummary = { bot_qq: string; process_instance_id: string; controlled_scope: CapabilityScope; profiles: ReadinessSummaryItem[] };
export type ReadinessDetail = ReadinessSummaryItem & { decision_id?: string; bot_qq: string; process_instance_id: string; current_instance?: boolean; blockers?: ReadinessIssue[]; warnings?: ReadinessIssue[]; probes?: ReadinessProbe[] };

export type ArtifactOwnerScope = "system" | "user";
export type ManagedArtifact = { artifact_id: string; artifact_type: string; owner_scope: ArtifactOwnerScope; owner_qq: string | null; display_name: string; size_bytes: number; created_at: string; verification_state: string; verification_revision: number; reference_state: string; reference_revision: number; retention_state: string; revision: number };
export type RetentionTarget = "migration_backups" | "privacy_exports" | "privacy_deletion_backups" | "imported_sources";
export type RetentionPreview = { confirmation_token: string; correlation_id: string; revision: number; policy_revision: number; expires_at: string; target_batch_type: RetentionTarget; candidate_ids: string[]; candidate_count: number; candidate_bytes: number; recoverable_quarantine: boolean; permanent_deletion: boolean };
export type RetentionConfirmResult = { status: string; batch_id: string; correlation_id: string; candidate_count: number; candidate_bytes: number; recoverable: boolean; permanent_deletion: boolean };
export type QuarantineBatch = { batch_id: string; batch_type: string; owner_scope: ArtifactOwnerScope; owner_qq: string | null; actor_id: string; process_instance_id: string; state: string; revision: number; created_at: string; updated_at: string; correlation_id: string; items: { artifact_id: string; sequence: number; state: string; error_code?: string | null }[] };

export type DatabasePreflight = {
  database_exists: boolean;
  database_name: string;
  database_bytes: number;
  modified_at?: string | null;
  current_schema_version: number;
  latest_schema_version: number;
  applied_versions: number[];
  needs_migration: boolean;
  backup_required: boolean;
  backup_enabled: boolean;
  integrity_ok: boolean;
  integrity_result: string;
  foreign_key_violations?: number | null;
  journal_mode?: string | null;
  ready: boolean;
  startup_action: string;
  startup_backup?: MigrationBackup | null;
  latest_backup?: MigrationBackup | null;
  backup_health: {
    verified: boolean;
    backup_count: number;
    verified_count: number;
    total_bytes: number;
    retention: {
      keep_latest: number;
      minimum_age_days: number;
      automatic_cleanup: boolean;
      candidate_count: number;
      candidate_bytes: number;
      cleanup_token?: string | null;
    };
  };
};

export type MigrationBackup = {
  format: string;
  backup_file: string;
  sha256: string;
  byte_count: number;
  source_schema_version: number;
  target_schema_version: number;
  created_at: string;
  integrity_result: string;
};

export type StickerCatalog = {
  root: string;
  outbound_attached: boolean;
  ready_tags: number;
  items: { tag: string; count: number; files: string[] }[];
};

export type SystemStatus = {
  brand?: string;
  mode: string;
  onebot?: {
    connected: boolean;
    last_event_at: string | null;
    auth_configured?: boolean;
    authenticated_bot_qq?: string | null;
    exact_bot?: boolean;
  };
  control?: {
    owner_reports_enabled: boolean;
    qzone_publish_enabled: boolean;
    qzone_profile_collection_enabled: boolean;
  };
  models?: {
    chat?: {
      configured: boolean;
      network_enabled: boolean;
      route_enabled: boolean;
      shadow_enabled?: boolean;
    };
    vision?: {
      configured: boolean;
      network_enabled: boolean;
      route_enabled: boolean;
      active?: boolean;
    };
    image?: {
      configured: boolean;
      network_enabled: boolean;
      route_enabled: boolean;
    };
    stats?: {
      configured: boolean;
      network_enabled: boolean;
      route_enabled: boolean;
    };
  };
};

export type QualificationCapability = "chat" | "vision" | "image" | "stats";

export type QualificationSuite = {
  suite_id: string;
  capability: QualificationCapability;
  version: string;
  fixture_ids: string[];
  fixture_count: number;
  blocking_check_codes: string[];
  source_kind: "repository_owned";
  sensitivity: "synthetic_public";
};

export type QualificationRoute = {
  capability: QualificationCapability;
  configured: boolean;
  route_enabled: boolean;
  network_enabled: boolean;
  qualified: boolean;
  model_identifier: string;
  sanitized_base_host: string;
  provider_protocol: string;
  suite_version: string;
  price_catalog_revision: string;
  fingerprint: string;
  activates_production: false;
};

export type QualificationDecision = {
  capability: QualificationCapability;
  status: "unqualified" | "passed" | "failed" | "stale" | "blocked";
  qualifies: boolean;
  activates_production: false;
  configured: boolean;
  route_enabled: boolean;
  model_identifier: string;
  sanitized_base_host: string;
  suite_version: string;
  blocker_codes: string[];
  advisory_score: string | null;
  run_id: string | null;
  evaluated_at: string;
  stale_reason: string | null;
};

export type QualificationPreview = {
  preview_id: string;
  confirmation_handle: string;
  capability: QualificationCapability;
  model_identifier: string;
  fixture_ids: string[];
  fixture_count: number;
  max_requests: number;
  max_input_tokens: number | null;
  max_output_tokens: number | null;
  max_images: number | null;
  planned_max_cost: string;
  conservative_max_cost: string;
  currency: string;
  expires_at: string;
  effect_isolation: string;
  price_catalog_revision: string;
  activates_production: false;
};

export type QualificationRunSummary = {
  run_id: string;
  preview_id: string;
  capability: QualificationCapability;
  state: string;
  execution_mode: string;
  suite_id: string;
  suite_version: string;
  model_identifier: string;
  sanitized_base_host: string;
  correlation_id: string;
  actor_id: string;
  created_at: string;
  activates_production: false;
  reason_code?: string;
};

export type QualificationCase = {
  case_id: string;
  run_id: string;
  fixture_id: string;
  fixture_hash: string;
  input_hash: string;
  state: string;
  idempotency_key: string;
  check_results: Array<{
    check_code: string;
    kind: string;
    passed: boolean | null;
    score: string | null;
    reason_code: string | null;
  }>;
  evidence: {
    latency_ms: number;
    input_tokens: number | null;
    output_tokens: number | null;
    image_count: number;
    cost_amount: string | null;
    cost_currency: string | null;
    provider_request_id: string | null;
    response_hash: string | null;
    artifact_id: string | null;
  };
  started_at: string | null;
  finished_at: string | null;
  reason_code: string | null;
};

export type QualificationRunDetail = QualificationRunSummary & {
  cases: QualificationCase[];
  cost_evidence: Record<string, unknown> | null;
  artifacts: Array<{
    artifact_id: string;
    mime_type: string;
    size_bytes: number;
    pixel_width: number | null;
    pixel_height: number | null;
  }>;
};

export type UserReplyStyle = {
  user_qq: string;
  min_bubbles: number;
  max_bubbles: number;
  sentence_min_chars: number;
  sentence_max_chars: number;
  max_allowed_bubbles?: number;
  typical_max_bubbles?: number;
  persisted?: boolean;
  outbound_attached?: boolean;
};

export type UserDetail = {
  user_qq: string;
  operator_label?: string;
  relationship: {
    friend_state: string;
    state_source: string;
    confidence: number;
    data_frozen: number;
    owner_overridden_by?: string | null;
    updated_at?: string;
  } | null;
  history_policy: {
    mode: string;
    source?: string;
    selected_from?: string | null;
    selected_to?: string | null;
    max_messages?: number | null;
    one_time_remaining?: number | null;
    reason?: string;
  };
  qzone_profile_policy: {
    mode: string;
    source?: string;
    max_items?: number | null;
    one_time_remaining?: number | null;
    expires_at?: string | null;
  };
  latest_collection?: {
    fetched_at?: string | null;
    expires_at?: string | null;
    scanned: number;
    images_seen: number;
    covers_seen: number;
    videos_unread: number;
    candidate_count: number;
  } | null;
  identity_evidence: {
    id: string;
    evidence_type: string;
    source: string;
    value: unknown;
    observed_at: string;
  }[];
  reply_style?: UserReplyStyle;
};

export type StatsOverview = {
  range: string;
  timezone: string;
  start_day: string;
  end_day: string;
  private_users: number;
  groups: number;
  rounds: number;
  user_messages: number;
  user_images: number;
  bot_messages: number;
  bot_images: number;
  trend: Array<{ day: string; inbound: number; outbound: number }>;
  tokens: {
    total_tokens: number;
    users: Array<{ peer_id: string; tokens: number }>;
    groups: Array<{ peer_id: string; tokens: number }>;
  };
};

export type DailySummary = {
  day_key: string;
  entries: Array<{ time: string; text: string; alias: string }>;
  source_peer_ids?: string[];
  generated_at: string | null;
};

export type DiaryEntry = {
  day_key: string;
  content: string;
  status?: string;
};

export type MaterialItem = {
  id: string;
  title: string;
  content: string;
  uploader_claim: string;
  ai_verdict: string;
  ai_reason?: string;
  enabled?: boolean;
};

export type ChatlogPeer = {
  kind: string;
  peer_id: string;
  messages: number;
};

export type ChatlogItem = {
  id: string;
  direction: string;
  plain_text?: string;
  occurred_at: string;
};

export const api = {
  createSession: (bootstrapToken: string) =>
    request<{ access_token: string; expires_at: string }>(
      "/api/v1/auth/session",
      {
        method: "POST",
        headers: { Authorization: `Bearer ${bootstrapToken}` },
      },
    ),
  identity: () => request<Identity>("/api/v1/system/identity"),
  status: () => request<SystemStatus>("/api/v1/status"),
  opsSnapshot: () => request<OpsSnapshot>("/api/v1/ops/snapshot"),
  opsSeries: (days: 1 | 7 | 30) =>
    request<{ points: SeriesPoint[] }>(`/api/v1/ops/series?days=${days}`),
  opsActivity: (limit = 8) => request<{ items: ActivityItem[] }>(`/api/v1/ops/activity?limit=${Math.max(1, Math.min(limit, 50))}`),
  replyRuns: (stage = "", limit = 50, offset = 0) => {
    const query = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    if (stage) query.set("stage", stage);
    return request<{
      items: ReplyRunSummary[];
      total: number;
      limit: number;
      offset: number;
    }>(`/api/v1/reply-runs?${query.toString()}`);
  },
  replyRunDetail: (runId: string) =>
    request<ReplyRunDetail>(`/api/v1/reply-runs/${encodeURIComponent(runId)}`),
  replyPipelineReadiness: () =>
    request<ReplyPipelineReadiness>("/api/v1/reply-pipeline/readiness"),
  replyRuntime: () => request<ReplyRuntimeStatus>("/api/v1/reply-runtime"),
  replyRuntimeApprovals: (status = "pending") =>
    request<{ items: ReplyRuntimeApproval[] }>(
      `/api/v1/reply-runtime/approvals?status=${encodeURIComponent(status)}`,
    ),
  previewReplyRuntime: (action: string, payload: Record<string, unknown>) =>
    request<ReplyRuntimePreview>("/api/v1/reply-runtime/preview", {
      method: "POST",
      body: JSON.stringify({ action, payload }),
    }),
  confirmReplyRuntime: (confirmationToken: string) =>
    request<ReplyRuntimeStatus>("/api/v1/reply-runtime/confirm", {
      method: "POST",
      body: JSON.stringify({ confirmation_token: confirmationToken }),
    }),
  approvals: () =>
    request<{ items: ApprovalItem[] }>("/api/v1/control/approvals"),
  reports: (status = "pending") =>
    request<{ items: OwnerReport[] }>(
      `/api/v1/owner/reports?status=${status}&limit=20`,
    ),
  reportDetail: (id: string) =>
    request<OwnerReport>(`/api/v1/owner/reports/${id}`),
  reportSummary: () => request<ReportSummary>("/api/v1/owner/reports/summary"),
  runCommand: (action: string, arguments_: Record<string, string>) =>
    request<ControlCommandResult>("/api/v1/control/commands", {
      method: "POST",
      body: JSON.stringify({ action, arguments: arguments_ }),
    }),
  users: () =>
    request<{
      items: KnownUser[];
      summary: UserIdentitySummary;
      default_history_access: string;
      live_history_read_enabled: boolean;
      classification_rule: string;
    }>("/api/v1/users"),
  userDetail: (qq: string) => request<UserDetail>(`/api/v1/users/${qq}`),
  operatorLabels: () => request<{ items: OperatorLabel[] }>("/api/v1/operator-labels"),
  setOperatorLabel: (kind: "user" | "group", id: string, label: string) =>
    request<ControlCommandResult>("/api/v1/control/commands", {
      method: "POST",
      body: JSON.stringify({
        action: kind === "group" ? "group.set_label" : "user.set_label",
        arguments:
          kind === "group" ? { group_qq: id, label } : { user_qq: id, label },
      }),
    }),
  userReplyStyle: (qq: string) =>
    request<UserReplyStyle>(`/api/v1/users/${qq}/reply-style`),
  updateUserReplyStyle: (
    qq: string,
    body: {
      min_bubbles: number;
      max_bubbles: number;
      sentence_min_chars: number;
      sentence_max_chars: number;
    },
  ) =>
    request<UserReplyStyle>(`/api/v1/users/${qq}/reply-style`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  personas: (user_qq?: string) =>
    request<{ items: PersonaProfileItem[] }>(
      `/api/v1/personas${user_qq ? `?user_qq=${user_qq}` : ""}`,
    ),
  savePersona: (body: {
    scope: "global" | "private_user";
    user_qq?: string;
    name: string;
    definition: string;
  }) =>
    request<{
      id: string;
      scope: string;
      scope_id: string;
      source: string;
      version: number;
    }>("/api/v1/personas/manual", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  personaPreview: (conversation_kind: "private" | "group", peer_id: string) =>
    request<PersonaPreview>(
      `/api/v1/personas/context-preview?conversation_kind=${conversation_kind}&peer_id=${peer_id}`,
    ),
  memories: (user_qq: string) =>
    request<{ items: MemoryRecordItem[] }>(`/api/v1/users/${user_qq}/memories`),
  createManualMemory: (
    user_qq: string,
    body: {
      kind:
        | "fact"
        | "preference"
        | "relationship_event"
        | "conversation_summary";
      key: string;
      value: Record<string, unknown>;
      expires_at?: string;
    },
  ) =>
    request<ManualMemoryResult>(`/api/v1/users/${user_qq}/memories/manual`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  forgetMemory: (user_qq: string, memory_id: string) =>
    request<{ forgotten: boolean }>(
      `/api/v1/users/${user_qq}/memories/${memory_id}`,
      {
        method: "DELETE",
      },
    ),
  memoryPreview: (user_qq: string, conversation_kind: "private" | "group") =>
    request<{ items: MemoryContextItem[] }>(
      `/api/v1/users/${user_qq}/memory-context-preview?conversation_kind=${conversation_kind}`,
    ),
  memoryConflicts: (user_qq?: string) =>
    request<{ items: MemoryConflictItem[] }>(
      `/api/v1/memory/conflicts${user_qq ? `?user_qq=${user_qq}` : ""}`,
    ),
  resolveMemoryConflict: (
    conflict_id: string,
    resolution: "keep_left" | "keep_right" | "forget_both",
  ) =>
    request<{ resolved: boolean }>(
      `/api/v1/memory/conflicts/${conflict_id}/resolve`,
      {
        method: "POST",
        body: JSON.stringify({ resolution }),
      },
    ),
  knowledgeJobs: (user_qq?: string) =>
    request<{ items: KnowledgeJobItem[] }>(
      `/api/v1/knowledge/jobs${user_qq ? `?user_qq=${user_qq}` : ""}`,
    ),
  knowledgeJob: (job_id: string) =>
    request<KnowledgeJobItem>(`/api/v1/knowledge/jobs/${job_id}`),
  uploadKnowledgeDocument: (
    user_qq: string,
    purpose: "user_understanding" | "persona_design",
    file: File,
  ) => {
    const body = new FormData();
    body.set("user_qq", user_qq);
    body.set("purpose", purpose);
    body.set("document", file);
    return request<KnowledgeUploadResult>("/api/v1/knowledge/documents", {
      method: "POST",
      body,
    });
  },
  processKnowledgeJob: (job_id: string) =>
    request<{
      job_id: string;
      status: string;
      completed_chunks: number;
      total_chunks: number;
    }>(`/api/v1/knowledge/jobs/${job_id}/process`, { method: "POST" }),
  approveKnowledgeJob: (job_id: string) =>
    request<Record<string, unknown>>(
      `/api/v1/knowledge/jobs/${job_id}/approve`,
      {
        method: "POST",
      },
    ),
  knowledgeWorker: () =>
    request<KnowledgeWorkerSnapshot>("/api/v1/knowledge/worker"),
  knowledgeWorkerRunOnce: () =>
    request<{
      status: string;
      job_id?: string | null;
      reason?: string | null;
      worker: KnowledgeWorkerSnapshot;
    }>("/api/v1/knowledge/worker/run-once", { method: "POST" }),
  knowledgeWorkerPause: () =>
    request<KnowledgeWorkerSnapshot>("/api/v1/knowledge/worker/pause", {
      method: "POST",
    }),
  knowledgeWorkerResume: () =>
    request<KnowledgeWorkerSnapshot>("/api/v1/knowledge/worker/resume", {
      method: "POST",
    }),
  privacyStatus: () => request<PrivacyStatus>("/api/v1/privacy/status"),
  systemPreflight: () => request<DatabasePreflight>("/api/v1/system/preflight"),
  readinessSummary: (controlled_scope: CapabilityScope = "qq_reply", bot_qq?: string) => {
    const query = new URLSearchParams({ controlled_scope });
    if (bot_qq) query.set("bot_qq", bot_qq);
    return request<ReadinessSummary>(`/api/v1/operations/readiness/summary?${query}`);
  },
  readinessCurrent: (profile: ReadinessProfile, capability_scope: CapabilityScope, bot_qq?: string, process_instance_id?: string) => {
    const query = new URLSearchParams({ profile, capability_scope });
    if (bot_qq) query.set("bot_qq", bot_qq);
    if (process_instance_id) query.set("process_instance_id", process_instance_id);
    return request<ReadinessDetail>(`/api/v1/operations/readiness/current?${query}`);
  },
  refreshReadiness: (body: { bot_qq: string; process_instance_id: string; profile: ReadinessProfile; capability_scope: CapabilityScope }) =>
    request<ReadinessDetail>("/api/v1/operations/readiness/refresh", { method: "POST", body: JSON.stringify(body) }),
  managedArtifacts: (filters: { owner_scope?: ArtifactOwnerScope; owner_qq?: string; artifact_type?: string; verification_state?: string; reference_state?: string; retention_state?: string; limit?: number; bot_qq?: string } = {}) => {
    const query = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => { if (value !== undefined && value !== "") query.set(key, String(value)); });
    return request<{ items: ManagedArtifact[]; count: number; paths_exposed: false }>(`/api/v1/operations/artifacts?${query}`);
  },
  refreshManagedArtifacts: (body: { bot_qq: string; process_instance_id: string; owner_scope?: ArtifactOwnerScope; owner_qq?: string }) =>
    request<{ items: ManagedArtifact[]; count: number; paths_exposed: false; filesystem_mutated: false; correlation_id: string }>("/api/v1/operations/artifacts/refresh", { method: "POST", body: JSON.stringify(body) }),
  previewArtifactRetention: (body: { bot_qq: string; process_instance_id: string; target_batch_type: RetentionTarget; owner_scope: ArtifactOwnerScope; owner_qq?: string }) =>
    request<RetentionPreview>("/api/v1/operations/artifacts/retention/preview", { method: "POST", body: JSON.stringify(body) }),
  confirmArtifactRetention: (body: { bot_qq: string; process_instance_id: string; confirmation_token: string; expected_revision: number }) =>
    request<RetentionConfirmResult>("/api/v1/operations/artifacts/retention/confirm", { method: "POST", body: JSON.stringify(body) }),
  quarantineHistory: (filters: { owner_scope?: ArtifactOwnerScope; owner_qq?: string; state?: string; bot_qq?: string } = {}) => {
    const query = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => { if (value) query.set(key, value); });
    return request<{ items: QuarantineBatch[]; count: number; paths_exposed: false }>(`/api/v1/operations/artifacts/quarantine?${query}`);
  },
  cleanupMigrationBackups: (cleanup_token: string) =>
    request<{
      status: string;
      candidate_count: number;
      candidate_bytes: number;
      files: string[];
      quarantine_id: string;
      recoverable: boolean;
    }>("/api/v1/system/backups/cleanup", {
      method: "POST",
      body: JSON.stringify({ cleanup_token }),
    }),
  conversationPreview: (
    conversation_kind: "private" | "group",
    peer_id: string,
    actor_qq: string,
  ) =>
    request<ConversationContextPreview>(
      `/api/v1/conversations/context-preview?conversation_kind=${conversation_kind}&peer_id=${peer_id}&actor_qq=${actor_qq}`,
    ),
  inferenceRuns: (limit = 100) =>
    request<{ items: InferenceRunItem[] }>(
      `/api/v1/inference/runs?limit=${limit}`,
    ),
  inferenceRun: (run_id: string) =>
    request<InferenceRunDetail>(`/api/v1/inference/runs/${run_id}`),
  replayShadow: (message_id: string) =>
    request<ShadowReplayResult>("/api/v1/inference/replay", {
      method: "POST",
      body: JSON.stringify({ message_id }),
    }),
  replyCandidates: (status?: string) =>
    request<{ items: ReplyCandidateItem[] }>(
      `/api/v1/reply-candidates${status ? `?status=${status}` : ""}`,
    ),
  qzoneSummary: () => request<QzoneSummary>("/api/v1/qzone/summary"),
  qzonePosts: (status?: string) =>
    request<{ items: QzonePostItem[] }>(
      `/api/v1/qzone/posts${status ? `?status=${encodeURIComponent(status)}` : ""}`,
    ),
  qzonePost: (post_id: string) =>
    request<QzonePostItem>(`/api/v1/qzone/posts/${post_id}`),
  createQzoneDraft: (body: {
    content: string;
    visibility: QzoneVisibility;
    target_uins?: string[];
  }) =>
    request<QzonePostItem>("/api/v1/qzone/drafts", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createQzonePost: (body: {
    content: string;
    visibility: QzoneVisibility;
    target_uins?: string[];
    scheduled_for?: string;
    timezone?: string;
  }) =>
    request<QzonePostItem & { approval_code?: string }>("/api/v1/qzone/posts", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancelQzonePost: (post_id: string) =>
    request<QzonePostItem>(`/api/v1/qzone/posts/${post_id}/cancel`, {
      method: "POST",
    }),
  revokeQzonePost: (post_id: string) =>
    request<QzonePostItem & { approval_code?: string }>(
      `/api/v1/qzone/posts/${post_id}/revoke`,
      {
        method: "POST",
      },
    ),
  qzonePolicy: () => request<QzonePolicy>("/api/v1/qzone/policy"),
  updateQzonePolicy: (body: QzonePolicy) =>
    request<QzonePolicy>("/api/v1/qzone/policy", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  qzoneWorker: () => request<QzoneWorkerSnapshot>("/api/v1/qzone/worker"),
  qzoneWorkerRunOnce: () =>
    request<{
      status: string;
      post_id?: string | null;
      reason?: string | null;
      worker: QzoneWorkerSnapshot;
    }>("/api/v1/qzone/worker/run-once", { method: "POST" }),
  qzoneWorkerPause: () =>
    request<QzoneWorkerSnapshot>("/api/v1/qzone/worker/pause", {
      method: "POST",
    }),
  qzoneWorkerResume: () =>
    request<QzoneWorkerSnapshot>("/api/v1/qzone/worker/resume", {
      method: "POST",
    }),
  proactiveSummary: () =>
    request<ProactiveSummary>("/api/v1/proactive/summary"),
  proactiveTasks: (params?: { target_qq?: string; status?: string }) => {
    const query = new URLSearchParams();
    if (params?.target_qq) query.set("target_qq", params.target_qq);
    if (params?.status) query.set("status", params.status);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return request<{ items: ProactiveTaskItem[] }>(
      `/api/v1/proactive/tasks${suffix}`,
    );
  },
  proactiveTask: (task_id: string) =>
    request<ProactiveTaskItem>(`/api/v1/proactive/tasks/${task_id}`),
  createProactiveTask: (body: {
    target_qq: string;
    content: string;
    scheduled_for: string;
    timezone?: string;
    missed_policy?: "skip" | "send_within_grace" | "require_reapproval";
  }) =>
    request<ProactiveTaskItem & { approval_code?: string }>(
      "/api/v1/proactive/tasks",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),
  cancelProactiveTask: (task_id: string) =>
    request<ProactiveTaskItem>(`/api/v1/proactive/tasks/${task_id}/cancel`, {
      method: "POST",
    }),
  proactivePolicy: (user_qq: string) =>
    request<ProactiveUserPolicy>(`/api/v1/proactive/users/${user_qq}/policy`),
  updateProactivePolicy: (user_qq: string, body: ProactiveUserPolicy) =>
    request<ProactiveUserPolicy>(`/api/v1/proactive/users/${user_qq}/policy`, {
      method: "PUT",
      body: JSON.stringify({
        enabled: body.enabled,
        timezone: body.timezone,
        quiet_hours_enabled: body.quiet_hours_enabled,
        quiet_start: body.quiet_start,
        quiet_end: body.quiet_end,
        quiet_behavior: body.quiet_behavior,
        daily_limit: body.daily_limit,
        minimum_interval_seconds: body.minimum_interval_seconds,
        auto_content_enabled: Boolean(body.auto_content_enabled),
        send_diary: Boolean(body.send_diary),
      }),
    }),
  proactiveWorker: () =>
    request<ProactiveSchedulerSnapshot>("/api/v1/proactive/worker"),
  proactiveWorkerRunOnce: () =>
    request<{
      status: string;
      task_id?: string | null;
      reason?: string | null;
      worker: ProactiveSchedulerSnapshot;
    }>("/api/v1/proactive/worker/run-once", { method: "POST" }),
  proactiveWorkerPause: () =>
    request<ProactiveSchedulerSnapshot>("/api/v1/proactive/worker/pause", {
      method: "POST",
    }),
  proactiveWorkerResume: () =>
    request<ProactiveSchedulerSnapshot>("/api/v1/proactive/worker/resume", {
      method: "POST",
    }),
  outboxWorker: () => request<OutboxWorkerSnapshot>("/api/v1/outbox/worker"),
  outboxWorkerRunOnce: () =>
    request<OutboxWorkerSnapshot>("/api/v1/outbox/worker/run-once", {
      method: "POST",
    }),
  outboxWorkerPause: () =>
    request<OutboxWorkerSnapshot>("/api/v1/outbox/worker/pause", {
      method: "POST",
    }),
  outboxWorkerResume: () =>
    request<OutboxWorkerSnapshot>("/api/v1/outbox/worker/resume", {
      method: "POST",
    }),
  reportWorkerPause: () =>
    request<Record<string, unknown>>("/api/v1/owner/reports/worker/pause", {
      method: "POST",
    }),
  controlCommands: (limit = 30) =>
    request<{ items: ControlCommandItem[] }>(
      `/api/v1/control/commands?limit=${limit}`,
    ),
  privacyRequests: (status?: string) =>
    request<{ items: PrivacyRequestItem[] }>(
      `/api/v1/privacy/requests${status ? `?status=${status}` : ""}`,
    ),
  privacyAccessLog: (params?: {
    user_qq?: string;
    decision?: "allowed" | "denied";
    data_class?: string;
    limit?: number;
  }) => {
    const query = new URLSearchParams();
    if (params?.user_qq) query.set("user_qq", params.user_qq);
    if (params?.decision) query.set("decision", params.decision);
    if (params?.data_class) query.set("data_class", params.data_class);
    query.set("limit", String(params?.limit ?? 100));
    return request<DataAccessAudit>(
      `/api/v1/privacy/access-log?${query.toString()}`,
    );
  },
  verifyPrivacyArtifact: (request_id: string) =>
    request<PrivacyArtifactVerification>(
      `/api/v1/privacy/requests/${request_id}/artifact/verify`,
    ),
  stickers: () => request<StickerCatalog>("/api/v1/stickers"),
  protection: () =>
    request<{ chat: ProtectionSnapshot; image: ProtectionSnapshot }>(
      "/api/v1/models/protection",
    ),
  accounts: () =>
    request<{ owner_qq: string; bots: BotAccount[] }>("/api/v1/accounts"),
  updateOwner: (owner_qq: string) =>
    request<{ owner_qq: string; bots: BotAccount[] }>(
      "/api/v1/accounts/owner",
      {
        method: "PUT",
        body: JSON.stringify({ owner_qq }),
      },
    ),
  addBot: (qq: string, label: string) =>
    request<{ owner_qq: string; bots: BotAccount[] }>("/api/v1/accounts/bots", {
      method: "POST",
      body: JSON.stringify({ qq, label }),
    }),
  setBotEnabled: (qq: string, enabled: boolean) =>
    request<{ owner_qq: string; bots: BotAccount[] }>(
      `/api/v1/accounts/bots/${qq}/${enabled ? "enable" : "disable"}`,
      { method: "POST" },
    ),
  updateBot: (
    qq: string,
    body: Partial<
      Pick<
        BotAccount,
        | "label"
        | "quota_user_default"
        | "quota_group_default"
        | "quota_user_reply"
      >
    >,
  ) =>
    request<{ owner_qq: string; bots: BotAccount[] }>(
      `/api/v1/accounts/bots/${qq}`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
    ),
  quotas: (bot_qq: string, peer_kind?: string) =>
    request<{ items: QuotaOverride[] }>(
      `/api/v1/quotas?bot_qq=${bot_qq}${peer_kind ? `&peer_kind=${peer_kind}` : ""}`,
    ),
  setQuotaLimit: (
    bot_qq: string,
    peer_kind: "private" | "group",
    peer_id: string,
    body: { daily_limit: number; display_name?: string },
  ) =>
    request<QuotaOverride>(`/api/v1/quotas/${bot_qq}/${peer_kind}/${peer_id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  addTodayBonus: (
    bot_qq: string,
    peer_kind: "private" | "group",
    peer_id: string,
    amount: number,
  ) =>
    request<QuotaOverride>(
      `/api/v1/quotas/${bot_qq}/${peer_kind}/${peer_id}/today-bonus`,
      {
        method: "POST",
        body: JSON.stringify({ amount }),
      },
    ),
  statsOverview: (range: "today" | "7d" | "30d" = "today") =>
    request<StatsOverview>(`/api/v1/stats/overview?range=${range}`),
  statsSummary: (date: string) =>
    request<DailySummary>(
      `/api/v1/stats/summary?date=${encodeURIComponent(date)}`,
    ),
  generateStatsSummary: (date: string, force = false) =>
    request<DailySummary>(
      `/api/v1/stats/summary?date=${encodeURIComponent(date)}&force=${force ? "true" : "false"}`,
      { method: "POST" },
    ),
  chatlogPeers: () =>
    request<{ items: ChatlogPeer[] }>("/api/v1/chatlog/peers"),
  chatlog: (kind: "private" | "group", peer: string, after_id = "") =>
    request<{ items: ChatlogItem[] }>(
      `/api/v1/chatlog?kind=${kind}&peer=${encodeURIComponent(peer)}${after_id ? `&after_id=${encodeURIComponent(after_id)}` : ""}`,
    ),
  diaryEntries: () => request<{ items: DiaryEntry[] }>("/api/v1/diary"),
  diaryEntry: (day_key: string) =>
    request<DiaryEntry>(`/api/v1/diary/${day_key}`),
  saveDiary: (day_key: string, content: string) =>
    request<DiaryEntry>("/api/v1/diary", {
      method: "PUT",
      body: JSON.stringify({ day_key, content }),
    }),
  materials: () => request<{ items: MaterialItem[] }>("/api/v1/materials"),
  createMaterial: (body: {
    title: string;
    content: string;
    uploader_claim: string;
    target_qq?: string;
  }) =>
    request<MaterialItem>("/api/v1/materials", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  setMaterialEnabled: (id: string, enabled: boolean) =>
    request<MaterialItem>(`/api/v1/materials/${id}/enable`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  qualificationSuites: () =>
    request<{ items: QualificationSuite[] }>("/api/v1/qualification/suites"),
  qualificationRoutes: () =>
    request<{ items: QualificationRoute[] }>("/api/v1/qualification/routes"),
  qualificationDecisions: () =>
    request<{ items: QualificationDecision[] }>("/api/v1/qualification/decisions"),
  previewQualification: (body: {
    capability: QualificationCapability;
    fixture_ids?: string[];
    max_input_tokens?: number;
    max_output_tokens?: number;
    max_images?: number;
  }) =>
    request<QualificationPreview>("/api/v1/qualification/previews", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  confirmQualification: (confirmation_handle: string, idempotency_key: string) =>
    request<QualificationRunSummary>("/api/v1/qualification/confirmations", {
      method: "POST",
      body: JSON.stringify({ confirmation_handle, idempotency_key }),
    }),
  cancelQualificationRun: (run_id: string) =>
    request<QualificationRunSummary>(`/api/v1/qualification/runs/${encodeURIComponent(run_id)}/cancel`, {
      method: "POST",
    }),
  qualificationRuns: (params: { capability?: string; limit?: number; offset?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.capability) query.set("capability", params.capability);
    query.set("limit", String(params.limit ?? 20));
    query.set("offset", String(params.offset ?? 0));
    return request<{ items: QualificationRunSummary[]; total: number; limit: number; offset: number }>(
      `/api/v1/qualification/runs?${query.toString()}`,
    );
  },
  qualificationRunDetail: (run_id: string) =>
    request<QualificationRunDetail>(`/api/v1/qualification/runs/${encodeURIComponent(run_id)}`),
};
