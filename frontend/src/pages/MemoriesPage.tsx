import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { DangerCard } from "../components/DangerCard";
import { DateTimePicker } from "../components/DateTimePicker";
import { EmptyState } from "../components/EmptyState";
import { PageColumn, PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { Select } from "../components/Select";
import { SurfaceCard } from "../components/SurfaceCard";
import {
  api,
  type KnownUser,
  type MemoryConflictItem,
  type MemoryContextItem,
  type MemoryRecordItem,
} from "../lib/api";
import { shanghaiLocalToIso } from "../lib/datetime";
import { peerTitle } from "../lib/peerLabels";

const KIND_LABELS: Record<string, string> = {
  fact: "事实",
  preference: "偏好",
  relationship_event: "关系事件",
  conversation_summary: "会话摘要",
};

const SOURCE_LABELS: Record<string, string> = {
  owner_manual: "主号人工",
  message_derived: "消息派生",
  document_derived: "资料派生",
  qzone_derived: "空间派生",
};

const STATUS_LABELS: Record<string, string> = {
  candidate: "候选",
  active: "在用",
  disputed: "冲突",
  superseded: "已替换",
  forgotten: "已忘记",
};

type MemoryKind = "fact" | "preference" | "relationship_event" | "conversation_summary";
type StatusFilter = "all" | "active" | "candidate" | "disputed" | "forgotten";

function statusClass(status: string) {
  if (status === "forgotten" || status === "superseded") {
    return "bg-black/5 text-muted-foreground";
  }
  if (status === "disputed" || status === "candidate") {
    return "bg-primary/10 text-primary";
  }
  return "bg-primary/10 text-primary";
}

function formatValue(value: Record<string, unknown>) {
  return JSON.stringify(value, null, 2);
}

function parseMemoryValue(raw: string): Record<string, unknown> {
  const trimmed = raw.trim();
  if (!trimmed) {
    throw new Error("内容不能为空");
  }
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    /* plain text */
  }
  return { text: trimmed };
}

