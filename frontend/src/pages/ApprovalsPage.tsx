import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { DangerCard } from "../components/DangerCard";
import { EmptyState } from "../components/EmptyState";
import { PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { SegmentSlider } from "../components/SegmentSlider";
import { SurfaceCard } from "../components/SurfaceCard";
import {
  api,
  type ApprovalItem,
  type OwnerReport,
  type ReportSummary,
} from "../lib/api";
import { peerTitle } from "../lib/peerLabels";
import { useOperatorLabels } from "../lib/useOperatorLabels";

const APPROVAL_LABELS: Record<string, string> = {
  "friend_baseline.create": "创建好友基线",
  "history.selected_range": "历史指定范围授权",
  "history.one_time": "历史一次授权",
  "qzone_profile.one_time": "空间资料一次授权",
  "qzone_profile.ttl": "空间资料限期授权",
  "privacy.export": "用户数据导出",
  "privacy.delete": "用户数据删除",
  "image_generation.use": "图片使用",
  "knowledge.preview": "资料预览",
  "proactive_message.schedule": "主动消息",
  "proactive_message.reapprove": "主动消息（过期重批）",
  "qzone.publish": "空间发布",
  "qzone.publish.reapprove": "空间发布（过期重批）",
  "qzone.delete": "空间删除",
};

const SEVERITY_LABELS: Record<string, string> = {
  action_required: "待你处理",
  critical: "紧急",
  warning: "警告",
  info: "信息",
};

function isPublishApproval(requestType: string) {
  return requestType.startsWith("qzone.");
}

function approvalTitle(item: ApprovalItem) {
  return APPROVAL_LABELS[item.request_type] ?? item.request_type;
}

function approvalScope(
  item: ApprovalItem,
  labels?: { user: (id: string) => string },
) {
  const payload = item.payload ?? {};
  const rows: Array<[string, string]> = [];
  if (payload.user_qq) {
    const qq = String(payload.user_qq);
    const name = labels?.user(qq);
    rows.push(["用户", name ? `${peerTitle(qq, name)} · ${qq}` : qq]);
  }
  if (payload.selected_from)
    rows.push(["开始日期", String(payload.selected_from)]);
  if (payload.selected_to) rows.push(["结束日期", String(payload.selected_to)]);
  if (payload.max_messages)
    rows.push(["最多消息", `${String(payload.max_messages)} 条`]);
  if (payload.max_items)
    rows.push(["最多动态", `${String(payload.max_items)} 条`]);
  if (payload.days) rows.push(["有效期", `${String(payload.days)} 天`]);
  return rows;
}

export function ApprovalsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") === "report" ? "report" : "approval";
  const selectedId = searchParams.get("id");
  const labels = useOperatorLabels();
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [reports, setReports] = useState<OwnerReport[]>([]);
  const [reportDetail, setReportDetail] = useState<OwnerReport | null>(null);
  const [summary, setSummary] = useState<ReportSummary | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function reload() {
    const [approvalBody, reportBody, summaryBody] = await Promise.all([
      api.approvals(),
      api.reports("pending"),
      api.reportSummary(),
    ]);
    setApprovals(approvalBody.items);
    setReports(reportBody.items);
    setSummary(summaryBody);
  }

  useEffect(() => {
    reload().catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    if (tab !== "report" || !selectedId) {
      setReportDetail(null);
      return;
    }
    let cancelled = false;
    api
      .reportDetail(selectedId)
      .then((detail) => {
        if (!cancelled) setReportDetail(detail);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [tab, selectedId]);

  const selectedApproval = useMemo(
    () => approvals.find((item) => item.id === selectedId) ?? null,
    [approvals, selectedId],
  );
  const selectedReport = useMemo(
    () => reports.find((item) => item.id === selectedId) ?? reportDetail,
    [reports, selectedId, reportDetail],
  );

  function select(nextTab: "approval" | "report", id?: string) {
    const next = new URLSearchParams();
    next.set("tab", nextTab);
    if (id) next.set("id", id);
    setSearchParams(next, { replace: true });
  }

  async function decideApproval(
    action: "approval.approve" | "approval.reject",
  ) {
    if (!selectedApproval) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.runCommand(action, {
        approval_code: selectedApproval.approval_code,
      });
      const ok =
        action === "approval.approve"
          ? result.data.approved === true
          : result.data.rejected === true;
      if (!ok) {
        setError(String(result.data.reason ?? "操作未生效"));
        return;
      }
      await reload();
      select("approval");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法处理审批");
    } finally {
      setBusy(false);
    }
  }

  async function ackReport() {
    if (!selectedReport) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.runCommand("report.ack", {
        report_id: selectedReport.id,
      });
      if (result.data.acknowledged !== true) {
        setError("这条汇报已经处理过，或找不到。");
        return;
      }
      await reload();
      select("report");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法标记已读");
    } finally {
      setBusy(false);
    }
  }

  const queueEmpty =
    tab === "approval" ? approvals.length === 0 : reports.length === 0;

  return (
    <PageFrame>
      <PageTitle
        title="汇报与审批"
        description="与主号同一状态机。确认不会立刻发 QQ；外发总开关关闭时只改状态。"
        badge={
          <span className="ych-pill">
            待审批 {approvals.length} · 待读 {reports.length}
          </span>
        }
      />
      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}
      {summary ? (
        <p className="mb-4 text-xs text-muted-foreground">
          汇报投递 {summary.delivery_route_enabled ? "开" : "关闭"} · 外发{" "}
          {summary.outbound_enabled ? "开" : "关闭"} · 投递{" "}
          {summary.worker.configured_enabled
            ? summary.worker.active
              ? "运行中"
              : "已暂停"
            : "未启用"}
        </p>
      ) : null}
      <PageSplit className="gap-4 lg:grid-cols-2">
        <SurfaceCard fill>
          <div className="mb-4">
            <SegmentSlider
              value={tab}
              onChange={(next) =>
                select(
                  next,
                  next === "approval" ? approvals[0]?.id : reports[0]?.id,
                )
              }
              options={[
                { value: "approval", label: `待审批 ${approvals.length}` },
                { value: "report", label: `待读汇报 ${reports.length}` },
              ]}
            />
          </div>
          {queueEmpty ? (
            <EmptyState
              tone="inline"
              className="py-8"
              title={tab === "approval" ? "没有待审批事项。" : "没有未读汇报。"}
            />
          ) : (
            <ul className="divide-y divide-black/5">
              {tab === "approval"
                ? approvals.map((item) => (
                    <li key={item.id}>
                      <button
                        type="button"
                        className={`flex w-full items-center justify-between gap-3 rounded-2xl px-2 py-3 text-left transition hover:bg-muted/60 ${
                          selectedId === item.id ? "bg-muted/70" : ""
                        }`}
                        onClick={() => select("approval", item.id)}
                      >
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium text-foreground">
                            {approvalTitle(item)}
                          </p>
                          <p className="mt-1 text-xs text-muted-foreground">
                            {item.approval_code} · {item.subject_id}
                          </p>
                        </div>
                        <span className="shrink-0 rounded-md bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                          待审批
                        </span>
                      </button>
                    </li>
                  ))
                : reports.map((item) => (
                    <li key={item.id}>
                      <button
                        type="button"
                        className={`flex w-full items-center justify-between gap-3 rounded-2xl px-2 py-3 text-left transition hover:bg-muted/60 ${
                          selectedId === item.id ? "bg-muted/70" : ""
                        }`}
                        onClick={() => select("report", item.id)}
                      >
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium text-foreground">
                            {item.title}
                          </p>
                          <p className="mt-1 text-xs text-muted-foreground">
                            {item.category}
                            {item.related_id ? ` · ${item.related_id}` : ""}
                          </p>
                        </div>
                        <span className="shrink-0 rounded-md bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                          {SEVERITY_LABELS[item.severity] ?? item.severity}
                        </span>
                      </button>
                    </li>
                  ))}
            </ul>
          )}
        </SurfaceCard>
        <SurfaceCard fill>
          {tab === "approval" ? (
            selectedApproval ? (
              <div>
                <p className="text-xs font-medium tracking-wide text-muted-foreground">
                  审批详情
                </p>
                <h2 className="mt-3 text-2xl font-semibold text-foreground">
                  {approvalTitle(selectedApproval)}
                </h2>
                <dl className="mt-4 space-y-2 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">确认码</dt>
                    <dd className="font-medium text-foreground">
                      {selectedApproval.approval_code}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">对象</dt>
                    <dd className="font-medium text-foreground">
                      {selectedApproval.subject_id}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">类型</dt>
                    <dd className="font-medium text-foreground">
                      {selectedApproval.request_type}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">过期</dt>
                    <dd className="font-medium text-foreground">
                      {selectedApproval.expires_at}
                    </dd>
                  </div>
                </dl>
                {approvalScope(selectedApproval, labels).length ? (
                  <div className="mt-5 rounded-2xl bg-muted/60 p-4">
                    <p className="text-xs font-medium tracking-wide text-muted-foreground">
                      本次确认范围
                    </p>
                    <dl className="mt-3 space-y-2 text-sm">
                      {approvalScope(selectedApproval, labels).map(([label, value]) => (
                        <div key={label} className="flex justify-between gap-3">
                          <dt className="text-muted-foreground">{label}</dt>
                          <dd className="font-medium text-foreground">{value}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                ) : null}
                {isPublishApproval(selectedApproval.request_type) ? (
                  <DangerCard className="mt-5">
                    <p className="text-sm font-medium text-red-800">
                      这是空间类审批
                    </p>
                    <p className="mt-2 text-sm leading-6 text-red-700/90">
                      确认只推进状态机。空间发布和外发总开关关闭时，不会真的发
                      QQ 或改说说。
                    </p>
                  </DangerCard>
                ) : (
                  <p className="mt-5 text-sm leading-6 text-muted-foreground">
                    确认与主号 `/确认`
                    相同。授权类审批只改变访问策略，不会在这里直接读取聊天记录或
                    QQ 空间。
                  </p>
                )}
                <div className="mt-6 flex gap-3">
                  <button
                    type="button"
                    disabled={busy}
                    className="rounded-md bg-primary px-5 py-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
                    onClick={() => decideApproval("approval.approve")}
                  >
                    确认
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    className="rounded-md bg-muted px-5 py-3 text-sm font-medium text-foreground disabled:opacity-60"
                    onClick={() => decideApproval("approval.reject")}
                  >
                    拒绝
                  </button>
                </div>
              </div>
            ) : (
              <EmptyState tone="select" title="从左侧选一条审批。" />
            )
          ) : selectedReport ? (
            <div>
              <p className="text-xs font-medium tracking-wide text-muted-foreground">
                汇报详情
              </p>
              <h2 className="mt-3 text-2xl font-semibold text-foreground">
                {selectedReport.title}
              </h2>
              <p className="mt-2 text-xs text-muted-foreground">
                {SEVERITY_LABELS[selectedReport.severity] ??
                  selectedReport.severity}{" "}
                · {selectedReport.category}
              </p>
              <p className="mt-4 whitespace-pre-wrap text-sm leading-6 text-foreground">
                {(reportDetail ?? selectedReport).body || "没有正文。"}
              </p>
              <dl className="mt-4 space-y-2 text-sm">
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">状态</dt>
                  <dd className="font-medium text-foreground">
                    {selectedReport.status}
                  </dd>
                </div>
                {(reportDetail ?? selectedReport).delivery_status ? (
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">投递</dt>
                    <dd className="font-medium text-foreground">
                      {(reportDetail ?? selectedReport).delivery_status}
                    </dd>
                  </div>
                ) : null}
                {selectedReport.related_id ? (
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">关联</dt>
                    <dd className="font-medium text-foreground">
                      {selectedReport.related_type} ·{" "}
                      {selectedReport.related_id}
                    </dd>
                  </div>
                ) : null}
              </dl>
              <p className="mt-5 text-sm leading-6 text-muted-foreground">
                标为已读与主号 `/汇报 已读` 相同。已经排队外发的不会因此撤回。
              </p>
              <button
                type="button"
                disabled={busy || selectedReport.status === "acknowledged"}
                className="mt-6 rounded-md bg-primary px-5 py-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
                onClick={ackReport}
              >
                标为已读
              </button>
            </div>
          ) : (
            <EmptyState tone="select" title="从左侧选一条汇报。" />
          )}
        </SurfaceCard>
      </PageSplit>
    </PageFrame>
  );
}
