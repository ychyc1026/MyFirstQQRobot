import type { LucideIcon } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";

export function MetricCard({ label, value, note, icon: Icon }: { label: string; value: string | number; note?: string; icon?: LucideIcon }) {
  return <Card><CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2"><CardTitle className="text-sm font-medium">{label}</CardTitle>{Icon ? <Icon className="h-4 w-4 text-muted-foreground" /> : null}</CardHeader><CardContent><div className="text-2xl font-bold tabular-nums">{value}</div>{note ? <p className="mt-1 text-xs text-muted-foreground">{note}</p> : null}</CardContent></Card>;
}
