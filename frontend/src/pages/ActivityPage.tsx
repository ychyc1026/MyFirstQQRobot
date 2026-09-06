import { Activity, ExternalLink, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { DataTable, type DataColumn } from "../components/DataTable";
import { EmptyState } from "../components/EmptyState";
import { PageFrame } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { Select } from "../components/Select";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { api, type ActivityItem } from "../lib/api";
import { activityActionLabel, activityDetailLabel, activitySourceLabel } from "../lib/labels";

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

export function ActivityPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("all");
  const [error, setError] = useState("");

  useEffect(() => { api.opsActivity(50).then((body) => setItems(body.items)).catch((err: Error) => setError(err.message)); }, []);
  const rows = useMemo(() => items.filter((item) => {
    const sourceHit = source === "all" || item.source === source;
    const queryHit = !query.trim() || `${activityActionLabel(item.title)} ${activityDetailLabel(item.detail)} ${item.title}`.toLowerCase().includes(query.trim().toLowerCase());
    return sourceHit && queryHit;
  }), [items, query, source]);

  const columns: DataColumn<ActivityItem>[] = [
    { key: "time", label: "时间", className: "w-[190px] whitespace-nowrap", render: (item) => <span className="text-muted-foreground">{formatTime(item.at)}</span> },
    { key: "action", label: "操作", render: (item) => <div><p className="font-medium">{activityActionLabel(item.title)}</p><p className="mt-0.5 font-mono text-[11px] text-muted-foreground">{item.title}</p></div> },
    { key: "source", label: "来源", className: "w-[120px]", render: (item) => <Badge variant="outline">{activitySourceLabel(item.source)}</Badge> },
    { key: "detail", label: "对象与结果", render: (item) => <span className="text-muted-foreground">{activityDetailLabel(item.detail)}</span> },
    { key: "open", label: "", className: "w-[60px] text-right", render: (item) => <Button variant="ghost" size="icon" aria-label={`打开${activityActionLabel(item.title)}`} onClick={() => navigate(item.href)}><ExternalLink className="h-4 w-4" /></Button> },
  ];

  return <PageFrame className="gap-6">
    <PageTitle title="操作日志" description="汇总仪表盘审计、主号命令和主号汇报。这里只展示安全摘要，不展示消息正文或密钥。" badge={<Badge variant="secondary">最近 {items.length} 条</Badge>} />
    {error ? <p className="text-sm text-destructive">{error}</p> : null}
    <Card className="flex-1"><CardHeader><CardTitle className="flex items-center gap-2"><Activity className="h-4 w-4" />日志记录</CardTitle><CardDescription>默认读取服务端最近 50 条，可按来源和关键词筛选。</CardDescription></CardHeader><CardContent>
      <div className="mb-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_180px]"><div className="relative"><Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索操作或对象" className="pl-9" /></div><Select value={source} onChange={setSource} options={[{ value: "all", label: "全部来源" }, { value: "audit", label: "审计记录" }, { value: "control_command", label: "主号命令" }, { value: "owner_report", label: "主号汇报" }]} aria-label="日志来源" /></div>
      <DataTable rows={rows} columns={columns} rowKey={(item) => `${item.source}-${item.at}-${item.title}-${item.detail}`} empty={<EmptyState title="没有匹配的操作记录" detail="调整筛选条件，或等待新的审计事件。" />} />
    </CardContent></Card>
  </PageFrame>;
}
