import { type FormEvent, useEffect, useState } from "react";
import { ArchiveRestore, FileCheck2, Search, ShieldCheck } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { PageColumn, PageFrame, PageSplit } from "../components/PageLayout";
import { OperationalArtifacts } from "../components/OperationalArtifacts";
import { PageTitle } from "../components/PageTitle";
import { Select } from "../components/Select";
import { SurfaceCard } from "../components/SurfaceCard";
import {
  api,
  type DataAccessAudit,
  type PrivacyArtifactVerification,
  type PrivacyRequestItem,
} from "../lib/api";
import { peerTitle } from "../lib/peerLabels";
import { useOperatorLabels } from "../lib/useOperatorLabels";

const DATA_CLASS_LABELS: Record<string, string> = {
  message_content: "聊天内容",
  qzone_content: "QQ 空间资料",
  public_profile: "公开资料",
  imported_document: "导入文档",
  derived_memory: "派生记忆",
  persona_profile: "用户人格",
};

const PURPOSE_LABELS: Record<string, string> = {
  initialize_user_profile: "初始化用户理解",
  restore_recent_context: "恢复最近会话上下文",
};

const REASON_LABELS: Record<string, string> = {
  authorized: "符合授权",
  history_mode_deny: "历史策略禁止",
  history_mode_file_import_only: "只允许文件导入",
  user_data_frozen: "用户数据已冻结",
  source_cannot_enforce_selected_range: "来源无法在读取前限制日期",
  message_limit_exceeded: "超过授权条数",
  one_time_authorization_consumed: "一次授权已消耗",
  qzone_profile_collection_disabled: "空间资料全局采集关闭",
  qzone_profile_mode_deny: "空间资料策略禁止",
  qzone_profile_authorization_expired: "空间资料授权已过期",
  item_limit_exceeded: "超过空间资料授权条数",
};

const REQUEST_LABELS: Record<string, string> = {
  export: "数据导出",
  delete: "数据删除",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "待执行",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
};

function fmt(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", { hour12: false });
}

