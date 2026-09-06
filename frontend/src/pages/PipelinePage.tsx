import {
  AlertTriangle,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  Database,
  RefreshCw,
  Route,
  Settings2,
  ShieldCheck,
  Siren,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { PageFrame } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { Select } from "../components/Select";
import { SurfaceCard } from "../components/SurfaceCard";
import {
  api,
  type ReplyPipelineReadiness,
  type ReplyRuntimeApproval,
  type ReplyRuntimePreview,
  type ReplyRuntimeStatus,
  type ReplyRunDetail,
  type ReplyRunSummary,
} from "../lib/api";
import { peerTitle } from "../lib/peerLabels";
import { useOperatorLabels } from "../lib/useOperatorLabels";

const STAGES = [
  ["", "全部"],
  ["settling", "等待合并"],
  ["assembling_context", "组装上下文"],
  ["calling_model", "调用模型"],
  ["planning_reply", "规划回复"],
  ["creating_outbox", "创建队列"],
  ["awaiting_delivery", "等待送达"],
  ["awaiting_approval", "等待主人审批"],
  ["shadow_completed", "影子完成"],
  ["completed", "已完成"],
  ["suppressed", "已抑制"],
  ["failed", "失败"],
] as const;

const STAGE_LABELS = Object.fromEntries(STAGES.filter(([key]) => key)) as Record<
  string,
  string
>;

const SOURCE_LABELS: Record<string, string> = {
  core_identity: "核心身份",
  authorization: "授权",
  conversation: "当前会话",
  persona: "人格",
  user_understanding: "用户理解",
  knowledge: "资料",
  memory: "记忆",
  history: "历史",
};

const BLOCKER_LABELS: Record<string, string> = {
  database: "数据库不可用",
  ingestion: "入站关闭",
  reply_worker_wired: "回复 Worker 尚未接入",
  reply_worker_enabled: "回复 Worker 未启用",
  chat_model_configured: "对话模型未配置",
  model_network: "模型网络关闭",
  chat_route: "对话路由关闭",
  outbound_gate: "真实外发关闭",
  outbound_worker_active: "外发 Worker 未运行",
  onebot_token: "OneBot Token 未配置",
  onebot_connected: "OneBot 未连接",
  chat_circuit_available: "对话模型熔断中",
  reply_worker_disabled: "回复 Worker 未启用",
  emergency_paused: "紧急暂停中",
  configuration_mode_ceiling: "配置上限已降级",
  chat_model_unconfigured: "对话模型未配置",
  chat_route_disabled: "对话路由关闭",
  model_network_disabled: "模型网络关闭",
  chat_circuit_open: "对话模型熔断中",
  outbound_disabled: "真实外发关闭",
  outbound_worker_inactive: "外发 Worker 未运行",
  onebot_token_missing: "OneBot Token 未配置",
  onebot_disconnected: "OneBot 未连接",
};

const MODE_LABELS: Record<string, string> = {
  observe_only: "仅观察",
  shadow: "影子运行",
  owner_approved: "主人审批",
  limited_auto: "小范围自动",
  auto: "全自动",
};

function stageLabel(stage: string) {
  return STAGE_LABELS[stage] ?? stage;
}

function tone(stage: string) {
  if (stage === "completed" || stage === "shadow_completed") {
    return "bg-emerald-50 text-emerald-800";
  }
  if (stage === "failed") return "bg-red-50 text-red-700";
  if (stage === "suppressed" || stage === "cancelled") {
    return "bg-black/5 text-muted-foreground";
  }
  return "bg-amber-50 text-amber-800";
}

function RuntimeControlPanel({
  runtime,
  approvals,
  preview,
  busy,
  onPreview,
  onConfirm,
}: {
  runtime: ReplyRuntimeStatus | null;
  approvals: ReplyRuntimeApproval[];
  preview: ReplyRuntimePreview | null;
  busy: boolean;
  onPreview: (action: string, payload?: Record<string, unknown>) => void;
  onConfirm: () => void;
}) {
  const [mode, setMode] = useState("observe_only");
  const [conversationKey, setConversationKey] = useState("");
  useEffect(() => {
    if (runtime) setMode(runtime.requested_mode);
  }, [runtime]);
  return (
    <SurfaceCard className="mt-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Settings2 size={17} className="text-primary" />
            <h3 className="text-base font-semibold text-foreground">生产回复运行时</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">
            所有会产生影响的操作都先生成一次性预览，再由当前管理员明确确认。
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="rounded-xl bg-muted px-3 py-2 text-muted-foreground">
            请求：{MODE_LABELS[runtime?.requested_mode ?? ""] ?? "载入中"}
          </span>
          <span className="rounded-xl border bg-card px-3 py-2 text-foreground">
            生效：{MODE_LABELS[runtime?.effective_mode ?? ""] ?? "载入中"}
          </span>
          <span className="rounded-xl bg-muted px-3 py-2 text-primary-deep">
            上限：{MODE_LABELS[runtime?.configuration_ceiling ?? ""] ?? "—"}
          </span>
        </div>
      </div>

      <div className="mt-5 grid gap-4 xl:grid-cols-[1fr_1fr_1.15fr]">
        <div className="rounded-2xl bg-muted/45 p-4">
          <p className="text-xs font-semibold text-foreground">模式与急停</p>
          <Select className="mt-3" value={mode} onChange={setMode} options={Object.entries(MODE_LABELS).map(([value, label]) => ({ value, label }))} aria-label="回复运行模式" />
          <button
            type="button"
            className="ych-pill mt-3 w-full justify-center"
            disabled={busy || mode === runtime?.requested_mode}
            onClick={() => onPreview("set_mode", { mode })}
          >
            预览模式变更
          </button>
          <button
            type="button"
            className="mt-2 flex w-full items-center justify-center gap-2 rounded-xl bg-red-50 px-3 py-2 text-xs font-semibold text-red-700 disabled:opacity-50"
            disabled={busy}
            onClick={() => onPreview(runtime?.state.emergency_paused ? "resume" : "emergency_stop")}
          >
            <Siren size={14} /> {runtime?.state.emergency_paused ? "预览安全恢复" : "预览紧急停止"}
          </button>
        </div>

        <div className="rounded-2xl bg-muted/45 p-4">
          <p className="text-xs font-semibold text-foreground">Limited-auto 精确范围</p>
          <input
            value={conversationKey}
            onChange={(event) => setConversationKey(event.target.value)}
            placeholder={`${runtime?.state.bot_qq ?? "机器人QQ"}:private:用户QQ`}
            className="mt-3 w-full rounded-xl border border-border/10 bg-card px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground/55 focus:border-primary"
            aria-label="会话范围键"
          />
          <div className="mt-3 grid grid-cols-2 gap-2">
            {[true, false].map((enabled) => (
              <button
                key={String(enabled)}
                type="button"
                className="ych-pill justify-center"
                disabled={busy || !conversationKey.trim()}
                onClick={() => onPreview("set_eligibility", {
                  conversation_key: conversationKey.trim(), enabled,
                })}
              >
                {enabled ? "预览允许" : "预览移除"}
              </button>
            ))}
          </div>
          <p className="mt-3 text-[11px] text-muted-foreground">
            当前 {runtime?.eligibility.filter((item) => item.enabled).length ?? 0} 个精确会话；默认全部拒绝。
          </p>
        </div>

        <div className="rounded-2xl border bg-card p-4 text-foreground">
          <p className="text-xs font-semibold">Worker 与审批</p>
          <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
            <span className="rounded-xl bg-secondary p-2">状态 {runtime?.worker.lifecycle ?? "—"}</span>
            <span className="rounded-xl bg-secondary p-2">恢复 {runtime?.worker.recovered_count ?? 0}</span>
            <span className="rounded-xl bg-secondary p-2">最近进度 {time(runtime?.worker.last_progress_at)}</span>
            <span className="rounded-xl bg-secondary p-2">待审批 {approvals.length}</span>
          </div>
          <div className="mt-3 max-h-32 space-y-2 overflow-y-auto">
            {approvals.length === 0 ? (
              <EmptyState tone="inline" className="py-2 text-xs" title="暂无待审批回复" />
            ) : approvals.map((item) => (
              <div key={item.id} className="flex items-center justify-between gap-2 rounded-xl bg-secondary p-2">
                <div className="min-w-0 text-[10px]">
                  <p className="truncate">{shortId(item.run_id)}</p>
                  <p className="text-foreground/45">到期 {time(item.expires_at)}</p>
                </div>
                <div className="flex gap-1">
                  <button type="button" disabled={busy} className="rounded-lg bg-emerald-300/15 px-2 py-1 text-emerald-100" onClick={() => onPreview("decide_approval", { approval_id: item.id, approve: true })}>批准</button>
                  <button type="button" disabled={busy} className="rounded-lg bg-red-300/15 px-2 py-1 text-red-100" onClick={() => onPreview("decide_approval", { approval_id: item.id, approve: false })}>拒绝</button>
                  <button type="button" disabled={busy} className="rounded-lg bg-secondary px-2 py-1 text-foreground/70" onClick={() => onPreview("cancel_approval", { approval_id: item.id })}>取消</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {preview ? (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-primary/25 bg-muted/55 p-4">
          <div className="min-w-0 text-xs text-muted-foreground">
            <p className="font-semibold text-foreground">等待明确确认：{preview.action}</p>
            <p className="mt-1 truncate">修订 {preview.runtime_revision} · 证据 {shortId(preview.readiness_hash)} · {time(preview.expires_at)} 失效</p>
          </div>
          <button type="button" disabled={busy} className="rounded-xl bg-primary px-4 py-2 text-xs font-bold text-primary-foreground disabled:opacity-50" onClick={onConfirm}>
            确认执行
          </button>
        </div>
      ) : null}
    </SurfaceCard>
  );
}

function shortId(value: string) {
  return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

function time(value?: string | null) {
  return value ? value.slice(0, 16).replace("T", " ") : "—";
}

function ReadinessHero({ readiness }: { readiness: ReplyPipelineReadiness | null }) {
  const blockers = readiness?.blockers ?? [];
  const ready = readiness?.production_activation_ready ?? false;
  return (
    <section className="relative overflow-hidden rounded-lg border bg-card px-7 py-7 text-foreground">
      <div className="absolute -right-16 -top-20 h-52 w-52 rounded-full border border-white/10" />
      <div className="absolute -bottom-28 right-20 h-56 w-56 rounded-full border border-white/10" />
      <div className="relative grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(320px,.8fr)]">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-foreground/55">
            Reply pipeline / readiness
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <h2 className="text-3xl font-semibold tracking-tight">
              {ready ? "正式启用条件已满足" : "安全观察中"}
            </h2>
            <span className="rounded-xl bg-secondary px-3 py-1 text-xs font-semibold">
              {readiness?.mode ?? "载入中"}
            </span>
          </div>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-foreground/65">
            这张状态卡只读。阻塞项清零前不会把回复链路标记为可正式启用，送达不明也不会自动重发。
          </p>
          <div className="mt-6 flex flex-wrap gap-2">
            {blockers.length === 0 ? (
              <span className="rounded-xl bg-emerald-300/15 px-3 py-2 text-xs text-emerald-100">
                <Check className="mr-1 inline" size={14} /> 无阻塞项
              </span>
            ) : (
              blockers.slice(0, 6).map((item) => (
                <span
                  key={item.code}
                  className="rounded-xl bg-secondary px-3 py-2 text-xs text-foreground/80"
                >
                  {BLOCKER_LABELS[item.code] ?? item.code}
                </span>
              ))
            )}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 self-end">
          {[
            ["任务总数", readiness?.summary.total_runs ?? 0],
            ["进行中", readiness?.summary.active_runs ?? 0],
            ["状态不明", readiness?.summary.delivery_outcomes.delivery_unknown ?? 0],
            ["过期租约", readiness?.summary.leases.expired ?? 0],
          ].map(([label, value]) => (
            <div key={label} className="rounded-2xl bg-secondary p-4">
              <p className="text-[11px] text-foreground/50">{label}</p>
              <p className="mt-2 text-2xl font-semibold">{value}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function RunList({
  runs,
  selectedId,
  onSelect,
}: {
  runs: ReplyRunSummary[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const labels = useOperatorLabels();
  return (
    <div className="grid gap-2 xl:grid-cols-2 2xl:grid-cols-3">
      {runs.length === 0 ? (
        <EmptyState
          tone="panel"
          className="xl:col-span-2 2xl:col-span-3"
          title="当前筛选下没有回复任务"
        />
      ) : (
        runs.map((run) => (
          <button
            key={run.id}
            type="button"
            onClick={() => onSelect(run.id)}
            className={`w-full rounded-2xl px-4 py-3 text-left transition ${
              selectedId === run.id ? "border bg-card text-foreground" : "bg-muted/55 hover:bg-muted"
            }`}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">
                  {run.conversation_kind === "private" ? "私聊" : "群聊"} ·{" "}
                  {peerTitle(run.peer_id, peerLabel(labels, run.conversation_kind, run.peer_id))}
                </p>
                <p
                  className={`mt-1 text-[11px] ${
                    selectedId === run.id ? "text-foreground/55" : "text-muted-foreground"
                  }`}
                >
                  {shortId(run.id)} · {time(run.updated_at)}
                </p>
              </div>
              <ChevronRight size={16} className="mt-1 shrink-0 opacity-55" />
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span
                className={`rounded-lg px-2 py-1 text-[10px] font-bold ${
                  selectedId === run.id ? "bg-secondary" : tone(run.stage)
                }`}
              >
                {stageLabel(run.stage)}
              </span>
              <span className="text-[10px] opacity-60">
                {run.trigger_count} 条触发 · 尝试 {run.attempt_count}
              </span>
            </div>
          </button>
        ))
      )}
    </div>
  );
}

function peerLabel(
  labels: { user: (id: string) => string; group: (id: string) => string },
  conversationKind: string,
  peerId: string,
) {
  return conversationKind === "group" ? labels.group(peerId) : labels.user(peerId);
}

function DetailPanel({ detail }: { detail: ReplyRunDetail | null }) {
  const labels = useOperatorLabels();
  const manifest = detail?.context_manifests[0];
  if (!detail) {
    return (
      <EmptyState
        tone="select"
        className="min-h-[520px] rounded-lg bg-card"
        title="选择一条任务查看脱敏证据"
      />
    );
  }
  return (
    <div className="space-y-4">
      <SurfaceCard>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-muted-foreground">
              {shortId(detail.id)}
            </p>
            <h3 className="mt-2 text-xl font-semibold text-foreground">
              {detail.conversation_kind === "private" ? "私聊" : "群聊"}{" "}
              {peerTitle(
                detail.peer_id,
                peerLabel(labels, detail.conversation_kind, detail.peer_id),
              )}
            </h3>
          </div>
          <span className={`rounded-xl px-3 py-2 text-xs font-bold ${tone(detail.stage)}`}>
            {stageLabel(detail.stage)}
          </span>
        </div>
        {detail.failure ? (
          <div className="mt-5 rounded-2xl bg-red-50 p-4 text-sm text-red-800">
            <p className="font-semibold">{detail.failure.category}</p>
            <p className="mt-1 text-xs leading-5 text-red-700/80">{detail.failure.summary}</p>
          </div>
        ) : null}
        <div className="mt-5 grid grid-cols-3 gap-3 max-sm:grid-cols-1">
          {[
            ["模型尝试", detail.attempt_count],
            ["上下文版本", detail.context_manifests.length],
            ["回复气泡", detail.reply_plan?.bubble_count ?? 0],
          ].map(([label, value]) => (
            <div key={label} className="rounded-2xl bg-muted/55 p-3">
              <p className="text-[10px] text-muted-foreground">{label}</p>
              <p className="mt-1 text-lg font-semibold text-foreground">{value}</p>
            </div>
          ))}
        </div>
      </SurfaceCard>

      <SurfaceCard>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Route size={16} className="text-primary" />
            <p className="text-sm font-semibold text-foreground">上下文来源</p>
          </div>
          <span className="text-[11px] text-muted-foreground">
            {manifest ? `${manifest.used_chars ?? 0}/${manifest.budget_chars ?? 0} 字符` : "未组装"}
          </span>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2 max-sm:grid-cols-1">
          {(manifest?.sections ?? []).map((section) => (
            <div key={section.section_id} className="rounded-2xl bg-muted/45 p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-semibold text-foreground">
                  {SOURCE_LABELS[section.source_class ?? ""] ?? section.source_class}
                </p>
                <span
                  className={`text-[10px] font-bold ${
                    section.policy_decision === "allowed" ? "text-emerald-700" : "text-red-600"
                  }`}
                >
                  {section.policy_decision === "allowed" ? "允许" : "拒绝"}
                </span>
              </div>
              <p className="mt-1 text-[10px] text-muted-foreground">
                {section.scope} · 证据 {section.record_ids.length}
                {section.truncated ? ` · 省略 ${section.omitted_chars}` : ""}
              </p>
            </div>
          ))}
          {!manifest ? <p className="text-sm text-muted-foreground">尚无上下文清单</p> : null}
        </div>
      </SurfaceCard>

      <div className="grid grid-cols-2 gap-4 max-lg:grid-cols-1">
        <SurfaceCard>
          <div className="flex items-center gap-2">
            <Database size={16} className="text-primary" />
            <p className="text-sm font-semibold text-foreground">触发与模型</p>
          </div>
          <div className="mt-4 space-y-3">
            {detail.triggers.map((trigger) => (
              <div key={trigger.message_id} className="rounded-2xl bg-muted/45 p-3">
                <p className="line-clamp-3 text-sm leading-5 text-foreground">
                  {trigger.text_preview || `[${trigger.segment_types.join(", ")}]`}
                </p>
                <p className="mt-1 text-[10px] text-muted-foreground">{time(trigger.occurred_at)}</p>
              </div>
            ))}
            <div className="border-t border-border/8 pt-3 text-xs text-muted-foreground">
              <p>模型：{detail.model?.model_route ?? "尚未调用"}</p>
              <p className="mt-1">
                token：{detail.model?.input_tokens ?? 0} 入 / {detail.model?.output_tokens ?? 0} 出
              </p>
            </div>
          </div>
        </SurfaceCard>
        <SurfaceCard>
          <div className="flex items-center gap-2">
            <Clock3 size={16} className="text-primary" />
            <p className="text-sm font-semibold text-foreground">送达轨迹</p>
          </div>
          <div className="mt-4 space-y-3">
            {detail.delivery.length === 0 ? (
              <p className="text-sm text-muted-foreground">尚未进入送达阶段</p>
            ) : (
              detail.delivery.map((item, index) => (
                <div key={`${item.outbox_id}-${item.attempt}-${item.outcome}`} className="flex gap-3">
                  <div className="flex flex-col items-center">
                    <CircleDot size={15} className="mt-0.5 text-primary" />
                    {index < detail.delivery.length - 1 ? (
                      <span className="mt-1 h-full w-px border bg-card/10" />
                    ) : null}
                  </div>
                  <div className="pb-3">
                    <p className="text-xs font-semibold text-foreground">
                      气泡 {item.bubble_sequence} · {item.outcome}
                    </p>
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      第 {item.attempt} 次 · {time(item.observed_at)}
                    </p>
                  </div>
                </div>
              ))
            )}
          </div>
        </SurfaceCard>
      </div>
    </div>
  );
}

export function PipelinePage() {
  const [readiness, setReadiness] = useState<ReplyPipelineReadiness | null>(null);
  const [runtime, setRuntime] = useState<ReplyRuntimeStatus | null>(null);
  const [runtimeApprovals, setRuntimeApprovals] = useState<ReplyRuntimeApproval[]>([]);
  const [runtimePreview, setRuntimePreview] = useState<ReplyRuntimePreview | null>(null);
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const [runs, setRuns] = useState<ReplyRunSummary[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const selectedIdRef = useRef("");
  const [detail, setDetail] = useState<ReplyRunDetail | null>(null);
  const [stage, setStage] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [readinessBody, runtimeBody, approvalsBody, runsBody] = await Promise.all([
        api.replyPipelineReadiness(),
        api.replyRuntime(),
        api.replyRuntimeApprovals(),
        api.replyRuns(stage),
      ]);
      setReadiness(readinessBody);
      setRuntime(runtimeBody);
      setRuntimeApprovals(approvalsBody.items);
      setRuns(runsBody.items);
      const currentSelectedId = selectedIdRef.current;
      const nextId = runsBody.items.some((item) => item.id === currentSelectedId)
        ? currentSelectedId
        : (runsBody.items[0]?.id ?? "");
      selectedIdRef.current = nextId;
      setSelectedId(nextId);
      setDetail(nextId ? await api.replyRunDetail(nextId) : null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "消息链路读取失败");
    } finally {
      setLoading(false);
    }
  }, [stage]);

  useEffect(() => {
    void load();
  }, [load]);

  const selectRun = useCallback(async (id: string) => {
    selectedIdRef.current = id;
    setSelectedId(id);
    setError("");
    try {
      setDetail(await api.replyRunDetail(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "任务详情读取失败");
    }
  }, []);

  const failedCount = useMemo(
    () => (readiness?.summary.stages.failed ?? 0) + (readiness?.summary.stages.suppressed ?? 0),
    [readiness],
  );

  const previewRuntimeAction = useCallback(
    async (action: string, payload: Record<string, unknown> = {}) => {
      setRuntimeBusy(true);
      setError("");
      try {
        setRuntimePreview(await api.previewReplyRuntime(action, payload));
      } catch (err) {
        setRuntimePreview(null);
        setError(err instanceof Error ? err.message : "运行时操作预览失败");
      } finally {
        setRuntimeBusy(false);
      }
    },
    [],
  );

  const confirmRuntimeAction = useCallback(async () => {
    if (!runtimePreview) return;
    setRuntimeBusy(true);
    setError("");
    try {
      await api.confirmReplyRuntime(runtimePreview.confirmation_token);
      setRuntimePreview(null);
      await load();
    } catch (err) {
      setRuntimePreview(null);
      setError(
        `${err instanceof Error ? err.message : "运行时操作失败"}；预览可能已过期，请刷新后重试。`,
      );
    } finally {
      setRuntimeBusy(false);
    }
  }, [load, runtimePreview]);

  return (
    <PageFrame>
      <PageTitle
        title="消息链路"
        description="从入站、上下文、模型到送达的只读证据。这里看清问题，不在这里偷偷打开能力。"
        badge={
          <button type="button" className="ych-pill" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
            刷新
          </button>
        }
      />
      {error ? (
        <div className="mb-4 flex items-center gap-2 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle size={16} /> {error}
        </div>
      ) : null}
      <ReadinessHero readiness={readiness} />
      <RuntimeControlPanel
        runtime={runtime}
        approvals={runtimeApprovals}
        preview={runtimePreview}
        busy={runtimeBusy}
        onPreview={(action, payload) => void previewRuntimeAction(action, payload)}
        onConfirm={() => void confirmRuntimeAction()}
      />

      <div className="mt-4 grid grid-cols-3 gap-4 max-md:grid-cols-1">
        {[
          [ShieldCheck, "隔离保护", "跨用户与群聊私人层拒绝"],
          [Clock3, "送达不明", `${readiness?.summary.delivery_outcomes.delivery_unknown ?? 0} 条，禁止盲重发`],
          [AlertTriangle, "需关注", `${failedCount} 条失败或抑制`],
        ].map(([Icon, title, text]) => {
          const CardIcon = Icon as typeof ShieldCheck;
          return (
            <SurfaceCard key={String(title)} emphasis="compact">
              <CardIcon size={17} className="text-primary" />
              <p className="mt-3 text-sm font-semibold text-foreground">{String(title)}</p>
              <p className="mt-1 text-xs text-muted-foreground">{String(text)}</p>
            </SurfaceCard>
          );
        })}
      </div>

      <div className="mt-4 space-y-4">
        <SurfaceCard>
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-foreground">任务队列</p>
              <p className="mt-1 text-[11px] text-muted-foreground">最多显示最近 50 条</p>
            </div>
            <span className="rounded-lg bg-muted px-2 py-1 text-[10px] font-bold text-muted-foreground">
              {runs.length}
            </span>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {STAGES.map(([key, label]) => (
              <button
                key={key || "all"}
                type="button"
                onClick={() => setStage(key)}
                className={`shrink-0 rounded-xl px-3 py-2 text-[11px] font-semibold ${
                  stage === key ? "bg-primary text-primary-foreground" : "bg-muted/60 text-muted-foreground"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="mt-4">
            <RunList runs={runs} selectedId={selectedId} onSelect={(id) => void selectRun(id)} />
          </div>
        </SurfaceCard>
        {runs.length > 0 ? <DetailPanel detail={detail} /> : null}
      </div>
    </PageFrame>
  );
}
