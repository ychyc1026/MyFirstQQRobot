import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { SeriesPoint } from "../lib/api";
import { CHART_CURSOR, CHART_TOOLTIP_ITEM_STYLE, CHART_TOOLTIP_LABEL_STYLE, CHART_TOOLTIP_STYLE } from "../lib/chartTheme";
import { EmptyState } from "./EmptyState";
import { SegmentSlider } from "./SegmentSlider";
import { SurfaceCard } from "./SurfaceCard";

type TrendCardProps = {
  points: SeriesPoint[];
  days: 1 | 7 | 30;
  onDaysChange: (days: 1 | 7 | 30) => void;
};

const ranges: { value: 1 | 7 | 30; label: string }[] = [
  { value: 1, label: "今日" },
  { value: 7, label: "7 日" },
  { value: 30, label: "30 日" },
];

export function TrendCard({ points, days, onDaysChange }: TrendCardProps) {
  const empty = points.every(
    (point) => point.inbound === 0 && point.commands === 0 && point.approvals === 0,
  );

  return (
    <SurfaceCard
      emphasis="featured"
      heading={{
        level: "primary",
        title: "近况趋势",
        subtitle: "入库 / 命令 / 审批，按上海自然日",
        action: <SegmentSlider value={days} options={ranges} onChange={onDaysChange} />,
      }}
    >
      <div className="h-64">
        {empty ? (
          <EmptyState
            tone="inline"
            className="flex h-full items-center justify-center py-0"
            title="还没有事件"
          />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={points} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="hsl(var(--muted))" vertical={false} />
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
              <Tooltip
                cursor={CHART_CURSOR}
                contentStyle={CHART_TOOLTIP_STYLE}
                labelStyle={CHART_TOOLTIP_LABEL_STYLE}
                itemStyle={CHART_TOOLTIP_ITEM_STYLE}
              />
              <Line type="monotone" dataKey="inbound" stroke="hsl(var(--chart-1))" strokeWidth={2} dot={false} name="入库" />
              <Line type="monotone" dataKey="commands" stroke="hsl(var(--chart-2))" strokeWidth={2} dot={false} name="命令" />
              <Line type="monotone" dataKey="approvals" stroke="hsl(var(--muted-foreground))" strokeWidth={2} dot={false} name="审批" />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </SurfaceCard>
  );
}
