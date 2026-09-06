import { ArchiveRestore, FileWarning, RefreshCw, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ApiError, api, type ArtifactOwnerScope, type ManagedArtifact, type QuarantineBatch, type ReadinessSummary, type RetentionPreview, type RetentionTarget } from "../lib/api";
import { ARTIFACT_STATE_LABELS, ARTIFACT_TYPE_LABELS, RETENTION_TARGET_LABELS } from "../lib/labels";
import { DataTable, type DataColumn } from "./DataTable";
import { EmptyState } from "./EmptyState";
import { LoadingState } from "./LoadingState";
import { Select } from "./Select";
import { StatusBadge } from "./StatusBadge";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "./ui/alert-dialog";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Input } from "./ui/input";

const PAGE_SIZE = 50;
const TYPES = ["", "migration_backup", "privacy_export", "privacy_deletion_backup", "imported_source", "quarantine_evidence"];
const RETENTION_ERROR_LABELS: Record<string, string> = {
  no_candidates: "当前范围没有通过验证且符合策略的隔离候选。",
  owner_scope_required: "一次预览只能针对一个明确的数据所有者。",
  preview_expired: "确认预览已过期，请重新生成。",
  candidate_changed: "候选文件或引用状态已变化，请重新生成预览。",
  preview_conflict: "该预览已使用、过期或发生并发变化，请重新生成。",
  process_instance_mismatch: "服务进程已变化，请刷新页面后重新预览。",
};

function fmt(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}
function bytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
}
function message(error: unknown) {
  if (error instanceof ApiError && error.code && RETENTION_ERROR_LABELS[error.code]) return RETENTION_ERROR_LABELS[error.code];
  return error instanceof Error ? error.message : "操作失败";
}