export function PrivacyPage() {
  const labels = useOperatorLabels();
  const [audit, setAudit] = useState<DataAccessAudit | null>(null);
  const [requests, setRequests] = useState<PrivacyRequestItem[]>([]);
  const [userQq, setUserQq] = useState("");
  const [decision, setDecision] = useState<"" | "allowed" | "denied">("");
  const [dataClass, setDataClass] = useState("");
  const [verification, setVerification] =
    useState<PrivacyArtifactVerification | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function reload(filters?: {
    user_qq?: string;
    decision?: "allowed" | "denied";
    data_class?: string;
  }) {
    const [accessBody, requestBody] = await Promise.all([
      api.privacyAccessLog({ ...filters, limit: 100 }),
      api.privacyRequests(),
    ]);
    setAudit(accessBody);
    setRequests(requestBody.items);
  }

  useEffect(() => {
    reload().catch((err: Error) => setError(err.message));
  }, []);

  function submitFilters(event: FormEvent) {
    event.preventDefault();
    const qq = userQq.trim();
    if (qq && !/^\d+$/.test(qq)) {
      setError("用户 QQ 只接受数字");
      return;
    }
    setError("");
    reload({
      user_qq: qq || undefined,
      decision: decision || undefined,
      data_class: dataClass || undefined,
    }).catch((err: Error) => setError(err.message));
  }

  async function verify(requestId: string) {
    setBusy(true);
    setError("");
    try {
      setVerification(await api.verifyPrivacyArtifact(requestId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法校验隐私文件");
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageFrame>
      <PageTitle
        title="隐私与访问审计"
        description="查看每一次聊天历史或空间资料读取为何被允许或拒绝，并校验导出与删除前备份。日志不保存消息正文。"
        badge={
          <span className="ych-pill">
            拒绝 {audit?.summary.denied ?? 0} · 允许{" "}
            {audit?.summary.allowed ?? 0}
          </span>
        }
      />
      {error ? (
        <p className="mb-4 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      ) : null}

      <div className="mb-5 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <SurfaceCard tone="ink" className="lg:col-span-2">
          <div className="flex items-center gap-2 text-primary-sand">
            <ShieldCheck size={18} />
            <p className="text-xs font-semibold tracking-[0.15em]">
              ACCESS DECISIONS
            </p>
          </div>
          <h2 className="mt-4 text-2xl font-semibold">读取必须留下理由</h2>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">
            允许与拒绝都会记录数据类别、目的、调用器和当时的策略快照；不记录聊天正文、空间正文或模型提示词。
          </p>
        </SurfaceCard>
        <SurfaceCard>
          <div className="flex items-center gap-2 text-primary">
            <ArchiveRestore size={18} />
            <p className="text-xs font-semibold tracking-[0.15em]">
              RECOVERY BOUNDARY
            </p>
          </div>
          <p className="mt-4 text-xl font-semibold text-foreground">
            备份可校验，不伪装成一键恢复
          </p>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            恢复只能重新审批后选择性重建；隐私墓碑和删除审计不可逆。
          </p>
        </SurfaceCard>
      </div>

      <div className="mb-5 grid grid-cols-3 gap-4">
        {[
          ["读取记录", audit?.summary.total ?? 0],
          ["已拒绝", audit?.summary.denied ?? 0],
          ["已允许", audit?.summary.allowed ?? 0],
        ].map(([label, value]) => (
          <SurfaceCard key={label} padding="p-5">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="mt-2 text-3xl font-semibold text-foreground">{value}</p>
          </SurfaceCard>
        ))}
      </div>

      <form
        className="mb-5 grid grid-cols-1 gap-3 rounded-lg bg-card p-4 md:grid-cols-[1fr_180px_200px_auto]"
        onSubmit={submitFilters}
      >
        <input
          value={userQq}
          onChange={(event) => setUserQq(event.target.value)}
          placeholder="按用户 QQ 筛选"
          className="rounded-2xl border border-border bg-muted/45 px-4 py-2.5 text-sm outline-none"
        />
        <Select value={decision || "all"} onChange={(next) => setDecision((next === "all" ? "" : next) as typeof decision)} options={[{ value: "all", label: "全部决定" }, { value: "denied", label: "拒绝" }, { value: "allowed", label: "允许" }]} aria-label="访问决定" />
        <Select value={dataClass || "all"} onChange={(next) => setDataClass(next === "all" ? "" : next)} options={[{ value: "all", label: "全部数据类别" }, { value: "message_content", label: "聊天内容" }, { value: "qzone_content", label: "QQ 空间资料" }]} aria-label="数据类别" />
        <button
          type="submit"
          className="flex items-center justify-center gap-2 rounded-md bg-primary px-5 py-2.5 text-sm text-primary-foreground"
        >
          <Search size={16} />
          筛选
        </button>
      </form>

      <PageSplit className="gap-4 xl:grid-cols-[1.25fr_.75fr]">
        <SurfaceCard fill>
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-medium tracking-wide text-muted-foreground">
                最近访问决定
              </p>
              <h2 className="mt-2 text-xl font-semibold text-foreground">
                策略门记录
              </h2>
            </div>
            <span className="ych-pill">最多显示 100 条</span>
          </div>
          {audit?.items.length ? (
            <ul className="mt-5 divide-y divide-black/5">
              {audit.items.map((item) => {
                const reason = String(item.policy.reason ?? "—");
                return (
                  <li
                    key={item.id}
                    className="grid gap-3 py-4 sm:grid-cols-[130px_1fr_auto]"
                  >
                    <div>
                      <span
                        className={`rounded-md px-3 py-1 text-xs font-medium ${item.decision === "allowed" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"}`}
                      >
                        {item.decision === "allowed" ? "允许" : "拒绝"}
                      </span>
                      <p className="mt-2 text-xs text-muted-foreground">
                        {fmt(item.created_at)}
                      </p>
                    </div>
                    <div>
                      <p className="text-sm font-medium text-foreground">
                        {DATA_CLASS_LABELS[item.data_class] ?? item.data_class}{" "}
                        · {peerTitle(item.user_qq, labels.user(item.user_qq))}
                      </p>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">
                        {PURPOSE_LABELS[item.purpose] ?? item.purpose} ·{" "}
                        {REASON_LABELS[reason] ?? reason}
                      </p>
                    </div>
                    <p className="max-w-52 truncate text-right text-xs text-muted-foreground">
                      {item.accessor}
                    </p>
                  </li>
                );
              })}
            </ul>
          ) : (
            <EmptyState tone="inline" className="py-10" title="当前筛选条件下没有访问记录。" />
          )}
        </SurfaceCard>

        <PageColumn className="gap-4">
          <SurfaceCard>
            <p className="text-xs font-medium tracking-wide text-muted-foreground">
              隐私任务
            </p>
            <h2 className="mt-2 text-xl font-semibold text-foreground">
              导出与删除备份
            </h2>
            {requests.length ? (
              <ul className="mt-4 space-y-3">
                {requests.map((item) => (
                  <li
                    key={item.id}
                    className="rounded-2xl bg-muted/45 p-4"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          {REQUEST_LABELS[item.request_kind] ??
                            item.request_kind}{" "}
                          · {item.user_qq}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {STATUS_LABELS[item.status] ?? item.status} ·{" "}
                          {fmt(item.created_at)}
                        </p>
                      </div>
                      {item.status === "completed" ? (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => verify(item.id)}
                          className="rounded-md bg-primary px-3 py-2 text-xs text-primary-foreground disabled:opacity-60"
                        >
                          校验文件
                        </button>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState tone="inline" className="py-8" title="还没有隐私任务。" />
            )}
          </SurfaceCard>

          {verification ? (
            <SurfaceCard tone={verification.verified ? "sage" : "surface"}>
              <div className="flex items-center gap-2 text-primary">
                <FileCheck2 size={18} />
                <p className="text-xs font-semibold tracking-[0.14em]">
                  BACKUP VERIFICATION
                </p>
              </div>
              <p className="mt-3 text-xl font-semibold text-foreground">
                {verification.verified ? "文件完整性已通过" : "文件校验未通过"}
              </p>
              <dl className="mt-4 space-y-2 text-sm">
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">结果</dt>
                  <dd className="font-medium text-foreground">
                    {verification.reason}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">类型</dt>
                  <dd className="font-medium text-foreground">
                    {verification.artifact.artifact_type}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">数据行</dt>
                  <dd className="font-medium text-foreground">
                    {verification.bundle?.row_count ?? "—"}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">校验产物</dt>
                  <dd className="font-medium text-foreground">
                    {verification.artifacts?.length ?? 1} 个
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">原文件归档</dt>
                  <dd className="font-medium text-foreground">
                    {verification.artifacts?.some((item) => item.file_archive)
                      ? "已包含并校验"
                      : "无原文件或未归档"}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">一键恢复</dt>
                  <dd className="font-medium text-foreground">不支持</dd>
                </div>
              </dl>
              <p className="mt-4 text-xs leading-5 text-muted-foreground">
                上传原文件存在时会进入独立 ZIP，并逐文件核验大小与
                SHA-256；后续记录仍可能发生 ID
                冲突，隐私墓碑与删除审计也不可逆。需要恢复时必须新建主控审批，只选择性重建已验证数据。
              </p>
            </SurfaceCard>
          ) : null}
        </PageColumn>
      </PageSplit>
      <div className="mt-8 border-t pt-8">
        <OperationalArtifacts />
      </div>
    </PageFrame>
  );
}
