import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { DangerCard } from "../components/DangerCard";
import { DateTimePicker } from "../components/DateTimePicker";
import { EmptyState } from "../components/EmptyState";
import { PageColumn, PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { Select } from "../components/Select";
import { SurfaceCard } from "../components/SurfaceCard";
import {
  api,
  type QzonePolicy,
  type QzonePostItem,
  type QzoneSummary,
  type QzoneVisibility,
  type QzoneWorkerSnapshot,
} from "../lib/api";
import { shanghaiLocalToIso } from "../lib/datetime";

const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  pending_approval: "待审批",
  approved: "已批准",
  scheduled: "已排期",
  evaluating: "评估中",
  publishing: "发布中",
  published: "已发布",
  waiting_quiet_hours: "安静时段",
  waiting_rate_limit: "限频等待",
  reapproval_required: "需重批",
  missed: "已错过",
  rejected: "已拒绝",
  delivery_uncertain: "发布不确定",
  approval_expired: "审批过期",
  failed: "失败",
  cancelled: "已取消",
  pending_delete_approval: "待撤审批",
  delete_approved: "撤销已批",
  deleting: "撤销中",
  deleted: "已删除",
  delete_uncertain: "撤销不确定",
};

const VISIBILITY_LABELS: Record<number, string> = {
  1: "所有人",
  4: "好友",
  16: "指定好友",
  64: "仅自己",
  128: "排除好友",
};

function workerBadge(worker: QzoneWorkerSnapshot | null) {
  if (!worker || !worker.configured_enabled || !worker.publish_route_enabled) return "关闭";
  if (worker.paused) return "已暂停";
  if (worker.active) return "运行中";
  return "关闭";
}

function needsTargets(visibility: number) {
  return visibility === 16 || visibility === 128;
}

function parseTargets(raw: string) {
  return [...new Set(raw.split(/[\s,，]+/).map((item) => item.trim()).filter(Boolean))];
}

function canCancel(status: string) {
  return ["draft", "pending_approval", "approved", "scheduled", "reapproval_required"].includes(
    status,
  );
}

