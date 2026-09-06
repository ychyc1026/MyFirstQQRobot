import { AlertTriangle, CheckCircle2, Database, RefreshCw, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { DangerCard } from "../components/DangerCard";
import { EmptyState } from "../components/EmptyState";
import { LoadingState } from "../components/LoadingState";
import { ErrorState } from "../components/ErrorState";
import { IdentityLock } from "../components/IdentityLock";
import { PageFrame } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { Select } from "../components/Select";
import { StatusBadge } from "../components/StatusBadge";
import { Alert, AlertDescription, AlertTitle } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Skeleton } from "../components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { api, type CapabilityScope, type ControlCommandItem, type DatabasePreflight, type Identity, type PrivacyRequestItem, type PrivacyStatus, type ReadinessDetail, type ReadinessProfile, type ReadinessSummary, type SystemStatus } from "../lib/api";
import { CAPABILITY_SCOPE_LABELS, COMMAND_ACTION_LABELS, EVIDENCE_SOURCE_LABELS, GENERIC_STATUS_LABELS, PROBE_LABELS, READINESS_PROFILE_LABELS, READINESS_REASON_LABELS, READINESS_STATUS_LABELS, REMEDIATION_LABELS } from "../lib/labels";

const CONTROLLED_SCOPES: CapabilityScope[] = ["qq_reply", "qq_proactive", "owner_report", "qzone_publish", "qzone_profile", "live_history", "chat_model", "vision_model", "image_model", "stats_model"];

