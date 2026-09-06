export const DIARY_STATUS_LABELS: Record<string, string> = {
  pending: "待发",
  sent: "已发",
  expired: "过期",
};

export const MATERIAL_VERDICT_LABELS: Record<string, string> = {
  match: "相符",
  mismatch: "不符",
  pending: "核对中",
};

export const HISTORY_ACCESS_LABELS: Record<string, string> = {
  deny: "禁止",
  allow: "允许",
};

export const COMMAND_ACTION_LABELS: Record<string, string> = {
  status: "查看状态",
  "qzone.queue": "空间队列",
  "qzone.draft": "空间草稿",
  "qzone.publish": "空间发布",
  "qzone.schedule": "空间排期",
  "qzone.cancel": "取消空间",
  "qzone.revoke": "撤回空间",
  "qzone.pause": "暂停空间",
  "qzone.resume": "恢复空间",
  "approval.approve": "确认审批",
  "approval.reject": "拒绝审批",
  "report.ack": "汇报已读",
  "report.queue": "汇报队列",
  "report.status": "汇报状态",
  "report.worker_status": "汇报投递状态",
  "report.worker_pause": "暂停汇报投递",
  "report.worker_resume": "恢复汇报投递",
  "user.status": "用户状态",
  "user.mark_existing_friend": "标为已有好友",
  "user.mark_new_friend": "标为新友",
  "user.mark_not_friend": "标为非好友",
  "user.history_deny": "禁止历史",
  "user.history_file_only": "仅档案历史",
  "user.history_selected_range": "选定历史范围",
  "user.history_one_time": "一次性历史",
  "user.qzone_profile_status": "空间资料状态",
  "user.qzone_profile_deny": "禁止空间资料",
  "user.qzone_profile_one_time": "一次性空间资料",
  "user.qzone_profile_ttl": "空间资料时效",
  "user.qzone_profile_preview": "空间资料预览",
  "privacy.status": "隐私状态",
  "privacy.freeze": "冻结用户",
  "privacy.unfreeze": "解冻用户",
  "privacy.export": "导出资料",
  "privacy.delete": "删除资料",
  "friend_baseline.status": "好友基线状态",
  "friend_baseline.preview": "好友基线预览",
  "image.create": "创建图片任务",
  "image.generate": "生成图片",
  "image.status": "图片状态",
  "image.renew": "续期图片",
  "image.orphan_scan": "扫描孤立图片",
  "image.orphan_worker_status": "孤立图片扫描状态",
  "image.orphan_worker_pause": "暂停孤立图片扫描",
  "image.orphan_worker_resume": "恢复孤立图片扫描",
  "shadow.status": "影子状态",
  "shadow.recent": "最近影子",
  "shadow.detail": "影子详情",
  "shadow.replay": "重跑影子",
  "model.status": "模型资格状态",
  "knowledge.process": "炼化资料",
  "knowledge.status": "资料状态",
  "knowledge.worker_status": "炼化状态",
  "knowledge.worker_pause": "暂停炼化",
  "knowledge.worker_resume": "恢复炼化",
  "proactive.create": "创建主动任务",
  "proactive.status": "主动任务状态",
  "proactive.cancel": "取消主动任务",
  "proactive.user_enable": "开启用户主动",
  "proactive.user_disable": "关闭用户主动",
  "proactive.user_status": "查看用户主动策略",
  "proactive.auto_content_enable": "开启自动话题",
  "proactive.auto_content_disable": "关闭自动话题",
  "proactive.diary_enable": "开启日记发送",
  "proactive.diary_disable": "关闭日记发送",
  "proactive.worker_status": "调度状态",
  "proactive.worker_pause": "暂停调度",
  "proactive.worker_resume": "恢复调度",
  "outbound.status": "外发状态",
  "outbound.pause": "暂停外发",
  "outbound.resume": "恢复外发",
  "quota.status": "额度状态",
  "quota.today_bonus": "今日追加额度",
};