export function MemoriesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedQq = searchParams.get("qq") ?? "";
  const selectedId = searchParams.get("id") ?? "";
  const selectedConflictId = searchParams.get("conflict") ?? "";
  const [users, setUsers] = useState<KnownUser[]>([]);
  const [items, setItems] = useState<MemoryRecordItem[]>([]);
  const [conflicts, setConflicts] = useState<MemoryConflictItem[]>([]);
  const [preview, setPreview] = useState<MemoryContextItem[] | null>(null);
  const [previewKind, setPreviewKind] = useState<"private" | "group">("private");
  const [lookup, setLookup] = useState(selectedQq);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [kind, setKind] = useState<MemoryKind>("fact");
  const [key, setKey] = useState("");
  const [valueText, setValueText] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function reload(qq = selectedQq) {
    const [listed, pending] = await Promise.all([
      api.users(),
      api.memoryConflicts(),
    ]);
    setUsers(listed.items);
    setConflicts(pending.items);
    if (!qq) {
      setItems([]);
      return;
    }
    const memories = await api.memories(qq);
    setItems(memories.items);
  }

  useEffect(() => {
    reload().catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    setLookup(selectedQq);
    setPreview(null);
    if (!selectedQq) {
      setItems([]);
      return;
    }
    api
      .memories(selectedQq)
      .then((body) => setItems(body.items))
      .catch((err: Error) => setError(err.message));
  }, [selectedQq]);

  const selected = useMemo(
    () => items.find((item) => item.id === selectedId) ?? null,
    [items, selectedId],
  );
  const selectedConflict = useMemo(
    () => conflicts.find((item) => item.id === selectedConflictId) ?? null,
    [conflicts, selectedConflictId],
  );
  const visibleItems = useMemo(
    () =>
      statusFilter === "all" ? items : items.filter((item) => item.status === statusFilter),
    [items, statusFilter],
  );

  function select(next: { qq?: string; id?: string; conflict?: string }) {
    const params = new URLSearchParams();
    const qq = next.qq ?? selectedQq;
    const id = next.id ?? "";
    const conflict = next.conflict ?? "";
    if (qq) params.set("qq", qq);
    if (id) params.set("id", id);
    if (conflict) params.set("conflict", conflict);
    setSearchParams(params, { replace: true });
  }

  function openLookup(event: FormEvent) {
    event.preventDefault();
    const qq = lookup.trim();
    if (!/^\d+$/.test(qq)) {
      setError("QQ 只接受数字");
      return;
    }
    setError("");
    setNotice("");
    select({ qq, id: "", conflict: "" });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const qq = selectedQq || lookup.trim();
    if (!/^\d+$/.test(qq)) {
      setError("先选择或输入用户 QQ");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const value = parseMemoryValue(valueText);
      const body: {
        kind: MemoryKind;
        key: string;
        value: Record<string, unknown>;
        expires_at?: string;
      } = { kind, key: key.trim(), value };
      if (expiresAt.trim()) {
        body.expires_at = shanghaiLocalToIso(expiresAt);
      }
      const saved = await api.createManualMemory(qq, body);
      if (saved.deduplicated) {
        setNotice("已存在相同键值，未重复写入。");
      } else if (saved.conflict_id) {
        setNotice("同键不同值已进入冲突，不会静默覆盖。");
        select({ qq, id: saved.id, conflict: saved.conflict_id });
      } else {
        setNotice(`已写入，状态 ${STATUS_LABELS[saved.status] ?? saved.status}。`);
        select({ qq, id: saved.id, conflict: "" });
      }
      setKey("");
      setValueText("");
      setExpiresAt("");
      await reload(qq);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法写入记忆");
    } finally {
      setBusy(false);
    }
  }

  async function forget() {
    if (!selectedQq || !selected) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.forgetMemory(selectedQq, selected.id);
      setNotice(result.forgotten ? "已标记忘记。这是可审计的逻辑状态，不是硬删除。" : "这条已经是忘记状态。");
      await reload(selectedQq);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法忘记");
    } finally {
      setBusy(false);
    }
  }

  async function resolve(resolution: "keep_left" | "keep_right" | "forget_both") {
    if (!selectedConflict) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.resolveMemoryConflict(selectedConflict.id, resolution);
      const labels = {
        keep_left: "已保留旧值",
        keep_right: "已使用新值",
        forget_both: "已将两边都忘记",
      };
      setNotice(labels[resolution]);
      select({ qq: selectedConflict.user_qq, id: "", conflict: "" });
      await reload(selectedConflict.user_qq);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法解决冲突");
    } finally {
      setBusy(false);
    }
  }

  async function runPreview(event: FormEvent) {
    event.preventDefault();
    const qq = selectedQq || lookup.trim();
    if (!/^\d+$/.test(qq)) {
      setError("预览需要用户 QQ");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const body = await api.memoryPreview(qq, previewKind);
      setPreview(body.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法预览");
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageFrame>
      <PageTitle
        title="记忆"
        description="非人工来源默认进候选，不直接影响回复。同键不同值进冲突，不静默覆盖。忘记是逻辑状态；群聊不会加载个人记忆。"
        badge={
          <span className="rounded-md bg-muted px-4 py-2 text-sm backdrop-blur">
            待解冲突 {conflicts.length}
          </span>
        }
      />
      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}
      {notice ? <p className="mb-4 text-sm text-primary">{notice}</p> : null}
      <PageSplit className="xl:grid-cols-[minmax(300px,.72fr)_minmax(0,1.28fr)]">
        <PageColumn>
          <SurfaceCard>
            <h2 className="text-lg font-semibold text-foreground">冲突待办</h2>
            {conflicts.length === 0 ? (
              <EmptyState tone="inline" className="mt-4" title="没有待解冲突。" />
            ) : (
              <ul className="mt-3 divide-y divide-black/5">
                {conflicts.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={`flex w-full items-center justify-between gap-3 rounded-2xl px-2 py-3 text-left transition hover:bg-muted/60 ${
                        selectedConflictId === item.id ? "bg-muted/70" : ""
                      }`}
                      onClick={() =>
                        select({ qq: item.user_qq, id: item.right_memory_id, conflict: item.id })
                      }
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-foreground">{item.memory_key}</p>
                        <p className="mt-1 text-xs text-muted-foreground">{item.user_qq}</p>
                      </div>
                      <span className="shrink-0 rounded-md bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                        待审批
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </SurfaceCard>
          <SurfaceCard>
            <h2 className="text-lg font-semibold text-foreground">按用户浏览</h2>
            <form className="mt-4 flex gap-2" onSubmit={openLookup}>
              <input
                value={lookup}
                onChange={(event) => setLookup(event.target.value)}
                placeholder="搜索备注或 QQ"
                className="flex-1 rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm outline-none"
              />
              <button type="submit" className="rounded-md bg-primary px-4 py-3 text-sm text-primary-foreground">
                查看
              </button>
            </form>
            {users.length ? (
              <ul className="mt-3 divide-y divide-black/5">
                {users.map((item) => (
                  <li key={item.user_qq}>
                    <button
                      type="button"
                      className={`flex w-full items-center justify-between gap-3 rounded-2xl px-2 py-3 text-left transition hover:bg-muted/60 ${
                        selectedQq === item.user_qq ? "bg-muted/70" : ""
                      }`}
                      onClick={() => select({ qq: item.user_qq, id: "", conflict: "" })}
                    >
                      <p className="truncate text-sm font-medium text-foreground">
                        {peerTitle(item.user_qq, item.operator_label)}
                      </p>
                      <span className="shrink-0 rounded-md bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                        {item.friend_state === "existing_friend"
                          ? "旧友"
                          : item.friend_state === "new_friend"
                            ? "新友"
                            : item.friend_state === "not_friend"
                              ? "非好友"
                              : "未知"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState
                tone="inline"
                className="mt-4 py-0 text-xs"
                title="还没有已知用户。"
                detail="直接输入 QQ 也可以查看记忆。"
              />
            )}
          </SurfaceCard>
          <SurfaceCard>
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-semibold text-foreground">记忆列表</h2>
              <Select size="pill" value={statusFilter} onChange={(next) => setStatusFilter(next as StatusFilter)} options={[{ value: "all", label: "全部" }, { value: "active", label: "在用" }, { value: "candidate", label: "候选" }, { value: "disputed", label: "冲突" }, { value: "forgotten", label: "已忘记" }]} aria-label="记忆状态" />
            </div>
            {!selectedQq ? (
              <EmptyState tone="inline" className="mt-4" title="先选择或输入用户 QQ。" />
            ) : visibleItems.length === 0 ? (
              <EmptyState tone="inline" className="mt-4" title="这个用户还没有符合筛选的记忆。" />
            ) : (
              <ul className="mt-3 divide-y divide-black/5">
                {visibleItems.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={`flex w-full items-center justify-between gap-3 rounded-2xl px-2 py-3 text-left transition hover:bg-muted/60 ${
                        selectedId === item.id ? "bg-muted/70" : ""
                      }`}
                      onClick={() => select({ qq: item.user_qq, id: item.id, conflict: "" })}
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-foreground">{item.memory_key}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {KIND_LABELS[item.memory_kind] ?? item.memory_kind} ·{" "}
                          {SOURCE_LABELS[item.source_type] ?? item.source_type}
                        </p>
                      </div>
                      <span
                        className={`shrink-0 rounded-md px-3 py-1 text-xs font-medium ${statusClass(item.status)}`}
                      >
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
          {selectedConflict ? (
            <SurfaceCard>
              <p className="text-xs font-medium tracking-wide text-muted-foreground">冲突详情</p>
              <h2 className="mt-3 text-2xl font-semibold text-foreground">{selectedConflict.memory_key}</h2>
              <p className="mt-2 text-sm text-muted-foreground">{selectedConflict.user_qq}</p>
              <div className="mt-4 grid grid-cols-2 gap-3">
                <div className="rounded-2xl bg-muted/50 p-4">
                  <p className="text-xs text-muted-foreground">旧值</p>
                  <pre className="mt-2 overflow-auto text-xs text-foreground">
                    {formatValue(selectedConflict.left_value)}
                  </pre>
                </div>
                <div className="rounded-2xl bg-muted/50 p-4">
                  <p className="text-xs text-muted-foreground">新值</p>
                  <pre className="mt-2 overflow-auto text-xs text-foreground">
                    {formatValue(selectedConflict.right_value)}
                  </pre>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy}
                  className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                  onClick={() => resolve("keep_left")}
                >
                  保留旧值
                </button>
                <button
                  type="button"
                  disabled={busy}
                  className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                  onClick={() => resolve("keep_right")}
                >
                  使用新值
                </button>
                <button
                  type="button"
                  disabled={busy}
                  className="rounded-md bg-muted px-4 py-2 text-sm text-foreground disabled:opacity-60"
                  onClick={() => resolve("forget_both")}
                >
                  两者都忘记
                </button>
              </div>
            </SurfaceCard>
          ) : null}
          <SurfaceCard>
            {selected ? (
              <div>
                <p className="text-xs font-medium tracking-wide text-muted-foreground">记忆详情</p>
                <h2 className="mt-3 text-2xl font-semibold text-foreground">{selected.memory_key}</h2>
                <dl className="mt-4 space-y-2 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">用户</dt>
                    <dd className="font-medium text-foreground">{selected.user_qq}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">类型</dt>
                    <dd className="font-medium text-foreground">
                      {KIND_LABELS[selected.memory_kind] ?? selected.memory_kind}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">来源</dt>
                    <dd className="font-medium text-foreground">
                      {SOURCE_LABELS[selected.source_type] ?? selected.source_type}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">状态</dt>
                    <dd className="font-medium text-foreground">
                      {STATUS_LABELS[selected.status] ?? selected.status}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">置信度</dt>
                    <dd className="font-medium text-foreground">{selected.confidence}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">过期</dt>
                    <dd className="font-medium text-foreground">{selected.expires_at ?? "无"}</dd>
                  </div>
                </dl>
                <pre className="mt-4 overflow-auto rounded-2xl bg-muted/50 p-4 text-xs text-foreground">
                  {formatValue(selected.value)}
                </pre>
                {selected.status !== "forgotten" ? (
                  <DangerCard className="mt-5">
                    <p className="text-sm font-medium text-red-800">忘记这条记忆</p>
                    <p className="mt-2 text-sm leading-6 text-red-700/90">
                      忘记只改逻辑状态，不会硬删除。隐私删除才是真正清掉数据。
                    </p>
                    <button
                      type="button"
                      disabled={busy}
                      className="mt-4 rounded-md bg-red-700 px-4 py-2 text-sm text-white disabled:opacity-60"
                      onClick={forget}
                    >
                      忘记
                    </button>
                  </DangerCard>
                ) : null}
              </div>
            ) : (
              <EmptyState tone="select" title="从左侧选一条记忆，或先解决冲突。" />
            )}
          </SurfaceCard>
          <SurfaceCard>
            <h2 className="text-lg font-semibold text-foreground">人工写入</h2>
            <p className="mt-2 text-xs text-muted-foreground">主号人工写入直接成为在用；同键不同值会进冲突。</p>
            <form className="mt-4 space-y-3" onSubmit={save}>
              <Select value={kind} onChange={(next) => setKind(next as MemoryKind)} options={[{ value: "fact", label: "事实" }, { value: "preference", label: "偏好" }, { value: "relationship_event", label: "关系事件" }, { value: "conversation_summary", label: "会话摘要" }]} aria-label="记忆类型" />
              <input
                value={key}
                onChange={(event) => setKey(event.target.value)}
                placeholder="键，最多 120 字"
                className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <textarea
                value={valueText}
                onChange={(event) => setValueText(event.target.value)}
                placeholder='内容。可写普通句子，或 JSON 对象如 {"name":"tea"}'
                rows={4}
                className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <DateTimePicker
                value={expiresAt}
                onChange={setExpiresAt}
                label="过期时间（可选）"
                placeholder="永久有效"
              />
              <button
                type="submit"
                disabled={busy}
                className="rounded-md bg-primary px-5 py-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                写入
              </button>
            </form>
          </SurfaceCard>
          <SurfaceCard>
            <h2 className="text-lg font-semibold text-foreground">检索预览</h2>
            <p className="mt-2 text-xs text-muted-foreground">只有匹配用户的私聊会看到在用记忆；群聊应为空。</p>
            <form className="mt-4 space-y-3" onSubmit={runPreview}>
              <Select value={previewKind} onChange={(next) => setPreviewKind(next as "private" | "group")} options={[{ value: "private", label: "私聊" }, { value: "group", label: "群聊" }]} aria-label="预览会话类型" />
              <button
                type="submit"
                disabled={busy}
                className="rounded-md bg-primary px-5 py-3 text-sm text-primary-foreground disabled:opacity-60"
              >
                预览
              </button>
            </form>
            {preview ? (
              preview.length === 0 ? (
                <p className="mt-5 text-sm text-muted-foreground">
                  {previewKind === "group" ? "群聊未加载个人记忆。" : "没有可检索的在用记忆。"}
                </p>
              ) : (
                <ul className="mt-5 space-y-2 text-sm">
                  {preview.map((item) => (
                    <li key={item.id} className="rounded-2xl bg-muted/50 px-4 py-3">
                      <p className="font-medium text-foreground">{item.key}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {KIND_LABELS[item.kind] ?? item.kind} ·{" "}
                        {SOURCE_LABELS[item.source] ?? item.source}
                      </p>
                    </li>
                  ))}
                </ul>
              )
            ) : null}
          </SurfaceCard>
        </PageColumn>
      </PageSplit>
    </PageFrame>
  );
}
