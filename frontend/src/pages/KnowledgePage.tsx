import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { PageColumn, PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { Select } from "../components/Select";
import { SurfaceCard } from "../components/SurfaceCard";
import {
  api,
  type KnowledgeJobItem,
  type KnowledgeWorkerSnapshot,
} from "../lib/api";
import { peerTitle } from "../lib/peerLabels";
import { useOperatorLabels } from "../lib/useOperatorLabels";

const PURPOSE_LABELS: Record<string, string> = {
  user_understanding: "认识用户",
  persona_design: "专属人格",
};

const STATUS_LABELS: Record<string, string> = {
  awaiting_model_config: "等待模型配置",
  queued: "排队",
  processing: "处理中",
  awaiting_approval: "待审批",
  approved: "已批准",
  failed: "失败",
  rejected: "已拒绝",
};

type Purpose = "user_understanding" | "persona_design";

function workerBadge(worker: KnowledgeWorkerSnapshot | null) {
  if (!worker || !worker.configured_enabled) return "关闭";
  if (worker.paused) return "已暂停";
  if (worker.active) return "运行中";
  return "关闭";
}

export function KnowledgePage() {
  const labels = useOperatorLabels();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("id") ?? "";
  const filterQq = searchParams.get("qq") ?? "";
  const [jobs, setJobs] = useState<KnowledgeJobItem[]>([]);
  const [detail, setDetail] = useState<KnowledgeJobItem | null>(null);
  const [worker, setWorker] = useState<KnowledgeWorkerSnapshot | null>(null);
  const [userQq, setUserQq] = useState(filterQq);
  const [purpose, setPurpose] = useState<Purpose>("user_understanding");
  const [file, setFile] = useState<File | null>(null);
  const [lookup, setLookup] = useState(filterQq);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function reload(qq = filterQq, jobId = selectedId) {
    const [listed, workerBody] = await Promise.all([
      api.knowledgeJobs(qq || undefined),
      api.knowledgeWorker(),
    ]);
    setJobs(listed.items);
    setWorker(workerBody);
    if (!jobId) {
      setDetail(null);
      return;
    }
    setDetail(await api.knowledgeJob(jobId));
  }

  useEffect(() => {
    reload().catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    setLookup(filterQq);
    setUserQq(filterQq);
    if (!selectedId) {
      setDetail(null);
      api
        .knowledgeJobs(filterQq || undefined)
        .then((body) => setJobs(body.items))
        .catch((err: Error) => setError(err.message));
      return;
    }
    api
      .knowledgeJob(selectedId)
      .then(setDetail)
      .catch((err: Error) => setError(err.message));
  }, [selectedId, filterQq]);

  const selected = useMemo(
    () => jobs.find((item) => item.id === selectedId) ?? detail,
    [jobs, selectedId, detail],
  );

  function select(next: { id?: string; qq?: string }) {
    const params = new URLSearchParams();
    const qq = next.qq ?? filterQq;
    const id = next.id ?? "";
    if (qq) params.set("qq", qq);
    if (id) params.set("id", id);
    setSearchParams(params, { replace: true });
  }

  function applyFilter(event: FormEvent) {
    event.preventDefault();
    const qq = lookup.trim();
    if (qq && !/^\d+$/.test(qq)) {
      setError("QQ 只接受数字，或留空看全部");
      return;
    }
    setError("");
    select({ qq, id: "" });
    reload(qq, "").catch((err: Error) => setError(err.message));
  }

  async function upload(event: FormEvent) {
    event.preventDefault();
    const qq = userQq.trim();
    if (!/^\d+$/.test(qq)) {
      setError("用户 QQ 只接受数字");
      return;
    }
    if (!file) {
      setError("先选择一个文件");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.uploadKnowledgeDocument(qq, purpose, file);
      setNotice(
        result.status === "awaiting_model_config"
          ? "已暂存。模型与炼化开关关闭时停在「等待模型配置」，不会触网。"
          : `已创建任务，状态 ${STATUS_LABELS[result.status] ?? result.status}。`,
      );
      setFile(null);
      select({ qq, id: result.job_id });
      await reload(qq, result.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }

  async function processJob() {
    if (!selectedId) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.processKnowledgeJob(selectedId);
      setNotice(
        `处理返回 ${STATUS_LABELS[result.status] ?? result.status} · 块 ${result.completed_chunks}/${result.total_chunks}`,
      );
      await reload(filterQq, selectedId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法处理");
    } finally {
      setBusy(false);
    }
  }

  async function approveJob() {
    if (!selectedId) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.approveKnowledgeJob(selectedId);
      setNotice("已批准预览。只会写入用户理解或派生人格，不会自动写永久记忆。");
      await reload(filterQq, selectedId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法批准");
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
        const result = await api.knowledgeWorkerRunOnce();
        setWorker(result.worker);
        setNotice(
          result.status === "disabled"
            ? "Worker 关闭。单次执行不会启动模型。"
            : `单次执行：${result.status}${result.reason ? ` · ${result.reason}` : ""}`,
        );
      } else if (action === "pause") {
        setWorker(await api.knowledgeWorkerPause());
        setNotice("已请求暂停。开关关闭时不会真正改状态。");
      } else {
        setWorker(await api.knowledgeWorkerResume());
        setNotice("已请求恢复。仍受 .env 总开关约束。");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Worker 操作失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageFrame>
      <PageTitle
        title="资料炼化"
        description="两种用途严格分离：认识用户，或设计面对该用户的人格。默认停在等待模型配置，不会触网、不会自动写记忆。"
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
            <h2 className="text-lg font-semibold text-foreground">导入文件</h2>
            <p className="mt-2 text-xs text-muted-foreground">
              TXT/Markdown/JSON/CSV/HTML/DOCX/带文本层 PDF。原文件进本地 imports，不进 Git。
            </p>
            <form className="mt-4 space-y-3" onSubmit={upload}>
              <input
                value={userQq}
                onChange={(event) => setUserQq(event.target.value)}
                placeholder="用户 QQ"
                className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <Select value={purpose} onChange={(next) => setPurpose(next as Purpose)} options={[{ value: "user_understanding", label: "认识用户" }, { value: "persona_design", label: "专属人格" }]} aria-label="资料用途" />
              <input
                type="file"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <button
                type="submit"
                disabled={busy}
                className="rounded-md bg-primary px-5 py-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                上传并创建任务
              </button>
            </form>
          </SurfaceCard>
          <SurfaceCard>
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-semibold text-foreground">任务列表</h2>
              <form className="flex gap-2" onSubmit={applyFilter}>
                <input
                  value={lookup}
                  onChange={(event) => setLookup(event.target.value)}
                  placeholder="按 QQ 筛选"
                  className="w-36 rounded-md border border-border bg-muted/50 px-3 py-1 text-xs"
                />
                <button type="submit" className="rounded-md bg-primary px-3 py-1 text-xs text-primary-foreground">
                  筛选
                </button>
              </form>
            </div>
            {jobs.length === 0 ? (
              <EmptyState
                tone="inline"
                className="mt-4"
                title="还没有资料任务。"
                detail="上传一个文件开始。"
              />
            ) : (
              <ul className="mt-3 divide-y divide-black/5">
                {jobs.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={`flex w-full items-center justify-between gap-3 rounded-2xl px-2 py-3 text-left transition hover:bg-muted/60 ${
                        selectedId === item.id ? "bg-muted/70" : ""
                      }`}
                      onClick={() => select({ id: item.id, qq: filterQq })}
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-foreground">
                          {item.original_filename || item.id.slice(0, 8)}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {peerTitle(item.user_qq, labels.user(item.user_qq))} ·{" "}
                          {PURPOSE_LABELS[item.purpose] ?? item.purpose}
                          {typeof item.analyzed_chunk_count === "number" &&
                          typeof item.chunk_count === "number"
                            ? ` · 块 ${item.analyzed_chunk_count}/${item.chunk_count}`
                            : ""}
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
        </PageColumn>
        <PageColumn>
          <SurfaceCard>
            {selected ? (
              <div>
                <p className="text-xs font-medium tracking-wide text-muted-foreground">任务详情</p>
                <h2 className="mt-3 text-2xl font-semibold text-foreground">
                  {selected.original_filename || selected.id.slice(0, 8)}
                </h2>
                <dl className="mt-4 space-y-2 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">用户</dt>
                    <dd className="font-medium text-foreground">
                      {peerTitle(selected.user_qq, labels.user(selected.user_qq))}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">用途</dt>
                    <dd className="font-medium text-foreground">
                      {PURPOSE_LABELS[selected.purpose] ?? selected.purpose}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">状态</dt>
                    <dd className="font-medium text-foreground">
                      {STATUS_LABELS[selected.status] ?? selected.status}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">格式</dt>
                    <dd className="font-medium text-foreground">{selected.detected_format ?? "—"}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">块进度</dt>
                    <dd className="font-medium text-foreground">
                      {selected.analyzed_chunk_count ?? 0}/{selected.chunk_count ?? 0}
                      {selected.attempt_count != null ? ` · 尝试 ${selected.attempt_count}` : ""}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">审批</dt>
                    <dd className="font-medium text-foreground">
                      {selected.approval_code
                        ? `${selected.approval_code} · ${selected.approval_status}`
                        : "无"}
                    </dd>
                  </div>
                </dl>
                {selected.error ? (
                  <p className="mt-4 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-700">
                    {selected.error}
                  </p>
                ) : null}
                {selected.result_preview ? (
                  <pre className="mt-4 max-h-64 overflow-auto rounded-2xl bg-muted/50 p-4 text-xs text-foreground">
                    {JSON.stringify(selected.result_preview, null, 2)}
                  </pre>
                ) : (
                  <EmptyState
                    tone="inline"
                    className="mt-4 py-0"
                    title="还没有预览结果。"
                    detail="批准前不会写入人格或画像。"
                  />
                )}
                <div className="mt-5 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                    onClick={processJob}
                  >
                    显式处理
                  </button>
                  <button
                    type="button"
                    disabled={busy || selected.status !== "awaiting_approval"}
                    className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                    onClick={approveJob}
                  >
                    批准预览
                  </button>
                  {selected.approval_code ? (
                    <Link
                      to="/approvals"
                      className="rounded-md bg-muted px-4 py-2 text-sm text-foreground"
                    >
                      去汇报与审批
                    </Link>
                  ) : null}
                </div>
                <p className="mt-3 text-xs text-muted-foreground">
                  显式处理在模型关闭时会返回冲突错误，这是预期的安全关卡。
                </p>
              </div>
            ) : (
              <EmptyState tone="select" title="从左侧选一个任务，或先上传文件。" />
            )}
          </SurfaceCard>
          <SurfaceCard>
            <h2 className="text-lg font-semibold text-foreground">资料 Worker</h2>
            <dl className="mt-4 space-y-2 text-sm">
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">状态</dt>
                <dd className="font-medium text-foreground">{workerBadge(worker)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">炼化路由</dt>
                <dd className="font-medium text-foreground">
                  {worker?.processing_route_enabled ? "开" : "关"}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">完成 / 失败 / 耗尽</dt>
                <dd className="font-medium text-foreground">
                  {worker?.completed_jobs ?? 0} / {worker?.failed_attempts ?? 0} /{" "}
                  {worker?.exhausted_jobs ?? 0}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">最近任务</dt>
                <dd className="max-w-[55%] truncate font-medium text-foreground">
                  {worker?.last_job_id ?? "无"}
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
            <p className="mt-3 text-xs text-muted-foreground">
              Worker、炼化开关和模型网络是三道关卡，默认全部关闭。
            </p>
          </SurfaceCard>
        </PageColumn>
      </PageSplit>
    </PageFrame>
  );
}
