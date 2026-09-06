import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { DateTimePicker } from "../components/DateTimePicker";
import { EmptyState } from "../components/EmptyState";
import { PageColumn, PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { Select } from "../components/Select";
import { SurfaceCard } from "../components/SurfaceCard";
import {
  api,
  type OutboxWorkerSnapshot,
  type ProactiveSchedulerSnapshot,
  type ProactiveSummary,
  type ProactiveTaskItem,
  type ProactiveUserPolicy,
} from "../lib/api";
import { shanghaiLocalToIso } from "../lib/datetime";
import { peerTitle } from "../lib/peerLabels";
import { useOperatorLabels } from "../lib/useOperatorLabels";

const STATUS_LABELS: Record<string, string> = {
  pending_approval: "待审批",
  scheduled: "已排期",
  evaluating: "评估中",
  waiting_quiet_hours: "安静时段",
  waiting_rate_limit: "限频等待",
  policy_blocked: "策略拦截",
  reapproval_required: "需重批",
  enqueued: "已入队",
  sent: "已发送",
  missed: "已错过",
  rejected: "已拒绝",
  approval_expired: "审批过期",
  cancelled: "已取消",
};

const MISSED_LABELS: Record<string, string> = {
  skip: "跳过",
  send_within_grace: "宽限补发",
  require_reapproval: "需重批",
};

function workerBadge(worker: { configured_enabled?: boolean; active?: boolean; paused?: boolean } | null) {
  if (!worker || !worker.configured_enabled) return "关闭";
  if (worker.paused) return "已暂停";
  if (worker.active) return "运行中";
  return "关闭";
}

function canCancel(status: string) {
  return ![
    "sent",
    "cancelled",
    "rejected",
    "missed",
    "approval_expired",
  ].includes(status);
}

export function ProactivePage() {
  const labels = useOperatorLabels();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("id") ?? "";
  const filterQq = searchParams.get("qq") ?? "";
  const [tasks, setTasks] = useState<ProactiveTaskItem[]>([]);
  const [detail, setDetail] = useState<ProactiveTaskItem | null>(null);
  const [summary, setSummary] = useState<ProactiveSummary | null>(null);
  const [policy, setPolicy] = useState<ProactiveUserPolicy | null>(null);
  const [targetQq, setTargetQq] = useState(filterQq);
  const [content, setContent] = useState("");
  const [scheduledLocal, setScheduledLocal] = useState("");
  const [missedPolicy, setMissedPolicy] = useState<"skip" | "send_within_grace" | "require_reapproval">(
    "skip",
  );
  const [policyQq, setPolicyQq] = useState(filterQq);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function reload(taskId = selectedId, qq = filterQq) {
    const [listed, summaryBody] = await Promise.all([
      api.proactiveTasks({ target_qq: qq || undefined }),
      api.proactiveSummary(),
    ]);
    setTasks(listed.items);
    setSummary(summaryBody);
    if (!taskId) {
      setDetail(null);
      return;
    }
    setDetail(await api.proactiveTask(taskId));
  }

  useEffect(() => {
    reload().catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    setTargetQq(filterQq);
    setPolicyQq(filterQq);
    if (!selectedId) {
      setDetail(null);
      return;
    }
    api
      .proactiveTask(selectedId)
      .then(setDetail)
      .catch((err: Error) => setError(err.message));
  }, [selectedId, filterQq]);

  const selected = useMemo(
    () => tasks.find((item) => item.id === selectedId) ?? detail,
    [tasks, selectedId, detail],
  );
  const scheduler: ProactiveSchedulerSnapshot | null = summary?.scheduler ?? null;
  const delivery: OutboxWorkerSnapshot | null = summary?.delivery ?? null;

  function select(next: { id?: string; qq?: string }) {
    const params = new URLSearchParams();
    const qq = next.qq ?? filterQq;
    const id = next.id ?? "";
    if (qq) params.set("qq", qq);
    if (id) params.set("id", id);
    setSearchParams(params, { replace: true });
  }

  async function applyFilter(event: FormEvent) {
    event.preventDefault();
    const qq = targetQq.trim();
    if (qq && !/^\d+$/.test(qq)) {
      setError("QQ 只接受数字，或留空看全部");
      return;
    }
    setError("");
    select({ qq, id: "" });
    try {
      await reload("", qq);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法筛选");
    }
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    const qq = targetQq.trim();
    if (!/^\d+$/.test(qq)) {
      setError("目标 QQ 只接受数字");
      return;
    }
    if (!content.trim() || !scheduledLocal.trim()) {
      setError("内容和计划时间都要填");
      return;
    }
    let scheduledFor = "";
    try {
      scheduledFor = shanghaiLocalToIso(scheduledLocal);
    } catch {
      setError("计划时间无效");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await api.createProactiveTask({
        target_qq: qq,
        content: content.trim(),
        scheduled_for: scheduledFor,
        timezone: "Asia/Shanghai",
        missed_policy: missedPolicy,
      });
      setNotice(
        saved.approval_code
          ? `已提交审批 ${saved.approval_code}。用户权限默认关闭，确认也不会立刻发 QQ。`
          : "已创建任务。",
      );
      select({ qq, id: saved.id });
      setContent("");
      await reload(saved.id, qq);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法创建");
    } finally {
      setBusy(false);
    }
  }

  async function cancelTask() {
    if (!selectedId) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.cancelProactiveTask(selectedId);
      setNotice("已取消。未领取的 outbox 会一起撤销，已发送不会假装成功。");
      await reload(selectedId, filterQq);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法取消");
    } finally {
      setBusy(false);
    }
  }

  async function loadPolicy(event: FormEvent) {
    event.preventDefault();
    const qq = policyQq.trim();
    if (!/^\d+$/.test(qq)) {
      setError("用户 QQ 只接受数字");
      return;
    }
    setBusy(true);
    setError("");
    try {
      setPolicy(await api.proactivePolicy(qq));
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法读取策略");
    } finally {
      setBusy(false);
    }
  }

  async function savePolicy(event: FormEvent) {
    event.preventDefault();
    if (!policy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await api.updateProactivePolicy(policy.user_qq, policy);
      setPolicy(saved);
      setNotice(saved.enabled ? "已开启该用户主动权限。" : "该用户主动权限已关闭。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法保存策略");
    } finally {
      setBusy(false);
    }
  }

  async function schedulerAction(action: "run-once" | "pause" | "resume") {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (action === "run-once") {
        const result = await api.proactiveWorkerRunOnce();
        setSummary((current) =>
          current ? { ...current, scheduler: result.worker } : current,
        );
        setNotice(
          result.status === "disabled"
            ? "调度 worker 关闭。不会写入 outbox。"
            : `调度单次：${result.status}${result.reason ? ` · ${result.reason}` : ""}`,
        );
      } else if (action === "pause") {
        const worker = await api.proactiveWorkerPause();
        setSummary((current) => (current ? { ...current, scheduler: worker } : current));
        setNotice("已请求暂停调度。");
      } else {
        const worker = await api.proactiveWorkerResume();
        setSummary((current) => (current ? { ...current, scheduler: worker } : current));
        setNotice("已请求恢复调度。仍受总开关约束。");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "调度 worker 操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function outboundAction(action: "run-once" | "pause" | "resume") {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (action === "run-once") {
        const worker = await api.outboxWorkerRunOnce();
        setSummary((current) => (current ? { ...current, delivery: worker } : current));
        setNotice(
          worker.configured_enabled
            ? `外发单次处理 ${worker.sent ?? 0} 条。`
            : "真实外发关闭。不会调用 NapCat。",
        );
      } else if (action === "pause") {
        const worker = await api.outboxWorkerPause();
        setSummary((current) => (current ? { ...current, delivery: worker } : current));
        setNotice("已请求暂停真实外发。");
      } else {
        const worker = await api.outboxWorkerResume();
        setSummary((current) => (current ? { ...current, delivery: worker } : current));
        setNotice("已请求恢复真实外发。仍受 YCH_OUTBOUND_ENABLED 约束。");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "外发 worker 操作失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageFrame>
      <PageTitle
        title="主动任务"
        description="真实日历时间，不是启动后计时。新用户默认关闭。调度与真实外发是两道关卡，确认不会立刻发 QQ。"
        badge={
          <span className="rounded-md bg-muted px-4 py-2 text-sm backdrop-blur">
            调度 {workerBadge(scheduler)} · 外发 {summary?.outbound_enabled ? "开" : "关闭"}
          </span>
        }
      />
      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}
      {notice ? <p className="mb-4 text-sm text-primary">{notice}</p> : null}
      <PageSplit className="xl:grid-cols-[minmax(320px,.9fr)_minmax(0,1.1fr)]">
        <PageColumn>
          <SurfaceCard>
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-semibold text-foreground">日历</h2>
              <form className="flex gap-2" onSubmit={applyFilter}>
                <input
                  value={targetQq}
                  onChange={(event) => setTargetQq(event.target.value)}
                  placeholder="按 QQ 筛选"
                  className="w-36 rounded-md border border-border bg-muted/50 px-3 py-1 text-xs"
                />
                <button type="submit" className="rounded-md bg-primary px-3 py-1 text-xs text-primary-foreground">
                  筛选
                </button>
              </form>
            </div>
            {tasks.length === 0 ? (
              <EmptyState tone="inline" className="mt-4" title="还没有主动任务。" />
            ) : (
              <ul className="mt-3 divide-y divide-black/5">
                {tasks.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={`flex w-full items-center justify-between gap-3 rounded-2xl px-2 py-3 text-left transition hover:bg-muted/60 ${
                        selectedId === item.id ? "bg-muted/70" : ""
                      }`}
                      onClick={() => select({ id: item.id })}
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-foreground">{item.content}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {peerTitle(item.target_qq, labels.user(item.target_qq))} ·{" "}
                          {item.scheduled_for_local || item.original_scheduled_for_local || "未排期"}
                        </p>
                      </div>
                      <span className="shrink-0 rounded-md bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                        {STATUS_LABELS[item.status] ?? item.status}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </SurfaceCard>
          <SurfaceCard>
            <h2 className="text-lg font-semibold text-foreground">创建任务</h2>
            <form className="mt-4 space-y-3" onSubmit={create}>
              <input
                value={targetQq}
                onChange={(event) => setTargetQq(event.target.value)}
                placeholder="目标 QQ"
                className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <textarea
                value={content}
                onChange={(event) => setContent(event.target.value)}
                placeholder="正文，最多 2000 字"
                rows={3}
                className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <DateTimePicker
                value={scheduledLocal}
                onChange={setScheduledLocal}
                label="计划发送时间"
                allowClear={false}
              />
              <Select value={missedPolicy} onChange={(next) => setMissedPolicy(next as "skip" | "send_within_grace" | "require_reapproval")} options={[{ value: "skip", label: "错过：跳过" }, { value: "send_within_grace", label: "错过：宽限补发" }, { value: "require_reapproval", label: "错过：需重批" }]} aria-label="错过任务策略" />
              <button
                type="submit"
                disabled={busy}
                className="rounded-md bg-primary px-5 py-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                提交审批
              </button>
            </form>
          </SurfaceCard>
        </PageColumn>
        <PageColumn>
          <SurfaceCard>
            {selected ? (
              <div>
                <p className="text-xs font-medium tracking-wide text-muted-foreground">任务详情</p>
                <h2 className="mt-3 text-2xl font-semibold text-foreground">
                  {STATUS_LABELS[selected.status] ?? selected.status}
                </h2>
                <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-foreground">{selected.content}</p>
                <dl className="mt-4 space-y-2 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">用户</dt>
                    <dd className="font-medium text-foreground">
                      {peerTitle(selected.target_qq, labels.user(selected.target_qq))}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">计划时间</dt>
                    <dd className="max-w-[60%] text-right font-medium text-foreground">
                      {selected.scheduled_for_local || selected.original_scheduled_for_local || "无"}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">错过策略</dt>
                    <dd className="font-medium text-foreground">
                      {MISSED_LABELS[selected.missed_policy ?? ""] ?? selected.missed_policy ?? "—"}
                    </dd>
                  </div>
                </dl>
                {selected.hold_reason ? (
                  <p className="mt-4 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-700">
                    {selected.hold_reason}
                  </p>
                ) : null}
                <div className="mt-5 flex flex-wrap gap-2">
                  {canCancel(selected.status) ? (
                    <button
                      type="button"
                      disabled={busy}
                      className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                      onClick={cancelTask}
                    >
                      取消
                    </button>
                  ) : null}
                  <Link to="/approvals" className="rounded-md bg-muted px-4 py-2 text-sm text-foreground">
                    去汇报与审批
                  </Link>
                </div>
              </div>
            ) : (
              <EmptyState tone="select" title="从左侧选一条任务，或先创建。" />
            )}
          </SurfaceCard>
          <SurfaceCard>
            <h2 className="text-lg font-semibold text-foreground">用户策略</h2>
            <p className="mt-2 text-xs text-muted-foreground">创建任务不会暗中开启权限。必须显式打开。</p>
            <form className="mt-4 flex gap-2" onSubmit={loadPolicy}>
              <input
                value={policyQq}
                onChange={(event) => setPolicyQq(event.target.value)}
                placeholder="用户 QQ"
                className="flex-1 rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <button type="submit" className="rounded-md bg-primary px-4 py-3 text-sm text-primary-foreground">
                读取
              </button>
            </form>
            {policy ? (
              <form className="mt-4 space-y-3" onSubmit={savePolicy}>
                <label className="flex items-center gap-2 text-sm text-foreground">
                  <input
                    type="checkbox"
                    className="accent-primary"
                    checked={policy.enabled}
                    onChange={(event) => setPolicy({ ...policy, enabled: event.target.checked })}
                  />
                  允许主动消息
                </label>
                <label className="flex items-center gap-2 text-sm text-foreground">
                  <input
                    type="checkbox"
                    className="accent-primary"
                    checked={policy.quiet_hours_enabled}
                    onChange={(event) =>
                      setPolicy({ ...policy, quiet_hours_enabled: event.target.checked })
                    }
                  />
                  安静时段 {policy.quiet_start}–{policy.quiet_end}
                </label>
                <label className="flex items-start gap-2 text-sm text-foreground">
                  <input
                    type="checkbox"
                    className="mt-0.5 accent-primary"
                    checked={Boolean(policy.auto_content_enabled)}
                    onChange={(event) =>
                      setPolicy({ ...policy, auto_content_enabled: event.target.checked })
                    }
                  />
                  <span>
                    允许每日自动找话题
                    <span className="mt-0.5 block text-xs font-normal text-muted-foreground">
                      材料或最近上下文，每人每天至多一条；默认关闭。
                    </span>
                  </span>
                </label>
                <label className="flex items-start gap-2 text-sm text-foreground">
                  <input
                    type="checkbox"
                    className="mt-0.5 accent-primary"
                    checked={Boolean(policy.send_diary)}
                    onChange={(event) =>
                      setPolicy({ ...policy, send_diary: event.target.checked })
                    }
                  />
                  <span>
                    允许发送 YCH 当日日记
                    <span className="mt-0.5 block text-xs font-normal text-muted-foreground">
                      单独授权；日记为空不发，仍受安静时段与真实外发总开关约束。
                    </span>
                  </span>
                </label>
                <button
                  type="submit"
                  disabled={busy}
                  className="rounded-md bg-primary px-5 py-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
                >
                  保存策略
                </button>
              </form>
            ) : null}
          </SurfaceCard>
          <SurfaceCard>
            <h2 className="text-lg font-semibold text-foreground">调度 / 外发</h2>
            <dl className="mt-4 space-y-2 text-sm">
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">调度</dt>
                <dd className="font-medium text-foreground">{workerBadge(scheduler)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">真实外发</dt>
                <dd className="font-medium text-foreground">
                  {summary?.outbound_enabled ? workerBadge(delivery) : "关闭"}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">入队 / 错过</dt>
                <dd className="font-medium text-foreground">
                  {scheduler?.enqueued_tasks ?? 0} / {scheduler?.missed_tasks ?? 0}
                </dd>
              </div>
            </dl>
            <div className="mt-5 flex flex-wrap gap-2">
              <button type="button" disabled={busy} className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60" onClick={() => schedulerAction("run-once")}>
                调度一次
              </button>
              <button type="button" disabled={busy} className="rounded-md bg-muted px-4 py-2 text-sm text-foreground disabled:opacity-60" onClick={() => schedulerAction("pause")}>
                暂停调度
              </button>
              <button type="button" disabled={busy} className="rounded-md bg-muted px-4 py-2 text-sm text-foreground disabled:opacity-60" onClick={() => outboundAction("pause")}>
                暂停外发
              </button>
            </div>
          </SurfaceCard>
        </PageColumn>
      </PageSplit>
    </PageFrame>
  );
}