export const GENERIC_STATUS_LABELS: Record<string, string> = {
  pending: "待处理",
  running: "进行中",
  completed: "已完成",
  failed: "失败",
  duplicate: "重复",
  pending_approval: "待审批",
  export: "导出",
  delete: "删除",
};

export const READINESS_PROFILE_LABELS: Record<string, string> = {
  local_start: "本机启动",
  offline_shadow: "离线影子运行",
  controlled_real_effect: "受控真实效果",
};

export const CAPABILITY_SCOPE_LABELS: Record<string, string> = {
  local_runtime: "本机运行环境", offline_inference: "离线推理", chat_model: "对话模型",
  vision_model: "视觉模型", image_model: "图片模型", stats_model: "统计模型",
  qq_reply: "QQ 回复", qq_proactive: "主动消息", owner_report: "主号汇报",
  qzone_publish: "空间发布", qzone_profile: "空间资料", live_history: "历史读取",
};

export const READINESS_STATUS_LABELS: Record<string, string> = {
  passed: "证据有效，可执行", blocked: "已阻止", not_evaluated: "尚未评估",
  pass: "通过", warning: "警告", unknown: "未知", stale: "证据过期",
};

export const READINESS_REASON_LABELS: Record<string, string> = {
  required_probe_blocked: "必需检查未通过", required_probe_unknown: "必需检查状态未知",
  evidence_stale: "证据已过期", process_instance_mismatch: "不是当前进程的证据",
  bot_identity_mismatch: "机器人身份不匹配", configuration_invalid: "配置无效",
  database_unavailable: "数据库不可用", backup_unverified: "备份未验证",
  path_not_confined: "文件位置越界", port_unavailable: "端口不可用",
  admin_auth_invalid: "管理认证无效", worker_ceiling_blocked: "工作进程上限阻止",
  durable_pause_active: "持久暂停生效中", network_gate_closed: "网络总闸关闭",
  activation_scope_blocked: "该能力未获准启用", owner_authorization_required: "需要主号授权",
};

export const PROBE_LABELS: Record<string, string> = {
  "launcher.admin_auth": "后台启动认证", "launcher.configuration": "本机配置",
  "launcher.database": "启动前数据库", "launcher.managed_paths": "受管目录边界",
  "launcher.port": "后台监听端口", "runtime.database": "运行数据库",
  "runtime.worker_ceilings": "工作进程上限", "offline.isolation": "离线隔离",
  "capability.gate": "能力配置与活动闸门", "runtime.worker_lifecycle": "工作进程生命周期",
  "runtime.activation_scope": "能力启用范围", "runtime.owner_authorization": "主号授权",
  "process.instance": "当前进程身份", "configuration.valid": "配置完整性",
  "database.available": "数据库可用性", "backup.verified": "备份验证",
  "path.confinement": "文件边界", "port.available": "本机端口",
  "admin.auth": "后台认证", "worker.ceiling": "工作进程上限",
  "runtime.durable_pause": "持久暂停", "network.gate": "网络总闸",
  "activation.scope": "能力启用范围", "owner.authorization": "主号授权",
  "onebot.identity": "OneBot 机器人身份",
};

export const REMEDIATION_LABELS: Record<string, string> = {
  refresh_evidence: "重新检查证据", restart_current_process: "重启当前进程",
  repair_configuration: "修正本机配置", verify_backup: "重新验证备份",
  resume_worker: "在确认后恢复工作进程", enable_network_gate: "确认后开启网络总闸",
  authorize_scope: "由主号授权此能力",
  configure_worker_ceilings: "为每个工作进程明确配置上限",
  disable_real_adapters_for_shadow: "关闭影子运行中的真实网络适配器",
  review_and_resume_worker: "复核原因后恢复工作进程", connect_expected_bot: "连接并认证预期机器人账号",
  open_required_capability_gate: "复核后开启所需能力闸门", start_required_worker: "启动对应工作进程",
  review_activation_scope: "复核紧急暂停与启用范围", restore_owner_authorization: "由主号重新授权",
  use_loopback_admin_host: "将后台监听地址限制为本机回环", repair_database_preflight: "修复数据库预检",
  repair_managed_roots: "修复受管目录边界", configure_admin_access_token: "配置独立后台管理令牌",
  free_admin_port: "释放或更换后台监听端口",
};

