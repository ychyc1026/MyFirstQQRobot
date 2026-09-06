import { Activity, AlertTriangle, ArrowUpRight, Bot, MessageSquare, ShieldCheck, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { PageFrame } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { StatusBadge } from "../components/StatusBadge";
import { Timeline } from "../components/Timeline";
import { Alert, AlertDescription, AlertTitle } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { api, type ApprovalItem, type Identity, type OpsSnapshot, type OwnerReport, type ReadinessSummary, type UserIdentitySummary } from "../lib/api";
import { CAPABILITY_SCOPE_LABELS, READINESS_PROFILE_LABELS, READINESS_STATUS_LABELS } from "../lib/labels";

function relTime(value?: string | null) {
  if (!value) return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return value;
  const delta = Date.now() - then;
  if (delta < 60_000) return "刚刚";
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)} 分钟前`;
  if (delta < 86_400_000) return "今天";
  return value.slice(0, 10);
}

export function OverviewPage() {
  const navigate = useNavigate();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [snapshot, setSnapshot] = useState<OpsSnapshot | null>(null);
  const [summary, setSummary] = useState<UserIdentitySummary | null>(null);
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [reports, setReports] = useState<OwnerReport[]>([]);
  const [readiness, setReadiness] = useState<ReadinessSummary | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.identity(), api.opsSnapshot(), api.approvals(), api.reports("pending"), api.users()])
      .then(([identityBody, snapshotBody, approvalsBody, reportsBody, usersBody]) => {
        if (cancelled) return;
        setIdentity(identityBody); setSnapshot(snapshotBody); setApprovals(approvalsBody.items); setReports(reportsBody.items); setSummary(usersBody.summary);
      })
      .catch((err: Error) => { if (!cancelled) setError(err.message); });
    api.readinessSummary().then((body) => { if (!cancelled) setReadiness(body); }).catch(() => { /* 独立降级：不把证据接口异常伪装成整个后端断开 */ });
    return () => { cancelled = true; };
  }, []);

  const rows = useMemo(() => [
    ...approvals.map((item) => ({ id: item.id, title: item.request_type, kind: "审批", status: "待审批", at: item.created_at, href: `/approvals?tab=approval&id=${item.id}` })),
    ...reports.map((item) => ({ id: item.id, title: item.title, kind: "汇报", status: item.severity, at: item.created_at, href: `/approvals?tab=report&id=${item.id}` })),
  ].filter((item) => `${item.title} ${item.kind} ${item.status}`.toLowerCase().includes(query.trim().toLowerCase())).slice(0, 8), [approvals, reports, query]);

  const userCount = Object.values(summary?.friend_states ?? {}).reduce((sum, value) => sum + value, 0);
  const metrics = [
    { label: "今日入站消息", value: snapshot?.inbound_today ?? "—", icon: MessageSquare, note: snapshot?.day ?? "等待后端" },
    { label: "待处理审批", value: snapshot?.attention.pending_approvals ?? "—", icon: ShieldCheck, note: `${snapshot?.attention.needs_owner ?? 0} 项需要主号` },
    { label: "已识别用户", value: summary ? userCount : "—", icon: Users, note: `${summary?.frozen_users ?? 0} 个已冻结档案` },
    { label: "运行异常", value: snapshot?.anomalies.total ?? "—", icon: AlertTriangle, note: `${snapshot?.anomalies.circuit_open ?? 0} 个熔断` },
  ];

  return <PageFrame className="gap-6">
    <PageTitle title="总览" description="QQ 机器人运行状态、待办与关键活动。" badge={<><Badge variant={snapshot?.mode === "active" ? "default" : "secondary"}>{snapshot?.mode === "active" ? "主动模式" : "观察模式"}</Badge><Button variant="outline" size="sm" onClick={() => navigate("/system")}>系统设置</Button></>} />
    {error ? <Alert variant="destructive"><AlertTitle>无法载入总览</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
    <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{metrics.map(({ label, value, icon: Icon, note }) => <Card key={label}><CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2"><CardTitle className="text-sm font-medium">{label}</CardTitle><Icon className="h-4 w-4 text-muted-foreground" /></CardHeader><CardContent><div className="text-2xl font-bold tabular-nums">{value}</div><p className="mt-1 text-xs text-muted-foreground">{note}</p></CardContent></Card>)}</section>
    <section className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
      <Card><CardHeader className="flex-row items-start justify-between space-y-0"><div><CardTitle>待处理队列</CardTitle><CardDescription>仅展示服务端真实审批和主号汇报。</CardDescription></div><Button variant="ghost" size="sm" onClick={() => navigate("/approvals")}>查看全部<ArrowUpRight className="ml-1 h-4 w-4" /></Button></CardHeader><CardContent><div className="mb-4"><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、类型或状态" aria-label="搜索待处理队列" /></div>{rows.length ? <div className="overflow-hidden rounded-md border"><Table><TableHeader><TableRow><TableHead>事项</TableHead><TableHead>类型</TableHead><TableHead>状态</TableHead><TableHead className="text-right">时间</TableHead></TableRow></TableHeader><TableBody>{rows.map((item) => <TableRow key={`${item.kind}-${item.id}`} className="cursor-pointer" onClick={() => navigate(item.href)}><TableCell className="font-medium">{item.title}</TableCell><TableCell><Badge variant="outline">{item.kind}</Badge></TableCell><TableCell><StatusBadge value={item.status} /></TableCell><TableCell className="text-right text-muted-foreground">{relTime(item.at)}</TableCell></TableRow>)}</TableBody></Table></div> : <EmptyState title="没有待处理事项" detail="当前没有审批或未确认汇报。" />}</CardContent></Card>
      <div className="space-y-4"><Card><CardHeader><CardTitle className="flex items-center gap-2"><Bot className="h-4 w-4" />固定身份</CardTitle><CardDescription>由系统配置锁定，普通用户不能修改。</CardDescription></CardHeader><CardContent className="space-y-3"><div><p className="text-xs text-muted-foreground">品牌</p><p className="font-medium">{identity?.brand ?? "YCH"}</p></div><div><p className="text-xs text-muted-foreground">创造者</p><p className="font-medium">{identity?.creator_name ?? "YCH（维护者）"}</p></div><div><p className="text-xs text-muted-foreground">主号</p><p className="font-mono text-sm">{identity?.owner_qq ?? "2000000001"}</p></div><Badge variant="secondary">只读身份</Badge></CardContent></Card><Card><CardHeader><CardTitle>生产就绪证据</CardTitle><CardDescription>当前进程的三层检查；配置完成不等于能力已启用。</CardDescription></CardHeader><CardContent className="space-y-3">{readiness ? readiness.profiles.map((item) => { const stale = Boolean(item.stale_probe_count); const text = stale ? "证据过期" : READINESS_STATUS_LABELS[item.status] ?? item.status; return <div key={item.profile} className="flex items-center justify-between gap-3 rounded-md border px-3 py-2"><div className="min-w-0"><p className="text-sm font-medium">{READINESS_PROFILE_LABELS[item.profile]}</p><p className="truncate text-xs text-muted-foreground">{CAPABILITY_SCOPE_LABELS[item.capability_scope]}</p></div><StatusBadge value={text} /></div>; }) : <EmptyState tone="inline" className="py-4" title="生产证据暂不可用，请到系统设置重试。" />}<Button variant="outline" size="sm" className="w-full" onClick={() => navigate("/system")}>查看证据与修复建议</Button></CardContent></Card></div>
    </section>
    <Card className="flex-1"><CardHeader><CardTitle className="flex items-center gap-2"><Activity className="h-4 w-4" />运行摘要</CardTitle><CardDescription>异常与待处理事件按当前服务端快照生成。</CardDescription></CardHeader><CardContent><Timeline items={[{ id: "approval", title: `${snapshot?.attention.pending_approvals ?? 0} 项审批等待处理`, detail: `${snapshot?.attention.urgent_reports ?? 0} 条紧急汇报`, time: snapshot?.day }, { id: "outbox", title: `${snapshot?.anomalies.outbox_failed ?? 0} 条发件失败`, detail: `${snapshot?.anomalies.qzone_uncertain ?? 0} 条空间发布状态不确定` }, { id: "onebot", title: snapshot?.onebot.connected ? "OneBot 连接正常" : "OneBot 当前未连接", detail: snapshot?.onebot.last_event_at ? `最近事件 ${relTime(snapshot.onebot.last_event_at)}` : "尚无事件时间" }]} /></CardContent></Card>
  </PageFrame>;
}