export function QzonePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("id") ?? "";
  const statusFilter = searchParams.get("status") ?? "";
  const [posts, setPosts] = useState<QzonePostItem[]>([]);
  const [detail, setDetail] = useState<QzonePostItem | null>(null);
  const [summary, setSummary] = useState<QzoneSummary | null>(null);
  const [policy, setPolicy] = useState<QzonePolicy | null>(null);
  const [content, setContent] = useState("");
  const [visibility, setVisibility] = useState<QzoneVisibility>(4);
  const [targets, setTargets] = useState("");
  const [scheduledLocal, setScheduledLocal] = useState("");
  const [asDraft, setAsDraft] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function reload(postId = selectedId, status = statusFilter) {
    const [listed, summaryBody, policyBody] = await Promise.all([
      api.qzonePosts(status || undefined),
      api.qzoneSummary(),
      api.qzonePolicy(),
    ]);
    setPosts(listed.items);
    setSummary(summaryBody);
    setPolicy(policyBody);
    if (!postId) {
      setDetail(null);
      return;
    }
    setDetail(await api.qzonePost(postId));
  }

  useEffect(() => {
    reload().catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    api
      .qzonePost(selectedId)
      .then(setDetail)
      .catch((err: Error) => setError(err.message));
  }, [selectedId]);

  const selected = useMemo(
    () => posts.find((item) => item.id === selectedId) ?? detail,
    [posts, selectedId, detail],
  );
  const worker = summary?.publisher ?? null;

  function select(next: { id?: string; status?: string }) {
    const params = new URLSearchParams();
    const status = next.status ?? statusFilter;
    const id = next.id ?? "";
    if (status) params.set("status", status);
    if (id) params.set("id", id);
    setSearchParams(params, { replace: true });
  }

  async function applyFilter(status: string) {
    select({ status, id: "" });
    try {
      await reload("", status);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法筛选");
    }
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    const text = content.trim();
    if (!text) {
      setError("内容不能为空");
      return;
    }
    const target_uins = parseTargets(targets);
    if (needsTargets(visibility) && target_uins.length === 0) {
      setError("指定或排除好友必须填写目标 QQ");
      return;
    }
    if (!needsTargets(visibility) && target_uins.length) {
      setError("该可见范围不能带目标 QQ");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (asDraft) {
        const saved = await api.createQzoneDraft({
          content: text,
          visibility,
          target_uins: needsTargets(visibility) ? target_uins : [],
        });
        setNotice("已存草稿。草稿不会生成审批，也不会进发布 worker。");
        select({ id: saved.id });
        await reload(saved.id, statusFilter);
      } else {
        const body: {
          content: string;
          visibility: QzoneVisibility;
          target_uins?: string[];
          scheduled_for?: string;
          timezone?: string;
        } = {
          content: text,
          visibility,
          target_uins: needsTargets(visibility) ? target_uins : [],
          timezone: policy?.timezone || "Asia/Shanghai",
        };
        if (scheduledLocal.trim()) {
          body.scheduled_for = shanghaiLocalToIso(scheduledLocal);
        }
        const saved = await api.createQzonePost(body);
        setNotice(
          saved.approval_code
            ? `已提交发布审批 ${saved.approval_code}。确认不会立刻发说说。`
            : "已创建发布请求。",
        );
        select({ id: saved.id });
        await reload(saved.id, statusFilter);
      }
      setContent("");
      setTargets("");
      setScheduledLocal("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法创建");
    } finally {
      setBusy(false);
    }
  }

  async function cancelPost() {
    if (!selectedId) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.cancelQzonePost(selectedId);
      setNotice("已取消。未发布内容不会调用 NapCat。");
      await reload(selectedId, statusFilter);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法取消");
    } finally {
      setBusy(false);
    }
  }

  async function revokePost() {
    if (!selectedId) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.revokeQzonePost(selectedId);
      setNotice(
        result.approval_code
          ? `已申请撤销 ${result.approval_code}，仍需主号确认。`
          : "已申请撤销。",
      );
      await reload(selectedId, statusFilter);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法撤销");
    } finally {
      setBusy(false);
    }
  }

  async function workerAction(action: "run-once" | "pause" | "resume") {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (action === "run-once") {
        const result = await api.qzoneWorkerRunOnce();
        setSummary((current) =>
          current ? { ...current, publisher: result.worker } : current,
        );
        setNotice(
          result.status === "disabled"
            ? "Worker 与发布开关关闭。单次执行不会发说说。"
            : `单次执行：${result.status}${result.reason ? ` · ${result.reason}` : ""}`,
        );
      } else if (action === "pause") {
        const workerBody = await api.qzoneWorkerPause();
        setSummary((current) => (current ? { ...current, publisher: workerBody } : current));
        setNotice("已请求暂停。开关关闭时不会真正改状态。");
      } else {
        const workerBody = await api.qzoneWorkerResume();
        setSummary((current) => (current ? { ...current, publisher: workerBody } : current));
        setNotice("已请求恢复。仍受 worker 与发布总开关约束。");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Worker 操作失败");
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
      setPolicy(await api.updateQzonePolicy(policy));
      setNotice("已保存全站安静时段与频率。不会立刻发说说。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法保存策略");
    } finally {
      setBusy(false);
    }
  }

  const publishedWithTid = Boolean(
    selected && selected.status === "published" && selected.qzone_tid,
  );

  return (
    <PageFrame>
      <PageTitle
        title="空间工作台"
        description="草稿不进审批。发布必须主号确认，再用真实日历时间调度。Worker 与发布开关默认关闭，禁止自动网络重试。"
        badge={
          <span className="rounded-md bg-muted px-4 py-2 text-sm backdrop-blur">
            Worker {workerBadge(worker)}
          </span>
        }
      />
      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}
      {notice ? <p className="mb-4 text-sm text-primary">{notice}</p> : null}
      <PageSplit className="xl:grid-cols-[minmax(320px,.9fr)_minmax(0,1.1fr)]">
        <PageColumn>
          <SurfaceCard>
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-semibold text-foreground">日历队列</h2>
              <Select size="pill" value={statusFilter || "all"} onChange={(next) => applyFilter(next === "all" ? "" : next)} options={[{ value: "all", label: "全部" }, { value: "draft", label: "草稿" }, { value: "pending_approval", label: "待审批" }, { value: "scheduled", label: "已排期" }, { value: "published", label: "已发布" }, { value: "delivery_uncertain", label: "发布不确定" }, { value: "delete_uncertain", label: "撤销不确定" }, { value: "cancelled", label: "已取消" }]} aria-label="空间任务状态" />
            </div>
            {posts.length === 0 ? (
              <EmptyState
                tone="inline"
                className="mt-4"
                title="还没有空间任务。"
                detail="先写一条草稿。"
              />
            ) : (
              <ul className="mt-3 divide-y divide-black/5">
                {posts.map((item) => (
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
                          {item.scheduled_for_local || item.original_scheduled_for_local || "未排期"}
                          {" · "}
                          {VISIBILITY_LABELS[item.visibility] ?? item.visibility}
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
            <h2 className="text-lg font-semibold text-foreground">新建</h2>
            <form className="mt-4 space-y-3" onSubmit={create}>
              <textarea
                value={content}
                onChange={(event) => setContent(event.target.value)}
                placeholder="说说正文，最多 2000 字"
                rows={4}
                className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <Select value={String(visibility)} onChange={(next) => setVisibility(Number(next) as QzoneVisibility)} options={[{ value: "4", label: "好友" }, { value: "1", label: "所有人" }, { value: "64", label: "仅自己" }, { value: "16", label: "指定好友" }, { value: "128", label: "排除好友" }]} aria-label="空间可见范围" />
              {needsTargets(visibility) ? (
                <textarea
                  value={targets}
                  onChange={(event) => setTargets(event.target.value)}
                  placeholder="目标 QQ，逗号或换行分隔"
                  rows={2}
                  className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
                />
              ) : null}
              <label className="flex items-center gap-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  className="accent-primary"
                  checked={asDraft}
                  onChange={(event) => setAsDraft(event.target.checked)}
                />
                只存草稿（不生成审批）
              </label>
              {!asDraft ? (
                <DateTimePicker
                  value={scheduledLocal}
                  onChange={setScheduledLocal}
                  label="计划发布时间"
                  allowClear={false}
                />
              ) : null}
              <button
                type="submit"
                disabled={busy}
                className="rounded-md bg-primary px-5 py-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                {asDraft ? "保存草稿" : "提交发布审批"}
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
                <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-foreground">
                  {selected.content}
                </p>
                <dl className="mt-4 space-y-2 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">可见</dt>
                    <dd className="font-medium text-foreground">
                      {VISIBILITY_LABELS[selected.visibility] ?? selected.visibility}
                      {selected.target_uins?.length ? ` · ${selected.target_uins.join(", ")}` : ""}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">计划时间</dt>
                    <dd className="max-w-[60%] text-right font-medium text-foreground">
                      {selected.scheduled_for_local ||
                        selected.original_scheduled_for_local ||
                        "无"}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">tid</dt>
                    <dd className="font-medium text-foreground">{selected.qzone_tid || "无"}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">错过策略</dt>
                    <dd className="font-medium text-foreground">
                      {selected.missed_policy === "require_reapproval"
                        ? "需重批"
                        : selected.missed_policy || "—"}
                    </dd>
                  </div>
                </dl>
                {selected.hold_reason || selected.last_error ? (
                  <p className="mt-4 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-700">
                    {selected.hold_reason || selected.last_error}
                  </p>
                ) : null}
                {selected.events?.length ? (
                  <ul className="mt-4 space-y-2 text-xs text-muted-foreground">
                    {selected.events.map((item) => (
                      <li key={item.id}>
                        {item.event_type}
                        {item.reason ? ` · ${item.reason}` : ""} · {item.occurred_at}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState tone="inline" className="mt-4 py-0 text-xs" title="还没有事件时间线。" />
                )}
                <div className="mt-5 flex flex-wrap gap-2">
                  {canCancel(selected.status) ? (
                    <button
                      type="button"
                      disabled={busy}
                      className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                      onClick={cancelPost}
                    >
                      取消
                    </button>
                  ) : null}
                  {selected.approval_code || selected.status === "pending_approval" ? (
                    <Link
                      to="/approvals"
                      className="rounded-md bg-muted px-4 py-2 text-sm text-foreground"
                    >
                      去汇报与审批
                    </Link>
                  ) : null}
                </div>
                {publishedWithTid ? (
                  <DangerCard className="mt-5">
                    <p className="text-sm font-medium text-red-800">申请撤销已发布说说</p>
                    <p className="mt-2 text-sm leading-6 text-red-700/90">
                      只有已发布且有 tid 才能申请。仍需主号确认；发布开关关闭时不会真的删说说。
                    </p>
                    <button
                      type="button"
                      disabled={busy}
                      className="mt-4 rounded-md bg-red-700 px-4 py-2 text-sm text-white disabled:opacity-60"
                      onClick={revokePost}
                    >
                      申请撤销
                    </button>
                  </DangerCard>
                ) : (
                  <p className="mt-4 text-xs text-muted-foreground">
                    草稿、未发布和发布不确定（无 tid）不能撤销。
                  </p>
                )}
              </div>
            ) : (
              <EmptyState tone="select" title="从左侧选一条任务，或先写草稿。" />
            )}
          </SurfaceCard>
          <SurfaceCard>
            <h2 className="text-lg font-semibold text-foreground">空间 Worker</h2>
            <dl className="mt-4 space-y-2 text-sm">
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">状态</dt>
                <dd className="font-medium text-foreground">{workerBadge(worker)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">发布路由</dt>
                <dd className="font-medium text-foreground">
                  {worker?.publish_route_enabled ? "开" : "关"}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">自动网络重试</dt>
                <dd className="font-medium text-foreground">
                  {summary?.automatic_network_retry ? "开" : "禁止"}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">不确定 / 错过</dt>
                <dd className="font-medium text-foreground">
                  {worker?.uncertain_count ?? 0} / {worker?.missed_count ?? 0}
                </dd>
              </div>
            </dl>
            <div className="mt-5 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy}
                className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                onClick={() => workerAction("run-once")}
              >
                单次执行
              </button>
              <button
                type="button"
                disabled={busy}
                className="rounded-md bg-muted px-4 py-2 text-sm text-foreground disabled:opacity-60"
                onClick={() => workerAction("pause")}
              >
                暂停
              </button>
              <button
                type="button"
                disabled={busy}
                className="rounded-md bg-muted px-4 py-2 text-sm text-foreground disabled:opacity-60"
                onClick={() => workerAction("resume")}
              >
                恢复
              </button>
            </div>
          </SurfaceCard>
          <SurfaceCard>
            <h2 className="text-lg font-semibold text-foreground">全站策略</h2>
            {policy ? (
              <form className="mt-4 space-y-3" onSubmit={savePolicy}>
                <label className="flex items-center gap-2 text-sm text-foreground">
                  <input
                    type="checkbox"
                    className="accent-primary"
                    checked={policy.quiet_hours_enabled}
                    onChange={(event) =>
                      setPolicy({ ...policy, quiet_hours_enabled: event.target.checked })
                    }
                  />
                  安静时段
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <input
                    value={policy.quiet_start}
                    onChange={(event) => setPolicy({ ...policy, quiet_start: event.target.value })}
                    className="rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
                  />
                  <input
                    value={policy.quiet_end}
                    onChange={(event) => setPolicy({ ...policy, quiet_end: event.target.value })}
                    className="rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
                  />
                </div>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={policy.daily_limit}
                  onChange={(event) =>
                    setPolicy({ ...policy, daily_limit: Number(event.target.value) })
                  }
                  className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
                />
                <p className="text-xs text-muted-foreground">每日上限 · 时区 {policy.timezone}</p>
                <button
                  type="submit"
                  disabled={busy}
                  className="rounded-md bg-primary px-5 py-3 text-sm text-primary-foreground disabled:opacity-60"
                >
                  保存策略
                </button>
              </form>
            ) : (
              <p className="mt-4 text-sm text-muted-foreground">正在读取策略。</p>
            )}
          </SurfaceCard>
        </PageColumn>
      </PageSplit>
    </PageFrame>
  );
}