export function OperationalArtifacts() {
  const [items, setItems] = useState<ManagedArtifact[]>([]);
  const [batches, setBatches] = useState<QuarantineBatch[]>([]);
  const [runtime, setRuntime] = useState<ReadinessSummary | null>(null);
  const [ownerScope, setOwnerScope] = useState<"all" | ArtifactOwnerScope>("all");
  const [ownerQq, setOwnerQq] = useState("");
  const [artifactType, setArtifactType] = useState("");
  const [verification, setVerification] = useState("");
  const [reference, setReference] = useState("");
  const [retention, setRetention] = useState("");
  const [target, setTarget] = useState<RetentionTarget>("migration_backups");
  const [preview, setPreview] = useState<RetentionPreview | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"refresh" | "preview" | "confirm" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [page, setPage] = useState(1);

  const filters = useMemo(() => ({ owner_scope: ownerScope === "all" ? undefined : ownerScope, owner_qq: ownerScope === "user" ? ownerQq.trim() || undefined : undefined, artifact_type: artifactType || undefined, verification_state: verification || undefined, reference_state: reference || undefined, retention_state: retention || undefined, limit: 500 }), [ownerScope, ownerQq, artifactType, verification, reference, retention]);

  async function load() {
    setLoading(true); setError("");
    if (ownerScope === "user" && ownerQq.trim() && !/^\d+$/.test(ownerQq.trim())) { setError("用户 QQ 只接受数字。"); setLoading(false); return; }
    const settled = await Promise.allSettled([api.managedArtifacts(filters), api.quarantineHistory({ owner_scope: filters.owner_scope, owner_qq: filters.owner_qq }), api.readinessSummary()]);
    if (settled[0].status === "fulfilled") setItems(settled[0].value.items); else setItems([]);
    if (settled[1].status === "fulfilled") setBatches(settled[1].value.items); else setBatches([]);
    if (settled[2].status === "fulfilled") setRuntime(settled[2].value); else setRuntime(null);
    if (settled.some((entry) => entry.status === "rejected")) setError("部分制品信息暂不可用；未返回的数据不会被视为正常或可清理。");
    setPage(1); setLoading(false);
  }
  useEffect(() => { void load(); }, []);

  async function refreshCatalog() {
    if (!runtime) { setError("当前进程身份不可用，不能刷新制品目录。"); return; }
    setBusy("refresh"); setError(""); setNotice("");
    try {
      await api.refreshManagedArtifacts({ bot_qq: runtime.bot_qq, process_instance_id: runtime.process_instance_id, owner_scope: filters.owner_scope, owner_qq: filters.owner_qq });
      setNotice("目录证据已重新扫描；刷新只更新分类，不移动或删除文件。");
      await load();
    } catch (err) { setError(message(err)); } finally { setBusy(null); }
  }

  async function createPreview() {
    if (!runtime) { setError("当前进程身份不可用，不能生成隔离预览。"); return; }
    if (ownerScope === "all") { setError("请先明确选择“系统制品”或“用户制品”；保留操作不能跨所有者执行。"); return; }
    if (ownerScope === "user" && !/^\d+$/.test(ownerQq.trim())) { setError("用户制品预览需要一个有效的数字 QQ。"); return; }
    setBusy("preview"); setError(""); setNotice(""); setPreview(null);
    try {
      const body = await api.previewArtifactRetention({ bot_qq: runtime.bot_qq, process_instance_id: runtime.process_instance_id, target_batch_type: target, owner_scope: ownerScope, owner_qq: ownerScope === "user" ? ownerQq.trim() : undefined });
      setPreview(body); setConfirmOpen(true);
    } catch (err) { setError(message(err)); } finally { setBusy(null); }
  }

  async function confirmPreview() {
    if (!runtime || !preview) return;
    setBusy("confirm"); setError("");
    try {
      const result = await api.confirmArtifactRetention({ bot_qq: runtime.bot_qq, process_instance_id: runtime.process_instance_id, confirmation_token: preview.confirmation_token, expected_revision: preview.revision });
      setNotice(`已将 ${result.candidate_count} 个制品移入可恢复隔离区；没有永久删除，也没有改写在线数据库。`);
      setConfirmOpen(false); setPreview(null); await load();
    } catch (err) {
      setError(message(err)); setConfirmOpen(false); setPreview(null);
    } finally { setBusy(null); }
  }

  const totalBytes = items.reduce((sum, item) => sum + item.size_bytes, 0);
  const candidates = items.filter((item) => item.retention_state === "candidate");
  const anomalies = items.filter((item) => ["invalid", "missing", "anomalous"].includes(item.verification_state));
  const pageCount = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const visible = items.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const columns: DataColumn<ManagedArtifact>[] = [
    { key: "name", label: "制品", render: (item) => <div><p className="max-w-72 truncate font-medium" title={item.display_name}>{item.display_name}</p><p className="text-xs text-muted-foreground">{ARTIFACT_TYPE_LABELS[item.artifact_type] ?? item.artifact_type}</p></div> },
    { key: "owner", label: "所有者", render: (item) => item.owner_scope === "system" ? "系统" : `用户 ${item.owner_qq}` },
    { key: "size", label: "大小", className: "whitespace-nowrap", render: (item) => bytes(item.size_bytes) },
    { key: "verify", label: "验证", render: (item) => <StatusBadge value={ARTIFACT_STATE_LABELS[item.verification_state] ?? item.verification_state}/> },
    { key: "reference", label: "引用", render: (item) => <StatusBadge value={ARTIFACT_STATE_LABELS[item.reference_state] ?? item.reference_state}/> },
    { key: "retention", label: "保留", render: (item) => <StatusBadge value={ARTIFACT_STATE_LABELS[item.retention_state] ?? item.retention_state}/> },
    { key: "created", label: "创建时间", className: "whitespace-nowrap", render: (item) => fmt(item.created_at) },
  ];

  return <section className="space-y-4" aria-labelledby="artifact-heading">
    <div><h2 id="artifact-heading" className="text-xl font-semibold">受管制品与可恢复隔离</h2><p className="text-sm text-muted-foreground">统一查看备份、隐私导出和导入资料。页面绝不展示本机绝对路径、摘要值或确认令牌。</p></div>
    {error ? <Alert variant="destructive"><FileWarning className="h-4 w-4"/><AlertTitle>制品操作未完成</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
    {notice ? <Alert><ShieldCheck className="h-4 w-4"/><AlertTitle>安全操作已记录</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert> : null}
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{[["受管制品", items.length], ["总大小", bytes(totalBytes)], ["隔离候选", candidates.length], ["文件异常", anomalies.length]].map(([label, value]) => <Card key={label}><CardHeader className="pb-2"><CardDescription>{label}</CardDescription><CardTitle className="text-2xl">{value}</CardTitle></CardHeader></Card>)}</div>
    <Card><CardHeader><CardTitle>筛选与重新检查</CardTitle><CardDescription>“重新扫描”只刷新目录证据，不移动文件。用户范围必须明确填写 QQ。</CardDescription></CardHeader><CardContent className="grid gap-3 md:grid-cols-2 xl:grid-cols-7">
      <Select value={ownerScope} onChange={(value) => setOwnerScope(value as typeof ownerScope)} options={[{value:"all",label:"全部所有者"},{value:"system",label:"系统制品"},{value:"user",label:"用户制品"}]} aria-label="制品所有者范围"/>
      <Input value={ownerQq} onChange={(event) => setOwnerQq(event.target.value)} placeholder="用户 QQ" disabled={ownerScope !== "user"} aria-label="制品所有者 QQ"/>
      <Select value={artifactType || "all"} onChange={(value) => setArtifactType(value === "all" ? "" : value)} options={TYPES.map((value) => ({value:value || "all",label:value ? ARTIFACT_TYPE_LABELS[value] : "全部类型"}))} aria-label="制品类型"/>
      <Select value={verification || "all"} onChange={(value) => setVerification(value === "all" ? "" : value)} options={["","pending","verified","invalid","missing","anomalous"].map((value) => ({value:value || "all",label:value ? ARTIFACT_STATE_LABELS[value] : "全部验证状态"}))} aria-label="验证状态"/>
      <Select value={reference || "all"} onChange={(value) => setReference(value === "all" ? "" : value)} options={["","unknown","unreferenced","protected"].map((value) => ({value:value || "all",label:value ? ARTIFACT_STATE_LABELS[value] : "全部引用状态"}))} aria-label="引用状态"/>
      <Select value={retention || "all"} onChange={(value) => setRetention(value === "all" ? "" : value)} options={["","retain","candidate","quarantined","blocked"].map((value) => ({value:value || "all",label:value ? ARTIFACT_STATE_LABELS[value] : "全部保留状态"}))} aria-label="保留状态"/>
      <div className="flex gap-2"><Button variant="outline" className="flex-1" onClick={() => void load()}>应用</Button><Button variant="outline" size="icon" disabled={busy === "refresh"} onClick={() => void refreshCatalog()} aria-label="重新扫描制品目录"><RefreshCw className={`h-4 w-4 ${busy === "refresh" ? "animate-spin" : ""}`}/></Button></div>
    </CardContent></Card>
    <Card><CardHeader><CardTitle>制品目录</CardTitle><CardDescription>最多读取 500 条；长名称截断显示，表格可横向滚动。</CardDescription></CardHeader><CardContent>{loading ? <LoadingState rows={4} /> : <DataTable rows={visible} columns={columns} rowKey={(item) => item.artifact_id} page={page} pageCount={pageCount} onPageChange={setPage} empty={<EmptyState title="没有受管制品" detail="当前筛选范围没有已登记文件；这不代表后端存储一定为空。可先运行重新扫描。"/>}/>}</CardContent></Card>
    <div className="grid items-stretch gap-4 xl:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
      <Card className="h-full"><CardHeader><CardTitle>隔离预览</CardTitle><CardDescription>先生成一次性预览，再明确确认。不会永久删除，也不会直接恢复或改写在线数据库。</CardDescription></CardHeader><CardContent className="space-y-3"><Select value={target} onChange={(value) => setTarget(value as RetentionTarget)} options={Object.entries(RETENTION_TARGET_LABELS).map(([value,label]) => ({value,label}))} aria-label="隔离批次类型"/><p className="text-xs leading-5 text-muted-foreground">当前范围：{ownerScope === "all" ? "尚未明确（不能预览）" : ownerScope === "system" ? "系统制品" : `用户 ${ownerQq || "未填写 QQ"}`}。只有“已验证、未受引用保护、符合保留策略”的候选会进入预览。</p><Button disabled={busy === "preview"} onClick={() => void createPreview()}><ArchiveRestore className="mr-2 h-4 w-4"/>{busy === "preview" ? "正在生成…" : "生成隔离预览"}</Button></CardContent></Card>
      <Card className="h-full"><CardHeader><CardTitle>隔离历史</CardTitle><CardDescription>记录批次状态和项目数；隔离是可恢复边界，不是永久删除。</CardDescription></CardHeader><CardContent>{batches.length ? <div className="space-y-3">{batches.map((batch) => <div key={batch.batch_id} className="flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center sm:justify-between"><div><p className="font-medium">{RETENTION_TARGET_LABELS[batch.batch_type] ?? batch.batch_type}</p><p className="text-xs text-muted-foreground">{batch.owner_scope === "system" ? "系统" : `用户 ${batch.owner_qq}`} · {batch.items.length} 项 · {fmt(batch.updated_at)}</p></div><div className="flex items-center gap-2"><StatusBadge value={ARTIFACT_STATE_LABELS[batch.state] ?? batch.state}/><Badge variant="outline">v{batch.revision}</Badge></div></div>)}</div> : <EmptyState title="没有隔离历史" detail="尚未执行可恢复隔离。"/>}</CardContent></Card>
    </div>
    <AlertDialog open={confirmOpen} onOpenChange={(open) => { if (busy !== "confirm") setConfirmOpen(open); }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>确认移入可恢复隔离区</AlertDialogTitle><AlertDialogDescription asChild><div className="space-y-3"><p>即将处理 {preview?.candidate_count ?? 0} 个制品，共 {bytes(preview?.candidate_bytes ?? 0)}。</p><div className="rounded-md border p-3 text-left text-sm"><p>范围：{ownerScope === "system" ? "系统制品" : `用户 ${ownerQq}`}</p><p>类型：{RETENTION_TARGET_LABELS[preview?.target_batch_type ?? target]}</p><p>预览有效期：{fmt(preview?.expires_at)}</p></div><ul className="list-disc space-y-1 pl-5 text-left"><li>操作可恢复，不会永久删除文件。</li><li>不会修改、回滚或一键恢复在线数据库。</li><li>确认后此一次性预览立即失效；文件或引用变化会阻止执行。</li></ul></div></AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel disabled={busy === "confirm"}>取消</AlertDialogCancel><AlertDialogAction disabled={busy === "confirm"} onClick={(event) => { event.preventDefault(); void confirmPreview(); }}>{busy === "confirm" ? "正在隔离…" : "确认移入隔离区"}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </section>;
}