export const EVIDENCE_SOURCE_LABELS: Record<string, string> = {
  "launcher-preflight": "启动预检", configuration: "本机配置", database: "数据库检查",
  "managed-paths": "受管目录检查", "admin-auth": "后台认证", port: "端口检查",
  "runtime-database": "运行数据库", "worker-ceilings": "工作进程上限",
  "offline-runtime": "离线运行环境", "durable-worker-state": "持久工作进程状态",
  "onebot-connection": "OneBot 连接", "capability-gates": "能力闸门",
  "worker-lifecycle": "工作进程生命周期", "activation-policy": "启用策略", "owner-policy": "主号策略",
};

export const ARTIFACT_TYPE_LABELS: Record<string, string> = {
  migration_backup: "数据库迁移备份", privacy_export: "隐私导出",
  privacy_deletion_backup: "删除前备份", imported_source: "导入资料原件",
  quarantine_evidence: "隔离证据",
};
export const ARTIFACT_STATE_LABELS: Record<string, string> = {
  pending: "待验证", verified: "已验证", invalid: "无效", missing: "文件缺失", anomalous: "异常",
  unknown: "引用未知", unreferenced: "未被引用", protected: "受引用保护",
  retain: "保留", candidate: "隔离候选", quarantined: "已隔离", blocked: "已阻止",
  prepared: "已准备", moving: "正在隔离", rollback_required: "需要回滚", rolled_back: "已回滚",
};
export const RETENTION_TARGET_LABELS: Record<string, string> = {
  migration_backups: "数据库迁移备份", privacy_exports: "隐私导出",
  privacy_deletion_backups: "删除前备份", imported_sources: "导入资料原件",
};

export const ACTIVITY_ACTION_LABELS: Record<string, string> = {
  ...COMMAND_ACTION_LABELS,
  "account.bot_updated": "机器人账号已更新",
  "account.bot_created": "机器人账号已添加",
  "account.bot_deleted": "机器人账号已移除",
  "migration_backups.quarantined": "迁移备份已隔离",
  "approval.approve": "审批已确认",
  "approval.reject": "审批已拒绝",
};

const ACTIVITY_AREA_LABELS: Record<string, string> = {
  account: "账号",
  approval: "审批",
  privacy: "隐私",
  qzone: "QQ 空间",
  proactive: "主动任务",
  knowledge: "资料",
  user: "用户",
  report: "汇报",
  quota: "配额",
  outbound: "外发",
  image: "图片",
  shadow: "影子运行",
  friend_baseline: "好友基线",
};

const ACTIVITY_VERB_LABELS: Record<string, string> = {
  created: "已创建", updated: "已更新", deleted: "已删除", approved: "已批准",
  rejected: "已拒绝", paused: "已暂停", resumed: "已恢复", queued: "已入队",
  published: "已发布", cancelled: "已取消", revoked: "已撤回", completed: "已完成",
  failed: "失败", status: "状态已查询", preview: "已预览", generate: "已生成",
};

export function activityActionLabel(value: string) {
  if (ACTIVITY_ACTION_LABELS[value]) return ACTIVITY_ACTION_LABELS[value];
  const parts = value.split(".");
  const area = ACTIVITY_AREA_LABELS[parts[0]] ?? "系统";
  const verb = ACTIVITY_VERB_LABELS[parts.at(-1) ?? ""] ?? "发生操作";
  return `${area}${verb}`;
}

export function activitySourceLabel(value: string) {
  return { audit: "审计记录", control_command: "主号命令", owner_report: "主号汇报" }[value] ?? "系统事件";
}

