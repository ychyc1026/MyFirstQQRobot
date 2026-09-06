import { type FormEvent, useEffect, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { SurfaceCard } from "../components/SurfaceCard";
import { api, type MaterialItem } from "../lib/api";
import { CONTENT_CHAR_LIMIT } from "../lib/datetime";
import { MATERIAL_VERDICT_LABELS, statusLabel } from "../lib/labels";

function isEnabled(item: MaterialItem) {
  return Boolean(item.enabled);
}

export function MaterialsPage() {
  const [items, setItems] = useState<MaterialItem[]>([]);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [claim, setClaim] = useState("");
  const [targetQq, setTargetQq] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function reload() {
    setItems((await api.materials()).items);
  }

  useEffect(() => {
    reload().catch((err: Error) => setError(err.message));
  }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    const qq = targetQq.trim();
    if (qq && !/^\d+$/.test(qq)) {
      setError("QQ 只接受数字，不填则记到主号");
      return;
    }
    if (!title.trim() || !claim.trim() || !content.trim()) {
      setError("标题、说明和正文都要填");
      return;
    }
    if (content.length > CONTENT_CHAR_LIMIT) {
      setError(`材料正文最多 ${CONTENT_CHAR_LIMIT} 字`);
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await api.createMaterial({
        title,
        content,
        uploader_claim: claim,
        target_qq: qq,
      });
      setTitle("");
      setContent("");
      setClaim("");
      setTargetQq("");
      setNotice(
        created.ai_verdict === "match"
          ? "说明与材料相符，已允许主动提起。材料全站共用，随机抽一条，不是指定发给这个 QQ。"
          : `未通过核对，不会主动提起：${created.ai_reason || statusLabel(created.ai_verdict, MATERIAL_VERDICT_LABELS)}`,
      );
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法保存材料");
    } finally {
      setBusy(false);
    }
  }

  async function toggleEnabled(item: MaterialItem) {
    if (item.ai_verdict !== "match") {
      setError("只有核对相符的材料才能启用");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const next = !isEnabled(item);
      await api.setMaterialEnabled(item.id, next);
      setNotice(next ? "已允许主动提起这条材料。" : "已停止提起这条材料。");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法更新材料");
    } finally {
      setBusy(false);
    }
  }

  function importFile(file: File | null) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const text = typeof reader.result === "string" ? reader.result : "";
      setContent(text.slice(0, CONTENT_CHAR_LIMIT));
      if (!title) setTitle(file.name.replace(/\.[^.]+$/, ""));
      setNotice(`已导入 ${file.name}。标题和说明仍要填，只收 md/txt。`);
    };
    reader.readAsText(file);
  }

  const overLimit = content.length > CONTENT_CHAR_LIMIT;

  return (
    <PageFrame>
      <PageTitle
        title="材料"
        description="有材料才主动提起，没有就不说。标题和说明必填，模型会核对，不会盲从。核对相符后才能启用。联网搜索默认关闭。正文最多 2 万字。"
      />
      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}
      {notice ? <p className="mb-4 text-sm text-primary">{notice}</p> : null}
      <PageSplit className="gap-4 lg:grid-cols-2">
        <SurfaceCard fill>
          <form className="space-y-3" onSubmit={create}>
            <h2 className="text-lg font-semibold text-foreground">新增材料</h2>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              required
              placeholder="标题，必填，例如一本书名"
              className="w-full rounded-2xl bg-muted/50 px-4 py-3 text-sm"
            />
            <textarea
              value={claim}
              onChange={(event) => setClaim(event.target.value)}
              rows={3}
              required
              placeholder="这是什么？必填，模型会核对你的说明"
              className="w-full rounded-2xl bg-muted/50 px-4 py-3 text-sm"
            />
            <textarea
              value={content}
              onChange={(event) => setContent(event.target.value)}
              rows={8}
              required
              maxLength={CONTENT_CHAR_LIMIT}
              placeholder="材料正文，必填，最多 2 万字"
              className="w-full rounded-2xl bg-muted/50 px-4 py-3 text-sm"
            />
            <p className={`text-xs ${overLimit ? "text-red-600" : "text-muted-foreground"}`}>
              {content.length} / {CONTENT_CHAR_LIMIT}
            </p>
            <label className="block text-xs text-muted-foreground">
              导入 md 或 txt
              <input
                type="file"
                accept=".md,.txt,text/plain,text/markdown"
                onChange={(event) => importFile(event.target.files?.[0] ?? null)}
                className="mt-1 w-full rounded-2xl bg-muted/50 px-4 py-3 text-sm text-foreground"
              />
            </label>
            <input
              value={targetQq}
              onChange={(event) => setTargetQq(event.target.value)}
              inputMode="numeric"
              placeholder="可选：把核对消耗记到这个 QQ，只填数字"
              className="w-full rounded-2xl bg-muted/50 px-4 py-3 text-sm"
            />
            <p className="text-xs text-muted-foreground">不填记到主号。这不是「发给谁」，材料全站共用。</p>
            <button
              type="submit"
              disabled={busy || overLimit}
              className="rounded-md bg-primary px-5 py-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
            >
              上传并核对
            </button>
          </form>
        </SurfaceCard>
        <SurfaceCard fill>
          <h2 className="text-lg font-semibold text-foreground">材料库</h2>
          {items.length === 0 ? (
            <EmptyState tone="inline" className="mt-4" title="还没有材料。" />
          ) : (
            <ul className="mt-3 space-y-3">
              {items.map((item) => (
                <li key={item.id} className="rounded-2xl bg-muted/50 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <p className="font-semibold text-foreground">{item.title}</p>
                    <span className="text-xs text-muted-foreground">
                      {statusLabel(item.ai_verdict, MATERIAL_VERDICT_LABELS)}
                      {isEnabled(item) ? " · 会提起" : " · 不会提起"}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{item.uploader_claim}</p>
                  <p className="mt-2 line-clamp-3 text-sm text-foreground">{item.content}</p>
                  {item.ai_reason ? <p className="mt-2 text-xs text-muted-foreground">{item.ai_reason}</p> : null}
                  {item.ai_verdict === "match" ? (
                    <button
                      type="button"
                      disabled={busy}
                      className="mt-3 rounded-md bg-primary px-4 py-2 text-xs text-primary-foreground disabled:opacity-60"
                      onClick={() => toggleEnabled(item)}
                    >
                      {isEnabled(item) ? "停止提起" : "允许提起"}
                    </button>
                  ) : (
                    <p className="mt-3 text-xs text-muted-foreground">核对相符后才能启用。</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </SurfaceCard>
      </PageSplit>
    </PageFrame>
  );
}
