import { type FormEvent, useEffect, useState } from "react";
import { DatePicker } from "../components/DatePicker";
import { EmptyState } from "../components/EmptyState";
import { PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { SurfaceCard } from "../components/SurfaceCard";
import { api, type DiaryEntry } from "../lib/api";
import { CONTENT_CHAR_LIMIT, shanghaiDayKey } from "../lib/datetime";
import { DIARY_STATUS_LABELS, statusLabel } from "../lib/labels";

export function DiaryPage() {
  const today = shanghaiDayKey();
  const [dayKey, setDayKey] = useState(today);
  const [content, setContent] = useState("");
  const [items, setItems] = useState<DiaryEntry[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function reload(nextDay = dayKey) {
    const [list, current] = await Promise.all([api.diaryEntries(), api.diaryEntry(nextDay)]);
    setItems(list.items);
    setContent(current.content ?? "");
    setStatus(current.status ?? "pending");
  }

  useEffect(() => {
    reload().catch((err: Error) => setError(err.message));
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (content.length > CONTENT_CHAR_LIMIT) {
      setError(`日记最多 ${CONTENT_CHAR_LIMIT} 字`);
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await api.saveDiary(dayKey, content);
      setStatus(saved.status ?? "pending");
      if (dayKey < today) {
        setNotice("日记已保存。过去的日期不会自动补发。");
      } else if (dayKey > today) {
        setNotice("日记已保存。到了那天、用户勾选发送日记、调度开着才会发。出站关着只入队。安静时段仍会推迟。");
      } else {
        setNotice(
          "日记已保存。当天非空、用户勾选发送日记、调度开着才会发。出站关着只入队不发 QQ。安静时段仍会推迟。每人每天一条，不受日限额限制。",
        );
      }
      await reload(dayKey);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法保存日记");
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
      setNotice(`已导入 ${file.name}，确认后保存。只收 md/txt，最多 ${CONTENT_CHAR_LIMIT} 字。`);
    };
    reader.readAsText(file);
  }

  const heading = dayKey === today ? "今天写" : dayKey > today ? `${dayKey}（还没到）` : dayKey;
  const overLimit = content.length > CONTENT_CHAR_LIMIT;

  return (
    <PageFrame>
      <PageTitle
        title="YCH 的日记"
        description="按上海自然日计算。日记为空不发；过去的日期保存后不会补发；跨天仍待发会标过期。正文最多 2 万字。"
      />
      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}
      {notice ? <p className="mb-4 text-sm text-primary">{notice}</p> : null}
      <PageSplit className="gap-4 lg:grid-cols-2">
        <SurfaceCard fill>
          <form className="space-y-3" onSubmit={save}>
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-semibold text-foreground">{heading}</h2>
              <span className="text-xs text-muted-foreground">{statusLabel(status, DIARY_STATUS_LABELS, "待发")}</span>
            </div>
            <DatePicker
              variant="compact"
              label="上海自然日"
              value={dayKey}
              onChange={(next) => {
                if (!next) return;
                setDayKey(next);
                reload(next).catch((err: Error) => setError(err.message));
              }}
            />
            {dayKey < today ? (
              <p className="text-xs text-muted-foreground">这一天已经过去，保存后不会自动补发给用户。</p>
            ) : null}
            {dayKey > today ? (
              <p className="text-xs text-muted-foreground">还没到这一天。到了当天才会按发送日记策略处理。</p>
            ) : null}
            <textarea
              value={content}
              onChange={(event) => setContent(event.target.value)}
              rows={14}
              maxLength={CONTENT_CHAR_LIMIT}
              className="w-full rounded-2xl bg-muted/50 px-4 py-3 text-sm"
              placeholder="支持 Markdown / 纯文本，最多 2 万字"
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
            <button
              type="submit"
              disabled={busy || overLimit}
              className="rounded-md bg-primary px-5 py-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
            >
              保存日记
            </button>
          </form>
        </SurfaceCard>
        <SurfaceCard fill>
          <h2 className="text-lg font-semibold text-foreground">历史</h2>
          {items.length === 0 ? (
            <EmptyState tone="inline" className="mt-4" title="还没有日记。" />
          ) : (
            <ul className="mt-3 divide-y divide-black/5">
              {items.map((item) => (
                <li key={item.day_key}>
                  <button
                    type="button"
                    className="flex w-full items-center justify-between px-1 py-3 text-left"
                    onClick={() => {
                      setDayKey(item.day_key);
                      setContent(item.content);
                      setStatus(item.status ?? "");
                    }}
                  >
                    <span className="text-sm font-semibold text-foreground">{item.day_key}</span>
                    <span className="text-xs text-muted-foreground">
                      {statusLabel(item.status, DIARY_STATUS_LABELS)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </SurfaceCard>
      </PageSplit>
    </PageFrame>
  );
}
