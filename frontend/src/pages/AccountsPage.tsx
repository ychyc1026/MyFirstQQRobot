import { type FormEvent, useEffect, useState } from "react";
import { PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { SurfaceCard } from "../components/SurfaceCard";
import { api, type BotAccount } from "../lib/api";

export function AccountsPage() {
  const [ownerQq, setOwnerQq] = useState("");
  const [ownerDraft, setOwnerDraft] = useState("");
  const [bots, setBots] = useState<BotAccount[]>([]);
  const [newQq, setNewQq] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [error, setError] = useState("");

  async function reload() {
    const snapshot = await api.accounts();
    setOwnerQq(snapshot.owner_qq);
    setOwnerDraft(snapshot.owner_qq);
    setBots(snapshot.bots);
  }

  useEffect(() => {
    reload().catch((err: Error) => setError(err.message));
  }, []);

  async function saveOwner(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await api.updateOwner(ownerDraft.trim());
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法改主号");
    }
  }

  async function addBot(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await api.addBot(newQq.trim(), newLabel.trim());
      setNewQq("");
      setNewLabel("");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法添加机器人");
    }
  }

  return (
    <PageFrame>
      <PageTitle
        title="账号"
        description="一份程序只有一个主号，可改。主号下可挂多个独立机器人；停用不是删数据。"
      />
      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}
      <PageSplit className="gap-4 lg:grid-cols-2">
        <SurfaceCard fill>
          <h2 className="text-lg font-semibold text-foreground">当前主号</h2>
          <form className="mt-4 flex flex-col gap-3 sm:flex-row" onSubmit={saveOwner}>
            <input
              value={ownerDraft}
              onChange={(event) => setOwnerDraft(event.target.value)}
              className="min-w-0 flex-1 rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm outline-none"
            />
            <button
              type="submit"
              className="rounded-md bg-primary px-5 py-3 text-sm font-medium text-primary-foreground"
            >
              保存
            </button>
          </form>
          <p className="mt-3 text-xs text-muted-foreground">当前生效：{ownerQq || "未播种"}</p>
        </SurfaceCard>
        <SurfaceCard fill>
          <h2 className="text-lg font-semibold text-foreground">机器人</h2>
          <form className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 2xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]" onSubmit={addBot}>
            <input
              value={newQq}
              onChange={(event) => setNewQq(event.target.value)}
              placeholder="机器人 QQ"
              className="min-w-0 rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm outline-none"
            />
            <input
              value={newLabel}
              onChange={(event) => setNewLabel(event.target.value)}
              placeholder="备注"
              className="min-w-0 rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm outline-none"
            />
            <button type="submit" className="rounded-md bg-primary px-5 py-3 text-sm text-primary-foreground sm:col-span-2 2xl:col-span-1">
              添加
            </button>
          </form>
          <ul className="mt-5 divide-y divide-black/5">
            {bots.map((bot) => (
              <li key={bot.qq} className="flex items-center justify-between gap-3 py-3">
                <div>
                  <p className="font-medium text-foreground">
                    {bot.qq} {bot.label ? `· ${bot.label}` : ""}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    用户默认 {bot.quota_user_default} · 群默认 {bot.quota_group_default}
                  </p>
                </div>
                <button
                  type="button"
                  className="rounded-md bg-muted px-3 py-1 text-xs"
                  onClick={() =>
                    api.setBotEnabled(bot.qq, !bot.enabled).then(reload).catch((err: Error) => setError(err.message))
                  }
                >
                  {bot.enabled ? "停用" : "启用"}
                </button>
              </li>
            ))}
          </ul>
        </SurfaceCard>
      </PageSplit>
    </PageFrame>
  );
}
