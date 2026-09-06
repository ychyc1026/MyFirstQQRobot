import { type FormEvent, useEffect, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { PageColumn, PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { Select } from "../components/Select";
import { SurfaceCard } from "../components/SurfaceCard";
import { api, type BotAccount, type QuotaOverride } from "../lib/api";

export function QuotasPage() {
  const [bots, setBots] = useState<BotAccount[]>([]);
  const [botQq, setBotQq] = useState("");
  const [kind, setKind] = useState<"private" | "group">("private");
  const [items, setItems] = useState<QuotaOverride[]>([]);
  const [peerId, setPeerId] = useState("");
  const [dailyLimit, setDailyLimit] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [bonus, setBonus] = useState("");
  const [replyDraft, setReplyDraft] = useState("");
  const [error, setError] = useState("");

  async function reload(nextBot = botQq, nextKind = kind) {
    if (!nextBot) return;
    const [snapshot, quotas] = await Promise.all([
      api.accounts(),
      api.quotas(nextBot, nextKind),
    ]);
    setBots(snapshot.bots);
    setItems(quotas.items);
    const current = snapshot.bots.find((bot) => bot.qq === nextBot);
    if (current) setReplyDraft(current.quota_user_reply);
  }

  useEffect(() => {
    api
      .accounts()
      .then((snapshot) => {
        setBots(snapshot.bots);
        const first = snapshot.bots[0]?.qq ?? "";
        setBotQq(first);
        return reload(first, "private");
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  async function saveLimit(event: FormEvent) {
    event.preventDefault();
    if (!botQq || !peerId || !dailyLimit) return;
    setError("");
    try {
      await api.setQuotaLimit(botQq, kind, peerId.trim(), {
        daily_limit: Number(dailyLimit),
        display_name: displayName.trim() || undefined,
      });
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法保存限额");
    }
  }

  async function saveBonus(event: FormEvent) {
    event.preventDefault();
    if (!botQq || !peerId || !bonus) return;
    setError("");
    try {
      await api.addTodayBonus(botQq, kind, peerId.trim(), Number(bonus));
      setBonus("");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法追加今天额度");
    }
  }

  async function saveReply(event: FormEvent) {
    event.preventDefault();
    if (!botQq) return;
    setError("");
    try {
      await api.updateBot(botQq, { quota_user_reply: replyDraft });
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法保存默认回复");
    }
  }

  return (
    <PageFrame>
      <PageTitle
        title="额度"
        description="私聊默认每天 50 万 token，群默认 200 万。用户超限会通知主号并回复默认文案；群超限静默停止。"
      />
      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}
      <PageSplit className="gap-4 lg:grid-cols-2">
        <PageColumn className="gap-4">
          <SurfaceCard>
            <div className="flex gap-3">
              <Select
                value={botQq}
                onChange={(value) => {
                  setBotQq(value);
                  reload(value, kind).catch((err: Error) => setError(err.message));
                }}
                options={bots.map((bot) => ({
                  value: bot.qq,
                  label: `${bot.qq} ${bot.label}`.trim(),
                }))}
              />
              <Select
                value={kind}
                onChange={(value) => {
                  const next = value as "private" | "group";
                  setKind(next);
                  reload(botQq, next).catch((err: Error) => setError(err.message));
                }}
                options={[
                  { value: "private", label: "私聊用户" },
                  { value: "group", label: "群" },
                ]}
              />
            </div>
          </SurfaceCard>
          {kind === "private" ? (
            <SurfaceCard>
              <h2 className="text-lg font-semibold text-foreground">超限默认回复</h2>
              <form className="mt-4 space-y-3" onSubmit={saveReply}>
                <textarea
                  value={replyDraft}
                  onChange={(event) => setReplyDraft(event.target.value)}
                  rows={3}
                  className="w-full rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm outline-none"
                />
                <button type="submit" className="rounded-md bg-primary px-5 py-2 text-sm text-primary-foreground">
                  保存回复
                </button>
              </form>
            </SurfaceCard>
          ) : null}
          <SurfaceCard>
            <h2 className="text-lg font-semibold text-foreground">设置限额 / 今天追加</h2>
            <form className="mt-4 grid grid-cols-2 gap-3" onSubmit={saveLimit}>
              <input
                value={peerId}
                onChange={(event) => setPeerId(event.target.value)}
                placeholder={kind === "group" ? "群号" : "用户 QQ"}
                className="rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <input
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="备注名（可选）"
                className="rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <input
                value={dailyLimit}
                onChange={(event) => setDailyLimit(event.target.value)}
                placeholder="日限额 token"
                className="rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <button type="submit" className="rounded-md bg-primary px-5 py-3 text-sm text-primary-foreground">
                保存日限额
              </button>
            </form>
            <form className="mt-3 flex gap-3" onSubmit={saveBonus}>
              <input
                value={bonus}
                onChange={(event) => setBonus(event.target.value)}
                placeholder="今天再加多少 token"
                className="flex-1 rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm"
              />
              <button type="submit" className="rounded-md bg-primary px-5 py-3 text-sm text-primary-foreground">
                追加今天额度
              </button>
            </form>
          </SurfaceCard>
        </PageColumn>
        <SurfaceCard fill>
          <h2 className="text-lg font-semibold text-foreground">已设置的覆盖</h2>
          {items.length === 0 ? (
            <EmptyState
              tone="inline"
              className="mt-3 py-0"
              title="还没有单独覆盖，走机器人默认额度。"
            />
          ) : (
            <ul className="mt-4 divide-y divide-black/5 text-sm">
              {items.map((item) => (
                <li key={`${item.peer_kind}-${item.peer_id}`} className="py-3">
                  <p className="font-medium text-foreground">
                    {item.peer_id}
                    {item.display_name ? ` · ${item.display_name}` : ""}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    日限额 {item.daily_limit} · 今天追加 {item.bonus_day ? item.today_bonus : 0}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </SurfaceCard>
      </PageSplit>
    </PageFrame>
  );
}