function fmt(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function safeDetail(value?: string) {
  if (!value) return "服务端未提供补充说明。";
  const labels: Record<string, string> = {
    "matching launcher evidence is absent": "没有找到匹配的启动检查证据。",
    "launcher evidence belongs to a different process instance": "启动证据属于另一个进程实例。",
    "configuration changed after launcher preflight": "启动检查后配置发生了变化。",
    "required launcher probe evidence is absent": "缺少必需的启动检查证据。",
    "database is healthy": "数据库健康检查通过。", "database health check failed": "数据库健康检查失败。",
    "worker ceilings are explicit": "所有工作进程上限均已明确配置。",
    "one or more worker ceilings are undefined": "一个或多个工作进程上限未定义。",
    "offline adapters, outbox and context isolation are active": "离线适配器、非投递发件箱与上下文隔离均已生效。",
    "offline runtime isolation is incomplete": "离线运行隔离不完整。",
    "persistent pause is active": "持久暂停正在生效。", "persistent pause is clear": "当前没有持久暂停。",
    "OneBot connection matches the evaluated bot": "OneBot 连接与被评估机器人完全匹配。",
    "OneBot authentication, connection or exact bot identity does not match": "OneBot 认证、连接或机器人身份不匹配。",
    "capability is configured and active": "能力已配置且活动闸门开启。",
    "capability configuration, active gate or circuit is unavailable": "能力配置、活动闸门或熔断状态不可用。",
    "required worker is active": "所需工作进程正在运行。", "required worker is not active": "所需工作进程没有运行。",
    "activation scope permits the capability": "启用范围允许此能力。",
    "activation scope or emergency pause blocks the capability": "启用范围或紧急暂停阻止了此能力。",
    "current owner authorization is valid": "当前主号授权有效。",
    "current owner authorization is absent or mismatched": "当前主号授权缺失或不匹配。",
    "local configuration is valid": "本机配置有效。",
    "administrator listener must use a loopback address": "后台监听必须使用本机回环地址。",
    "database is readable and migration backup policy is available": "数据库可读，迁移备份策略可用。",
    "database or required migration backup policy is not ready": "数据库或必需的迁移备份策略尚未就绪。",
    "administrator bootstrap authentication is configured": "后台启动认证已配置。",
    "administrator bootstrap authentication is not configured": "后台启动认证未配置。",
    "persisted evidence is past its absolute expiry": "持久化证据已超过绝对有效期。",
    "historical process evidence cannot authorize this instance": "历史进程证据不能授权当前实例。",
  };
  if (labels[value]) return labels[value];
  if (/^\d+ managed roots are confined$/.test(value)) return `${value.split(" ")[0]} 个受管目录均处于项目安全边界内。`;
  if (/port.*available|available.*port/i.test(value)) return "后台监听端口当前可用。";
  return "后端已记录安全说明；此界面只展示本地化状态与修复建议。";
}

export function SystemPage() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [privacy, setPrivacy] = useState<PrivacyStatus | null>(null);
  const [preflight, setPreflight] = useState<DatabasePreflight | null>(null);
  const [commands, setCommands] = useState<ControlCommandItem[]>([]);
  const [requests, setRequests] = useState<PrivacyRequestItem[]>([]);
  const [scope, setScope] = useState<CapabilityScope>("qq_reply");
  const [summary, setSummary] = useState<ReadinessSummary | null>(null);
  const [details, setDetails] = useState<Partial<Record<ReadinessProfile, ReadinessDetail>>>({});
  const [loading, setLoading] = useState(true);
  const [readinessLoading, setReadinessLoading] = useState(true);
  const [busy, setBusy] = useState<ReadinessProfile | "pause" | null>(null);
  const [error, setError] = useState("");
  const [readinessError, setReadinessError] = useState("");
  const [notice, setNotice] = useState("");

  async function loadReadiness(nextScope = scope) {
    setReadinessLoading(true); setReadinessError("");
    try {
      const next = await api.readinessSummary(nextScope);
      setSummary(next);
      const settled = await Promise.allSettled(next.profiles.map((item) => api.readinessCurrent(item.profile, item.capability_scope, next.bot_qq, next.process_instance_id)));
      const collected: Partial<Record<ReadinessProfile, ReadinessDetail>> = {};
      settled.forEach((result, index) => { if (result.status === "fulfilled") collected[next.profiles[index].profile] = result.value; });
      setDetails(collected);
      if (settled.some((item) => item.status === "rejected")) setReadinessError("部分检查明细暂不可用；缺失项不会显示为通过。");
    } catch (err) {
      setSummary(null); setDetails({});
      setReadinessError(err instanceof Error ? err.message : "生产就绪证据暂不可用");
    } finally { setReadinessLoading(false); }
  }

  async function reload() {
    setLoading(true); setError("");
    const settled = await Promise.allSettled([api.identity(), api.status(), api.privacyStatus(), api.systemPreflight(), api.controlCommands(20), api.privacyRequests()] as const);
    if (settled[0].status === "fulfilled") setIdentity(settled[0].value);
    if (settled[1].status === "fulfilled") setStatus(settled[1].value);
    if (settled[2].status === "fulfilled") setPrivacy(settled[2].value);
    if (settled[3].status === "fulfilled") setPreflight(settled[3].value);
    if (settled[4].status === "fulfilled") setCommands(settled[4].value.items);
    if (settled[5].status === "fulfilled") setRequests(settled[5].value.items);
    if (settled.some((item) => item.status === "rejected")) setError("部分系统信息暂不可用；缺失数据保持未知，不会显示为正常。");
    setLoading(false);
  }

  useEffect(() => { void reload(); }, []);
  useEffect(() => { void loadReadiness(scope); }, [scope]);

  async function refreshProfile(profile: ReadinessProfile) {
    const item = summary?.profiles.find((entry) => entry.profile === profile);
    if (!summary || !item) return;
    setBusy(profile); setReadinessError(""); setNotice("");
    try {
      await api.refreshReadiness({ bot_qq: summary.bot_qq, process_instance_id: summary.process_instance_id, profile, capability_scope: item.capability_scope });
      setNotice(`${READINESS_PROFILE_LABELS[profile]}已使用当前进程的新证据重新检查。`);
      await loadReadiness(scope);
    } catch (err) { setReadinessError(err instanceof Error ? err.message : "检查失败"); }
    finally { setBusy(null); }
  }

  async function emergencyPause() {
    setBusy("pause"); setError(""); setNotice("");
    try {
      await Promise.all([api.knowledgeWorkerPause(), api.qzoneWorkerPause(), api.proactiveWorkerPause(), api.outboxWorkerPause(), api.reportWorkerPause(), api.runCommand("outbound.pause", {})]);
      setNotice("已请求暂停资料、空间、调度、外发与汇报工作进程；配置总闸和只读身份没有改变。");
      await Promise.all([reload(), loadReadiness(scope)]);
    } catch (err) { setError(err instanceof Error ? err.message : "紧急暂停失败"); }
    finally { setBusy(null); }
  }

  const flags: [string, boolean | undefined][] = [
    ["历史读取", privacy?.live_history_read_enabled], ["空间资料", privacy?.qzone_profile_collection_enabled],
    ["空间发布", status?.control?.qzone_publish_enabled], ["影子推理", privacy?.shadow_inference_enabled],
    ["模型网络", status?.models?.chat?.network_enabled], ["资料炼化", privacy?.knowledge_processing_enabled],
    ["隐私任务", privacy?.privacy_jobs_enabled], ["主号汇报", status?.control?.owner_reports_enabled],
  ];

  return <PageFrame className="gap-6">
    <PageTitle title="系统与审计" description="查看当前进程的生产证据、只读配置和安全边界。配置完成、能力启用、运行与暂停是不同状态。" badge={<Badge variant={status?.mode === "active" ? "default" : "secondary"}>{status?.mode === "active" ? "主动模式" : "观察模式"}</Badge>} />
    {error ? <Alert variant="destructive"><AlertTriangle className="h-4 w-4"/><AlertTitle>部分系统信息缺失</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
    {notice ? <Alert><CheckCircle2 className="h-4 w-4"/><AlertTitle>操作已记录</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert> : null}

    <section className="space-y-4" aria-labelledby="readiness-heading">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between"><div><h2 id="readiness-heading" className="text-xl font-semibold">生产就绪证据</h2><p className="text-sm text-muted-foreground">三层检查绑定当前机器人与进程；过期、缺失或部分失败都不会被当成通过。</p></div><div className="w-full sm:w-64"><Select value={scope} onChange={(value) => setScope(value as CapabilityScope)} options={CONTROLLED_SCOPES.map((value) => ({ value, label: CAPABILITY_SCOPE_LABELS[value] }))} aria-label="选择受控能力"/></div></div>
      {readinessError ? <Alert variant="destructive"><ShieldAlert className="h-4 w-4"/><AlertTitle>就绪证据未完整载入</AlertTitle><AlertDescription>{readinessError}</AlertDescription></Alert> : null}
      {readinessLoading ? (
        <div className="grid gap-4 xl:grid-cols-3">{[0,1,2].map((item) => <Card key={item}><CardHeader><Skeleton className="h-5 w-36"/><Skeleton className="h-4 w-52"/></CardHeader><CardContent><Skeleton className="h-52 w-full"/></CardContent></Card>)}</div>
      ) : summary ? (
        <div className="grid items-stretch gap-4 xl:grid-cols-3">{summary.profiles.map((item) => <ReadinessCard key={item.profile} item={item} detail={details[item.profile]} busy={busy === item.profile} onRefresh={() => void refreshProfile(item.profile)}/>)}</div>
      ) : (
        <ErrorState title="生产证据不可用" detail="后端未返回就绪摘要。请确认服务仍在运行后重试。" retry={() => void loadReadiness(scope)}/>
      )}
    </section>

    <section className="grid items-stretch gap-4 xl:grid-cols-2">
      <IdentityLock identity={identity} fill={false}/>
      <Card className="h-full"><CardHeader><CardTitle>配置与连接</CardTitle><CardDescription>这些是配置/连接事实，不等同于生产就绪。</CardDescription></CardHeader><CardContent>{loading ? <LoadingState /> : <div className="grid grid-cols-2 gap-3">{flags.map(([label, on]) => <div key={label} className="rounded-md border p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 font-medium">{on === undefined ? "未知" : on ? "配置已开启" : "配置关闭"}</p></div>)}<div className="rounded-md border p-3"><p className="text-xs text-muted-foreground">OneBot</p><p className="mt-1 font-medium">{status?.onebot?.connected ? (status.onebot.exact_bot ? "已连接，机器人身份匹配" : "已连接，机器人身份不匹配") : "当前未连接"}</p><p className="mt-1 text-xs text-muted-foreground">{status?.mode === "observe_only" ? "当前为观察模式，不会外发。" : "外发状态见独立开关。"}</p></div><div className="rounded-md border p-3"><p className="text-xs text-muted-foreground">冻结用户</p><p className="mt-1 font-medium">{privacy?.frozen_users ?? "未知"}</p></div></div>}</CardContent></Card>
    </section>

    <section className="grid items-stretch gap-4 xl:grid-cols-2">
      <Card className="h-full"><CardHeader><CardTitle className="flex items-center gap-2"><Database className="h-4 w-4"/>数据库启动保护</CardTitle><CardDescription>只显示安全摘要，不公开本机路径、摘要值或管理凭据。</CardDescription></CardHeader><CardContent className="space-y-4"><div className="grid grid-cols-2 gap-3 text-sm">{[["结构版本", `v${preflight?.current_schema_version ?? "—"} / v${preflight?.latest_schema_version ?? "—"}`], ["完整性", preflight?.integrity_ok ? "通过" : preflight ? "未通过" : "未知"], ["迁移备份", preflight?.backup_enabled ? "强制开启" : "关闭"], ["备份验证", preflight ? `${preflight.backup_health.verified_count}/${preflight.backup_health.backup_count} 通过` : "未知"]].map(([label, value]) => <div key={label} className="rounded-md border p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 font-medium">{value}</p></div>)}</div><p className="text-xs leading-5 text-muted-foreground">制品保留与可恢复隔离已移至“隐私审计”。这里不再提供旧的直接清理入口，也不会暗示能够一键恢复在线数据库。</p></CardContent></Card>
      <DangerCard className="h-full"><p className="text-sm font-medium text-red-800">紧急暂停工作进程</p><p className="mt-2 text-sm leading-6 text-red-700/90">暂停资料、空间、主动调度、真实外发和汇报投递。不会更改 YCH 身份，也不会开启任何网络总闸。</p><Button variant="destructive" className="mt-4" disabled={busy === "pause"} onClick={() => void emergencyPause()}>{busy === "pause" ? "正在暂停…" : "全部暂停"}</Button></DangerCard>
    </section>

    <section className="grid items-stretch gap-4 xl:grid-cols-2">
      <AuditTable title="控制命令审计" description="最近 20 条主号或后台命令。" headers={["命令", "执行者", "状态", "时间"]} rows={commands.map((item) => [COMMAND_ACTION_LABELS[item.action] ?? item.action, item.actor_qq, GENERIC_STATUS_LABELS[item.status] ?? item.status, fmt(item.created_at)])}/>
      <AuditTable title="隐私请求" description="最近的数据导出与删除任务。" headers={["类型", "用户", "状态", "时间"]} rows={requests.map((item) => [item.request_kind === "export" ? "数据导出" : item.request_kind === "delete" ? "数据删除" : item.request_kind, item.user_qq, GENERIC_STATUS_LABELS[item.status] ?? item.status, fmt(item.created_at)])}/>
    </section>
  </PageFrame>;
}

function ReadinessCard({ item, detail, busy, onRefresh }: { item: ReadinessSummary["profiles"][number]; detail?: ReadinessDetail; busy: boolean; onRefresh: () => void }) {
  const stale = Boolean(item.stale_probe_count); const blockers = detail?.blockers ?? []; const warnings = detail?.warnings ?? [];
  const configured = detail?.probes?.find((probe) => /configuration|activation/.test(probe.probe_code));
  const pause = detail?.probes?.find((probe) => /pause/.test(probe.probe_code));
  return <Card className="flex h-full flex-col"><CardHeader className="space-y-3"><div className="flex items-start justify-between gap-3"><div><CardTitle>{READINESS_PROFILE_LABELS[item.profile]}</CardTitle><CardDescription>{CAPABILITY_SCOPE_LABELS[item.capability_scope]}</CardDescription></div><StatusBadge value={stale ? "证据过期" : READINESS_STATUS_LABELS[item.status] ?? item.status}/></div><div className="flex flex-wrap gap-2 text-xs"><Badge variant="outline">配置：{configured ? READINESS_STATUS_LABELS[configured.status] ?? configured.status : "未单独报告"}</Badge><Badge variant="outline">运行：{item.status === "passed" ? "可执行" : "不可执行"}</Badge><Badge variant="outline">暂停：{pause?.status === "blocked" || item.blocker_codes?.includes("durable_pause_active") ? "生效中" : pause ? "未暂停" : "未报告"}</Badge></div></CardHeader><CardContent className="flex flex-1 flex-col gap-4">
    {!detail?.evaluated ? <EmptyState title="尚未评估" detail="手动运行检查后才会产生当前进程证据。"/> : <><dl className="grid grid-cols-2 gap-3 rounded-md border p-3 text-xs"><div><dt className="text-muted-foreground">证据修订</dt><dd className="mt-1 font-medium">v{detail.revision ?? "—"}</dd></div><div><dt className="text-muted-foreground">检查时间</dt><dd className="mt-1 font-medium">{fmt(detail.evaluated_at)}</dd></div><div><dt className="text-muted-foreground">有效期至</dt><dd className="mt-1 font-medium">{fmt(detail.evidence_expires_at)}</dd></div><div><dt className="text-muted-foreground">过期探针</dt><dd className="mt-1 font-medium">{detail.stale_probe_count ?? 0}</dd></div></dl>
    {blockers.map((issue) => <div key={`b-${issue.code}-${issue.probe_code}`} className="rounded-md border border-destructive/30 bg-destructive/5 p-3"><p className="text-sm font-medium text-destructive">阻止：{READINESS_REASON_LABELS[issue.code] ?? issue.code}</p><p className="mt-1 text-xs text-muted-foreground">{PROBE_LABELS[issue.probe_code] ?? issue.probe_code} · {safeDetail(issue.safe_detail)}</p></div>)}{warnings.map((issue) => <div key={`w-${issue.code}-${issue.probe_code}`} className="rounded-md border border-amber-300/50 bg-amber-50/60 p-3 dark:bg-amber-950/20"><p className="text-sm font-medium">警告：{READINESS_REASON_LABELS[issue.code] ?? issue.code}</p><p className="mt-1 text-xs text-muted-foreground">{safeDetail(issue.safe_detail)}</p></div>)}{!blockers.length && !warnings.length ? <p className="rounded-md bg-emerald-50 p-3 text-sm text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300">当前没有阻止项或警告。</p> : null}
    <div className="space-y-2">{detail.probes?.map((probe) => <details key={`${probe.probe_code}-${probe.capability_scope}`} className="rounded-md border p-3"><summary className="cursor-pointer text-sm font-medium">{PROBE_LABELS[probe.probe_code] ?? "系统检查"} · {READINESS_STATUS_LABELS[probe.status] ?? "未知"}</summary><div className="mt-3 space-y-1 text-xs text-muted-foreground"><p>{safeDetail(probe.safe_detail)}</p><p>来源：{EVIDENCE_SOURCE_LABELS[probe.source] ?? "系统证据"} · 修订 {probe.source_revision}</p><p>观察：{fmt(probe.observed_at)} · 到期：{fmt(probe.expires_at)}</p>{probe.remediation_code ? <p>建议：{REMEDIATION_LABELS[probe.remediation_code] ?? "按运行手册人工复核"}</p> : null}</div></details>)}</div></>}
    <Button className="mt-auto" variant="outline" disabled={busy} onClick={onRefresh}><RefreshCw className={`mr-2 h-4 w-4 ${busy ? "animate-spin" : ""}`}/>重新检查当前层</Button>
  </CardContent></Card>;
}

function AuditTable({ title, description, headers, rows }: { title: string; description: string; headers: string[]; rows: string[][] }) {
  return <Card className="h-full"><CardHeader><CardTitle>{title}</CardTitle><CardDescription>{description}</CardDescription></CardHeader><CardContent>{rows.length ? <div className="overflow-x-auto rounded-md border"><Table><TableHeader><TableRow>{headers.map((header) => <TableHead key={header}>{header}</TableHead>)}</TableRow></TableHeader><TableBody>{rows.map((row, index) => <TableRow key={`${row[0]}-${index}`}>{row.map((cell, cellIndex) => <TableCell key={`${cell}-${cellIndex}`} className={cellIndex === 0 ? "font-medium" : cellIndex === row.length - 1 ? "whitespace-nowrap text-muted-foreground" : ""}>{cellIndex === 2 ? <StatusBadge value={cell}/> : cell}</TableCell>)}</TableRow>)}</TableBody></Table></div> : <EmptyState title={`没有${title}`} detail="当前没有可显示的记录。"/>}</CardContent></Card>;
}
