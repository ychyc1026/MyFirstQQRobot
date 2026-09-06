import type { CSSProperties } from "react";

export const CHART_CURSOR = { fill: "hsl(var(--muted))", opacity: 0.18 };

export const CHART_TOOLTIP_STYLE: CSSProperties = {
  borderRadius: 8,
  border: "1px solid hsl(var(--border))",
  background: "hsl(var(--popover))",
  boxShadow: "0 8px 20px rgb(0 0 0 / 0.08)",
  color: "hsl(var(--popover-foreground))",
};

export const CHART_TOOLTIP_LABEL_STYLE: CSSProperties = {
  color: "hsl(var(--muted-foreground))",
  fontSize: 12,
  fontWeight: 600,
};

export const CHART_TOOLTIP_ITEM_STYLE: CSSProperties = {
  color: "hsl(var(--foreground))",
  fontSize: 13,
  fontWeight: 600,
};
