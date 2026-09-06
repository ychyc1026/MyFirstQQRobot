import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { DatePicker } from "../components/DatePicker";
import { EmptyState } from "../components/EmptyState";
import { PageFrame } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { SegmentSlider } from "../components/SegmentSlider";
import { StatBlock } from "../components/StatBlock";
import { SurfaceCard } from "../components/SurfaceCard";
import { api, type DailySummary, type StatsOverview } from "../lib/api";
import { CHART_CURSOR, CHART_TOOLTIP_ITEM_STYLE, CHART_TOOLTIP_LABEL_STYLE, CHART_TOOLTIP_STYLE } from "../lib/chartTheme";
import { shanghaiDayKey } from "../lib/datetime";
import { peerTitle } from "../lib/peerLabels";
import { useOperatorLabels } from "../lib/useOperatorLabels";

const RANGES = [
  { value: "today" as const, label: "今日" },
  { value: "7d" as const, label: "7 日" },
  { value: "30d" as const, label: "30 日" },
];

function todayKey() {
  return shanghaiDayKey();
}

export function StatsPage() {
  const labels = useOperatorLabels();
  const [range, setRange] = useState<"today" | "7d" | "30d">("today");
  const [overview, setOverview] = useState<StatsOverview | null>(null);
  const [summary, setSummary] = useState<DailySummary | null>(null);
  const [day, setDay] = useState(todayKey);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function loadOverview(nextRange = range) {
    setOverview(await api.statsOverview(nextRange));
  }

  async function loadSummary(nextDay = day) {
    setSummary(await api.statsSummary(nextDay));
  }

  useEffect(() => {
    Promise.all([loadOverview(), loadSummary()]).catch((err: Error) => setError(err.message));
  }, []);

  const pieData = useMemo(() => {
    const users = overview?.tokens.users.reduce((sum, item) => sum + item.tokens, 0) ?? 0;
    const groups = overview?.tokens.groups.reduce((sum, item) => sum + item.tokens, 0) ?? 0;
    return [
      { name: "用户", value: users },
      { name: "群", value: groups },
    ].filter((item) => item.value > 0);
  }, [overview]);

  return (
    <PageFrame>
      <PageTitle
        title="统计"
        description="按上海自然日汇总私聊人数、群聊、活跃会话、图片和 token。摘要按日缓存，不会实时监听聊天。消耗记在主号名下。"
        badge={
          <SegmentSlider
            value={range}
            options={RANGES}
            onChange={(next) => {
              setRange(next);
              loadOverview(next).catch((err: Error) => setError(err.message));
            }}
          />
        }
      />
      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}

      <div className="grid flex-1 gap-4">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatBlock label="私聊人数" value={overview?.private_users ?? 0} hint="只算私聊，不含群" />
          <StatBlock label="群聊数" value={overview?.groups ?? 0} />
          <StatBlock
            label="活跃会话"
            value={overview?.rounds ?? 0}
            hint="有过消息的私聊或群，各算一场"
          />
          <StatBlock label="Token" value={overview?.tokens.total_tokens ?? 0} />
        </div>

        <div className="grid auto-rows-fr items-stretch gap-4 xl:grid-cols-3">
          <SurfaceCard fill className="xl:col-span-2">
            <h2 className="text-lg font-semibold text-foreground">消息趋势</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              用户 {overview?.user_messages ?? 0} 句 / {overview?.user_images ?? 0} 图 · 机器人{" "}
              {overview?.bot_messages ?? 0} 句 / {overview?.bot_images ?? 0} 图
            </p>
            <div className="mt-4 min-h-[256px] flex-1">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={overview?.trend ?? []}>
                  <XAxis
                    dataKey="day"
                    tickFormatter={(value: string) => value.slice(5)}
                    tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    allowDecimals={false}
                    tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <Tooltip cursor={CHART_CURSOR} contentStyle={CHART_TOOLTIP_STYLE} labelStyle={CHART_TOOLTIP_LABEL_STYLE} itemStyle={CHART_TOOLTIP_ITEM_STYLE} />
                  <Bar dataKey="inbound" fill="hsl(var(--chart-1))" radius={[6, 6, 3, 3]} name="用户" />
                  <Bar dataKey="outbound" fill="hsl(var(--chart-2))" radius={[6, 6, 3, 3]} name="机器人" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </SurfaceCard>

          <SurfaceCard fill className="xl:row-span-2">
            <div className="flex items-start justify-between gap-3">
              <h2 className="shrink-0 whitespace-nowrap text-lg font-semibold text-foreground">日摘要</h2>
              <DatePicker
                label="上海自然日"
                value={day}
                onChange={(next) => {
                  if (!next) return;
                  setDay(next);
                  loadSummary(next).catch((err: Error) => setError(err.message));
                }}
              />
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              上方今日/7日/30日只改左侧图表。右侧摘要按你选的上海自然日，互不影响。没有摘要时点生成摘要；已有摘要时会变成重新生成。最多 20 条。消耗记在主号名下。模型关着会出规则摘要。
            </p>
            <button
              type="button"
              disabled={busy}
              className="mt-3 rounded-md bg-primary px-4 py-2 text-xs font-bold text-primary-foreground disabled:opacity-60"
              onClick={() => {
                const force = Boolean(summary?.generated_at);
                setBusy(true);
                api
                  .generateStatsSummary(day, force)
                  .then((body) => setSummary(body))
                  .catch((err: Error) => setError(err.message))
                  .finally(() => setBusy(false));
              }}
            >
              {summary?.generated_at ? "重新生成" : "生成摘要"}
            </button>
            <ol className="mt-4 flex-1 space-y-4 overflow-auto border-l border-border/10 pl-4">
              {(summary?.entries ?? []).length === 0 ? (
                <li>
                  <EmptyState
                    tone="inline"
                    className="py-0"
                    title="这一天还没有摘要。"
                    detail="生成后最多保留 20 条。"
                  />
                </li>
              ) : (
                (summary?.entries ?? []).map((entry, index) => (
                  <li key={`${entry.time}-${index}`}>
                    <p className="text-xs font-bold text-primary">{entry.time || "--:--"}</p>
                    <p className="mt-1 text-sm text-foreground">{entry.text || entry.alias}</p>
                  </li>
                ))
              )}
            </ol>
          </SurfaceCard>

          <SurfaceCard fill>
            <h2 className="text-lg font-semibold text-foreground">Token 分布</h2>
            <div className="mt-4 min-h-[224px] flex-1">
              {pieData.length === 0 ? (
                <EmptyState
                  tone="inline"
                  className="flex h-full items-center justify-center py-0"
                  title="还没有消耗"
                />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={pieData} dataKey="value" nameKey="name" innerRadius={48} outerRadius={72} paddingAngle={4}>
                      {pieData.map((item) => (
                        <Cell key={item.name} fill={item.name === "用户" ? "hsl(var(--chart-1))" : "hsl(var(--chart-2))"} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={CHART_TOOLTIP_STYLE} labelStyle={CHART_TOOLTIP_LABEL_STYLE} itemStyle={CHART_TOOLTIP_ITEM_STYLE} />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </div>
          </SurfaceCard>

          <SurfaceCard fill>
            <h2 className="text-lg font-semibold text-foreground">消耗排行</h2>
            <ul className="mt-4 flex-1 space-y-2 text-sm">
              {(overview?.tokens.users ?? []).slice(0, 6).map((item) => (
                <li key={`u-${item.peer_id}`} className="flex justify-between gap-3">
                  <span className="text-muted-foreground">
                    用户 {peerTitle(item.peer_id, labels.user(item.peer_id))}
                  </span>
                  <span className="font-semibold text-foreground">{item.tokens}</span>
                </li>
              ))}
              {(overview?.tokens.groups ?? []).slice(0, 4).map((item) => (
                <li key={`g-${item.peer_id}`} className="flex justify-between gap-3">
                  <span className="text-muted-foreground">
                    群 {peerTitle(item.peer_id, labels.group(item.peer_id))}
                  </span>
                  <span className="font-semibold text-foreground">{item.tokens}</span>
                </li>
              ))}
              {!overview?.tokens.users.length && !overview?.tokens.groups.length ? (
                <li>
                  <EmptyState tone="inline" className="py-0" title="还没有排行数据。" />
                </li>
              ) : null}
            </ul>
          </SurfaceCard>
        </div>
      </div>
    </PageFrame>
  );
}