export function activityDetailLabel(value: string) {
  const match = value.match(/^([^:]+):(.*)$/);
  if (!match) {
    return value.replace(/\bpending\b/g, "待处理").replace(/\bcompleted\b/g, "已完成").replace(/\bfailed\b/g, "失败");
  }
  const subject = { bot: "机器人", user: "用户", worker: "工作进程", control: "控制命令", approval: "审批", owner_report: "主号汇报" }[match[1]] ?? "对象";
  return `${subject} ${match[2]}`;
}

export const SCHEDULER_STATUS_LABELS: Record<string, string> = {
  disabled: "已关闭",
  idle: "没有到期任务",
  busy: "正在处理",
  composed: "已生成日记或材料",
  failed: "失败",
  enqueued: "已入队",
};

export const SCHEDULER_REASON_LABELS: Record<string, string> = {
  worker_not_active: "调度未开启",
  worker_already_running: "上一轮还在跑",
  no_due_task: "没有到期任务",
  user_policy_disabled: "用户未允许主动",
  quiet_hours: "安静时段",
  daily_limit: "已达日限额",
  minimum_interval: "未到最短间隔",
};

export const QUALIFICATION_CAPABILITY_LABELS: Record<string, string> = {
  chat: "对话",
  vision: "识图",
  image: "文生图",
  stats: "统计",
};

export const QUALIFICATION_DECISION_LABELS: Record<string, string> = {
  unqualified: "未鉴定",
  passed: "已通过鉴定",
  failed: "鉴定未通过",
  stale: "鉴定过期",
  blocked: "鉴定被阻止",
};

export const QUALIFICATION_RUN_STATE_LABELS: Record<string, string> = {
  prepared: "已准备",
  running: "执行中",
  passed: "已通过",
  failed: "失败",
  inconclusive: "结论不明",
  blocked: "已阻止",
  cancelled: "已取消",
};

export const QUALIFICATION_REASON_LABELS: Record<string, string> = {
  identity_violation: "身份违规",
  isolation_violation: "隔离违规",
  schema_invalid: "结构无效",
  forbidden_field: "出现禁止字段",
  artifact_unsafe: "制品不安全",
  side_effect_isolation: "副作用隔离失败",
  evidence_incomplete: "证据不完整",
  advisory_threshold_unmet: "建议分未达标",
  cost_unbounded: "费用上界无法确定",
  confirmation_reused: "确认码已使用",
  confirmation_actor_mismatch: "确认操作者不一致",
  confirmation_process_mismatch: "确认进程不一致",
  confirmation_scope_mismatch: "确认范围不一致",
  preview_expired: "预览已过期",
  lease_expired: "租约已过期",
  stale_route_revision: "路由版本已过期",
  stale_suite_version: "套件版本已过期",
  stale_policy_revision: "保护策略已过期",
  stale_price_catalog: "价格目录已过期",
  emergency_pause: "紧急暂停",
  quota_exhausted: "配额已耗尽",
  circuit_open: "熔断已打开",
  readiness_stale: "就绪状态过期",
  ceiling_exhausted: "鉴定上限已用尽",
  compatibility_unsupported: "协议不受支持",
  timeout: "超时",
  retryable_provider_error: "可重试的供应方错误",
  invalid_response: "响应无效",
  ambiguous_interruption: "中断后计费不明",
  production_input_rejected: "拒绝非合成输入",
  default_denied: "默认拒绝",
  cancelled: "已取消",
};

export function statusLabel(value: string | undefined, table: Record<string, string>, fallback = "—") {
  if (!value) return fallback;
  return table[value] ?? value;
}

export function schedulerNotice(status: string, reason?: string | null) {
  const statusText = SCHEDULER_STATUS_LABELS[status] ?? status;
  if (!reason) return `调度一次：${statusText}`;
  if (reason.startsWith("auto:")) {
    return `调度一次：${statusText}`;
  }
  const reasonText = SCHEDULER_REASON_LABELS[reason] ?? reason;
  return `调度一次：${statusText} · ${reasonText}`;
}
