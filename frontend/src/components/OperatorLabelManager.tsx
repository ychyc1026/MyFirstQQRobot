import { type FormEvent, useState } from "react";
import { api } from "../lib/api";
import { useOperatorLabels } from "../lib/useOperatorLabels";
import { EmptyState } from "./EmptyState";
import { SegmentSlider } from "./SegmentSlider";
import { SurfaceCard } from "./SurfaceCard";

const KIND_LABELS: Record<"user" | "group", string> = {
  user: "用户",
  group: "群",
};

export function OperatorLabelManager() {
  const labels = useOperatorLabels();
  const [kind, setKind] = useState<"user" | "group">("user");
  const [subjectId, setSubjectId] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function save(nextKind: "user" | "group", id: string, text: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.setOperatorLabel(nextKind, id, text);
      await labels.reload();
      setNotice(
        text
          ? `已把 ${KIND_LABELS[nextKind]} ${id} 备注为 ${text}`
          : `已清除 ${KIND_LABELS[nextKind]} ${id} 的备注`,
      );
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存备注失败");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const id = subjectId.trim();
    const text = label.trim();
    if (!/^\d+$/.test(id)) {
      setError(kind === "group" ? "群号只接受数字" : "QQ 只接受数字");
      return;
    }
    if (!text) {
      setError("备注名不能为空。要去掉备注请用右侧的清除。");
      return;
    }
    if (await save(kind, id, text)) {
      setSubjectId("");
      setLabel("");
    }
  }

  return (
    <SurfaceCard>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-foreground">备注</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            给 QQ 和群号起你认得的名字。只给你看，不进模型，也不改变谁会被回复。
          </p>
        </div>
        <p className="text-xs text-muted-foreground">已备注 {labels.items.length} 个</p>
      </div>
      <form className="mt-4 flex flex-wrap items-center gap-2" onSubmit={submit}>
        <SegmentSlider
          value={kind}
          onChange={setKind}
          options={[
            { value: "user", label: "用户" },
            { value: "group", label: "群" },
          ]}
        />
        <input
          value={subjectId}
          onChange={(event) => setSubjectId(event.target.value)}
          placeholder={kind === "group" ? "群号" : "用户 QQ"}
          className="min-w-0 flex-1 basis-32 rounded-2xl border border-border bg-muted/50 px-4 py-2 text-sm outline-none"
        />
        <input
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          placeholder="备注名，例如 小明"
          maxLength={32}
          className="min-w-0 flex-1 basis-40 rounded-2xl border border-border bg-muted/50 px-4 py-2 text-sm outline-none"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
        >
          保存
        </button>
      </form>
      {error ? <p className="mt-3 text-sm text-destructive">{error}</p> : null}
      {notice ? <p className="mt-3 text-sm text-muted-foreground">{notice}</p> : null}
      {labels.items.length === 0 ? (
        <EmptyState
          tone="inline"
          className="mt-4 py-0"
          title="还没有备注。"
          detail="列表里现在只会显示 QQ 号。"
        />
      ) : (
        <ul className="mt-4 divide-y divide-black/5 text-sm">
          {labels.items.map((item) => (
            <li
              key={`${item.subject_kind}-${item.subject_id}`}
              className="flex items-center justify-between gap-3 py-3"
            >
              <div className="min-w-0">
                <p className="truncate font-medium text-foreground">{item.label}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {KIND_LABELS[item.subject_kind]} {item.subject_id}
                </p>
              </div>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  void save(item.subject_kind, item.subject_id, "");
                }}
                className="shrink-0 rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground disabled:opacity-60"
              >
                清除
              </button>
            </li>
          ))}
        </ul>
      )}
    </SurfaceCard>
  );
}
