import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { PageColumn, PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { Select } from "../components/Select";
import { SurfaceCard } from "../components/SurfaceCard";
import { api, type PersonaPreview, type PersonaProfileItem } from "../lib/api";

const SCOPE_LABELS: Record<string, string> = {
  global: "全局",
  private_user: "单用户",
};

const SOURCE_LABELS: Record<string, string> = {
  manual: "人工",
  document_derived: "资料派生",
};

export function PersonasPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("id") ?? "";
  const [items, setItems] = useState<PersonaProfileItem[]>([]);
  const [preview, setPreview] = useState<PersonaPreview | null>(null);
  const [scope, setScope] = useState<"global" | "private_user">("global");
  const [userQq, setUserQq] = useState("");
  const [name, setName] = useState("");
  const [definition, setDefinition] = useState("");
  const [previewKind, setPreviewKind] = useState<"private" | "group">("private");
  const [previewPeer, setPreviewPeer] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function reload() {
    const listed = await api.personas();
    setItems(listed.items);
  }

  useEffect(() => {
    reload().catch((err: Error) => setError(err.message));
  }, []);

  const selected = useMemo(
    () => items.find((item) => item.id === selectedId) ?? null,
    [items, selectedId],
  );

  function select(id: string) {
    const next = new URLSearchParams();
    if (id) next.set("id", id);
    setSearchParams(next, { replace: true });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await api.savePersona({
        scope,
        user_qq: scope === "private_user" ? userQq.trim() : undefined,
        name: name.trim(),
        definition: definition.trim(),
      });
      setNotice(`已保存 v${saved.version}，旧人工定义会被覆盖为 superseded。`);
      setName("");
      setDefinition("");
      await reload();
      select(saved.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法保存人格");
    } finally {
      setBusy(false);
    }
  }

  async function runPreview(event: FormEvent) {
    event.preventDefault();
    const peer = previewPeer.trim();
    if (!/^\d+$/.test(peer)) {
      setError("预览对象 QQ/群号只接受数字");
      return;
    }
    setBusy(true);
    setError("");
    try {
      setPreview(await api.personaPreview(previewKind, peer));
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法预览");
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageFrame>
      <PageTitle
        title="人格"
        description="全局人工定义进所有对话；单用户人格和资料派生只进该用户私聊。群聊不会加载个人层。署名不可覆盖。"
        badge={
          <span className="ych-pill">
            在用 {items.length} 条
          </span>
        }
      />
      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}
      {notice ? <p className="mb-4 text-sm text-primary">{notice}</p> : null}
      <PageSplit className="gap-4 lg:grid-cols-[minmax(0,1.08fr)_minmax(0,.92fr)]">
        <PageColumn className={`gap-4 ${items.length === 0 ? "lg:col-span-2" : ""}`}>
          {items.length > 0 ? <SurfaceCard className="min-h-[260px]">
            <h2 className="text-lg font-semibold text-foreground">在用人格</h2>
            <ul className="mt-3 divide-y divide-black/5">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`flex w-full items-center justify-between gap-3 rounded-2xl px-2 py-3 text-left transition hover:bg-muted/60 ${
                      selectedId === item.id ? "bg-muted/70" : ""
                    }`}
                    onClick={() => select(item.id)}
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">{item.name}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {SCOPE_LABELS[item.scope_type] ?? item.scope_type}
                        {item.scope_id !== "*" ? ` · ${item.scope_id}` : ""} · v{item.version}
                      </p>
                    </div>
                    <span className="shrink-0 rounded-md bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                      {SOURCE_LABELS[item.source_type] ?? item.source_type}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </SurfaceCard> : null}
          <SurfaceCard className="flex-1">
            <h2 className="text-lg font-semibold text-foreground">人工简洁定义</h2>
            {items.length === 0 ? (
              <EmptyState
                tone="inline"
                className="mt-2 py-0"
                title="还没有人格。"
                detail="先从一条全局简洁定义开始，保存后再进行上下文预览。"
              />
            ) : null}
            <form className="mt-4 space-y-3" onSubmit={save}>
              <Select
                value={scope}
                onChange={(next) => setScope(next as "global" | "private_user")}
                options={[
                  { value: "global", label: "全局" },
                  { value: "private_user", label: "单用户私聊" },
                ]}
              />
              {scope === "private_user" ? (
                <input
                  value={userQq}
                  onChange={(event) => setUserQq(event.target.value)}
                  placeholder="用户 QQ"
                  className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
                />
              ) : null}
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="名称，最多 80 字"
                className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <textarea
                value={definition}
                onChange={(event) => setDefinition(event.target.value)}
                placeholder="简洁定义，最多 1000 字。这是开发者指令，不是用户事实。"
                rows={4}
                className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <button
                type="submit"
                disabled={busy}
                className="rounded-md bg-primary px-5 py-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                保存
              </button>
            </form>
          </SurfaceCard>
        </PageColumn>
        {items.length > 0 ? <PageColumn className="gap-4">
          <SurfaceCard className="min-h-[260px]">
            {selected ? (
              <div>
                <p className="text-xs font-medium tracking-wide text-muted-foreground">人格详情</p>
                <h2 className="mt-3 text-2xl font-semibold text-foreground">{selected.name}</h2>
                <dl className="mt-4 space-y-2 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">范围</dt>
                    <dd className="font-medium text-foreground">
                      {SCOPE_LABELS[selected.scope_type]} · {selected.scope_id}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">来源</dt>
                    <dd className="font-medium text-foreground">
                      {SOURCE_LABELS[selected.source_type]} · v{selected.version}
                    </dd>
                  </div>
                </dl>
                <p className="mt-4 whitespace-pre-wrap text-sm leading-6 text-foreground">
                  {selected.developer_definition || "（资料派生，无人工定义）"}
                </p>
                {selected.traits && Object.keys(selected.traits).length ? (
                  <pre className="mt-4 overflow-auto rounded-2xl bg-muted/50 p-4 text-xs text-foreground">
                    {JSON.stringify(selected.traits, null, 2)}
                  </pre>
                ) : null}
              </div>
            ) : (
              <EmptyState tone="select" title="从左侧选一条人格。" />
            )}
          </SurfaceCard>
          <SurfaceCard className="flex-1">
            <h2 className="text-lg font-semibold text-foreground">上下文预览</h2>
            <p className="mt-2 text-xs text-muted-foreground">群聊预览不应出现单用户定义或用户理解。</p>
            <form className="mt-4 space-y-3" onSubmit={runPreview}>
              <Select
                value={previewKind}
                onChange={(next) => setPreviewKind(next as "private" | "group")}
                options={[
                  { value: "private", label: "私聊" },
                  { value: "group", label: "群聊" },
                ]}
              />
              <input
                value={previewPeer}
                onChange={(event) => setPreviewPeer(event.target.value)}
                placeholder={previewKind === "group" ? "群号" : "用户 QQ"}
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
                  <dt className="text-muted-foreground">全局定义</dt>
                  <dd className="max-w-[60%] text-right font-medium text-foreground">
                    {preview.base_definition || "无"}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">单用户定义</dt>
                  <dd className="max-w-[60%] text-right font-medium text-foreground">
                    {preview.private_definition || "无（群聊应为空）"}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">用户理解</dt>
                  <dd className="font-medium text-foreground">
                    {preview.user_context ? "有（仅私聊）" : "无"}
                  </dd>
                </div>
              </dl>
            ) : null}
          </SurfaceCard>
        </PageColumn> : null}
      </PageSplit>
    </PageFrame>
  );
}
