import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { PageColumn, PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { Select } from "../components/Select";
import { SurfaceCard } from "../components/SurfaceCard";
import {
  api,
  type ConversationContextPreview,
  type InferenceRunDetail,
  type InferenceRunItem,
  type ReplyCandidateItem,
} from "../lib/api";
import { peerTitle } from "../lib/peerLabels";
import { useOperatorLabels } from "../lib/useOperatorLabels";

const RUN_STATUS_LABELS: Record<string, string> = {
  completed: "完成",
  failed: "失败",
  skipped: "跳过",
  generated: "已生成",
};

const KIND_LABELS: Record<string, string> = {
  private: "私聊",
  group: "群聊",
};

type Tab = "runs" | "candidates";

function flagList(flags: string[] | Record<string, unknown> | undefined) {
  if (!flags) return [];
  if (Array.isArray(flags)) return flags;
  return Object.keys(flags).filter((key) => Boolean(flags[key]));
}

export function ConversationsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: Tab = searchParams.get("tab") === "candidates" ? "candidates" : "runs";
  const selectedId = searchParams.get("id") ?? "";
  const labels = useOperatorLabels();
  const [runs, setRuns] = useState<InferenceRunItem[]>([]);
  const [candidates, setCandidates] = useState<ReplyCandidateItem[]>([]);
  const [detail, setDetail] = useState<InferenceRunDetail | null>(null);
  const [preview, setPreview] = useState<ConversationContextPreview | null>(null);
  const [shadowEnabled, setShadowEnabled] = useState(false);
  const [kind, setKind] = useState<"private" | "group">("private");
  const [peerId, setPeerId] = useState("");
  const [actorQq, setActorQq] = useState("");
  const [messageId, setMessageId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function reload(runId = selectedId) {
    const [runBody, candidateBody, privacy] = await Promise.all([
      api.inferenceRuns(),
      api.replyCandidates(),
      api.privacyStatus(),
    ]);
    setRuns(runBody.items);
    setCandidates(candidateBody.items);
    setShadowEnabled(Boolean(privacy.shadow_inference_enabled));
    if (tab !== "runs" || !runId) {
      setDetail(null);
      return;
    }
    setDetail(await api.inferenceRun(runId));
  }

  useEffect(() => {
    reload().catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    if (tab !== "runs" || !selectedId) {
      setDetail(null);
      return;
    }
    api
      .inferenceRun(selectedId)
      .then(setDetail)
      .catch((err: Error) => setError(err.message));
  }, [tab, selectedId]);

  const selectedRun = useMemo(
    () => runs.find((item) => item.id === selectedId) ?? detail,
    [runs, selectedId, detail],
  );
  const selectedCandidate = useMemo(
    () => candidates.find((item) => item.id === selectedId) ?? null,
    [candidates, selectedId],
  );

  function select(next: { tab?: Tab; id?: string }) {
    const params = new URLSearchParams();
    const nextTab = next.tab ?? tab;
    const id = next.id ?? "";
    if (nextTab !== "runs") params.set("tab", nextTab);
    if (id) params.set("id", id);
    setSearchParams(params, { replace: true });
  }

  async function runPreview(event: FormEvent) {
    event.preventDefault();
    if (!/^\d+$/.test(peerId.trim()) || !/^\d+$/.test(actorQq.trim())) {
      setError("对象与发送者 QQ 只接受数字");
      return;
    }
    setBusy(true);
    setError("");
    try {
      setPreview(await api.conversationPreview(kind, peerId.trim(), actorQq.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法预览上下文");
    } finally {
      setBusy(false);
    }
  }

  async function replay(event: FormEvent) {
    event.preventDefault();
    const id = messageId.trim();
    if (!id) {
      setError("需要消息 ID");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.replayShadow(id);
      setNotice(
        result.outbox
          ? "异常：重跑不应进入 outbox。"
          : `重跑 ${RUN_STATUS_LABELS[result.status] ?? result.status}，未进 outbox。`,
      );
      if (result.run_id) select({ tab: "runs", id: result.run_id });
      await reload(result.run_id ?? "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法重跑");
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageFrame>
      <PageTitle
        title="会话"
        description="检查私聊/群聊分层上下文，并查看影子推理。候选只有 shadow 状态，批准也不会发 QQ。"
        badge={
          <span
            className={`rounded-md px-4 py-2 text-sm backdrop-blur ${
              shadowEnabled ? "bg-violet-100/80 text-violet-800" : "bg-muted text-muted-foreground"
            }`}
          >
            {shadowEnabled ? "影子" : "关闭"}
          </span>
        }
      />
      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}
      {notice ? <p className="mb-4 text-sm text-primary">{notice}</p> : null}
      <div className="mb-5 flex gap-2">
        <button
          type="button"
          className={`rounded-md px-4 py-2 text-sm ${
            tab === "runs" ? "bg-primary text-primary-foreground" : "bg-background text-foreground"
          }`}
          onClick={() => select({ tab: "runs", id: "" })}
        >
          影子运行 {runs.length}
        </button>
        <button
          type="button"
          className={`rounded-md px-4 py-2 text-sm ${
            tab === "candidates" ? "bg-primary text-primary-foreground" : "bg-background text-foreground"
          }`}
          onClick={() => select({ tab: "candidates", id: "" })}
        >
          回复候选 {candidates.length}
        </button>
      </div>
      <PageSplit className="xl:grid-cols-[minmax(320px,.9fr)_minmax(0,1.1fr)]">
        <SurfaceCard fill>
          {tab === "runs" ? (
            runs.length === 0 ? (
              <EmptyState
                tone="inline"
                className="py-8"
                title="还没有影子运行。"
                detail="默认关闭，不会触网。"
              />
            ) : (
              <ul className="divide-y divide-black/5">
                {runs.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={`flex w-full items-center justify-between gap-3 rounded-2xl px-2 py-3 text-left transition hover:bg-muted/60 ${
                        selectedId === item.id ? "bg-muted/70" : ""
                      }`}
                      onClick={() => select({ tab: "runs", id: item.id })}
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-foreground">
                          {peerTitle(item.actor_qq, labels.user(item.actor_qq))}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {item.mode} · {item.model_route} · {item.source_message_id}
                        </p>
                      </div>
                      <span className="shrink-0 rounded-md bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                        {RUN_STATUS_LABELS[item.status] ?? item.status}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )
          ) : candidates.length === 0 ? (
              <EmptyState
                tone="inline"
                className="py-8"
                title="还没有影子候选。"
                detail="候选不会进入 outbox。"
              />
          ) : (
            <ul className="divide-y divide-black/5">
              {candidates.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`flex w-full items-center justify-between gap-3 rounded-2xl px-2 py-3 text-left transition hover:bg-muted/60 ${
                      selectedId === item.id ? "bg-muted/70" : ""
                    }`}
                    onClick={() => select({ tab: "candidates", id: item.id })}
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">{item.content}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {KIND_LABELS[item.conversation_kind] ?? item.conversation_kind} ·{" "}
                        {peerTitle(
                          item.target_id,
                          item.conversation_kind === "group"
                            ? labels.group(item.target_id)
                            : labels.user(item.target_id),
                        )}
                      </p>
                    </div>
                    <span className="shrink-0 rounded-md bg-violet-100 px-3 py-1 text-xs font-medium text-violet-800">
                      影子
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </SurfaceCard>
        <PageColumn>
          <SurfaceCard>
            {tab === "runs" && selectedRun ? (
              <div>
                <p className="text-xs font-medium tracking-wide text-muted-foreground">运行详情</p>
                <h2 className="mt-3 text-2xl font-semibold text-foreground">{selectedRun.actor_qq}</h2>
                <dl className="mt-4 space-y-2 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">状态</dt>
                    <dd className="font-medium text-foreground">
                      {RUN_STATUS_LABELS[selectedRun.status] ?? selectedRun.status}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">模式 / 路由</dt>
                    <dd className="font-medium text-foreground">
                      {selectedRun.mode} · {selectedRun.model_route}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">消息</dt>
                    <dd className="max-w-[60%] truncate font-medium text-foreground">
                      {selectedRun.source_message_id}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">prompt hash</dt>
                    <dd className="max-w-[60%] truncate font-medium text-foreground">
                      {selectedRun.prompt_hash}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">token</dt>
                    <dd className="font-medium text-foreground">
                      {selectedRun.input_tokens ?? "—"} / {selectedRun.output_tokens ?? "—"}
                    </dd>
                  </div>
                </dl>
                {selectedRun.error_message ? (
                  <p className="mt-4 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-700">
                    {selectedRun.error_type}: {selectedRun.error_message}
                  </p>
                ) : null}
                {detail?.candidate ? (
                  <div className="mt-4 rounded-2xl bg-muted/50 p-4">
                    <p className="text-xs text-muted-foreground">影子候选 · 不进 outbox</p>
                    <p className="mt-2 whitespace-pre-wrap text-sm text-foreground">
                      {detail.candidate.content}
                    </p>
                    {flagList(detail.candidate.safety_flags).length ? (
                      <p className="mt-2 text-xs text-muted-foreground">
                        标记：{flagList(detail.candidate.safety_flags).join(" · ")}
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <EmptyState
                    tone="inline"
                    className="mt-4 py-0"
                    title="这次运行没有候选，或尚未生成。"
                  />
                )}
              </div>
            ) : tab === "candidates" && selectedCandidate ? (
              <div>
                <p className="text-xs font-medium tracking-wide text-muted-foreground">候选详情</p>
                <h2 className="mt-3 text-2xl font-semibold text-foreground">
                  {KIND_LABELS[selectedCandidate.conversation_kind] ??
                    selectedCandidate.conversation_kind}
                </h2>
                <dl className="mt-4 space-y-2 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">对象</dt>
                    <dd className="font-medium text-foreground">{selectedCandidate.target_id}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">状态</dt>
                    <dd className="font-medium text-foreground">{selectedCandidate.status}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">消息</dt>
                    <dd className="max-w-[60%] truncate font-medium text-foreground">
                      {selectedCandidate.source_message_id}
                    </dd>
                  </div>
                </dl>
                <p className="mt-4 whitespace-pre-wrap rounded-2xl bg-muted/50 p-4 text-sm text-foreground">
                  {selectedCandidate.content}
                </p>
                <p className="mt-3 text-xs text-muted-foreground">
                  只有 shadow 状态。没有批准发送动作，这是刻意的安全限制。
                </p>
                <button
                  type="button"
                  className="mt-4 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground"
                  onClick={() => select({ tab: "runs", id: selectedCandidate.inference_run_id })}
                >
                  查看对应运行
                </button>
              </div>
            ) : (
              <EmptyState tone="select" title="从左侧选一条运行或候选，或先做上下文预览。" />
            )}
          </SurfaceCard>
          <SurfaceCard>
            <h2 className="text-lg font-semibold text-foreground">上下文检查器</h2>
            <p className="mt-2 text-xs text-muted-foreground">
              群聊应清空个人人格、用户理解和记忆。署名不可覆盖。
            </p>
            <form className="mt-4 space-y-3" onSubmit={runPreview}>
              <Select value={kind} onChange={(next) => setKind(next as "private" | "group")} options={[{ value: "private", label: "私聊" }, { value: "group", label: "群聊" }]} aria-label="检查会话类型" />
              <input
                value={peerId}
                onChange={(event) => setPeerId(event.target.value)}
                placeholder={kind === "group" ? "群号" : "对象 QQ"}
                className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <input
                value={actorQq}
                onChange={(event) => setActorQq(event.target.value)}
                placeholder="发送者 QQ"
                className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <button
                type="submit"
                disabled={busy}
                className="rounded-md bg-primary px-5 py-3 text-sm text-primary-foreground disabled:opacity-60"
              >
                预览
              </button>
            </form>
            {preview ? (
              <dl className="mt-5 space-y-2 text-sm">
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">署名</dt>
                  <dd className="font-medium text-foreground">
                    {preview.core_identity.brand} · {preview.core_identity.creator_name}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">主控认证</dt>
                  <dd className="font-medium text-foreground">
                    {preview.authenticated_creator ? "是（QQ 匹配）" : "否"}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">个人层</dt>
                  <dd className="font-medium text-foreground">
                    {preview.isolation.private_user_layers_allowed ? "允许（仅匹配私聊）" : "已清除"}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">全局定义</dt>
                  <dd className="max-w-[60%] text-right font-medium text-foreground">
                    {preview.persona.base_definition || "无"}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">单用户定义</dt>
                  <dd className="max-w-[60%] text-right font-medium text-foreground">
                    {preview.persona.private_definition || "无（群聊应为空）"}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">用户理解 / 记忆</dt>
                  <dd className="font-medium text-foreground">
                    {preview.user_reference ? "有理解" : "无理解"} · 记忆 {preview.memories.length}
                  </dd>
                </div>
              </dl>
            ) : null}
          </SurfaceCard>
          <SurfaceCard>
            <h2 className="text-lg font-semibold text-foreground">显式重跑</h2>
            <p className="mt-2 text-xs text-muted-foreground">
              对已入库消息重跑影子。开关关闭时会失败。新 run 不覆盖旧 run，绝不进 outbox。
            </p>
            <form className="mt-4 space-y-3" onSubmit={replay}>
              <input
                value={messageId}
                onChange={(event) => setMessageId(event.target.value)}
                placeholder="消息 ID"
                className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <button
                type="submit"
                disabled={busy}
                className="rounded-md bg-primary px-5 py-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                重跑
              </button>
            </form>
          </SurfaceCard>
        </PageColumn>
      </PageSplit>
    </PageFrame>
  );
}
