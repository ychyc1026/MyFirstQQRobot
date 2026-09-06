import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { DataTable } from "../components/DataTable";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { PageColumn, PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { Select } from "../components/Select";
import { StatusBadge } from "../components/StatusBadge";
import { SurfaceCard } from "../components/SurfaceCard";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "../components/ui/alert-dialog";
import { Button } from "../components/ui/button";
import { Checkbox } from "../components/ui/checkbox";
import {
  api,
  type QualificationCapability,
  type QualificationDecision,
  type QualificationPreview,
  type QualificationRoute,
  type QualificationRunDetail,
  type QualificationRunSummary,
  type QualificationSuite,
} from "../lib/api";
import { shanghaiDateTime } from "../lib/datetime";
import {
  QUALIFICATION_CAPABILITY_LABELS,
  QUALIFICATION_DECISION_LABELS,
  QUALIFICATION_REASON_LABELS,
  QUALIFICATION_RUN_STATE_LABELS,
  statusLabel,
} from "../lib/labels";

const CAPABILITIES: QualificationCapability[] = ["chat", "vision", "image", "stats"];
const PAGE_SIZE = 10;

function qualificationTone(decision?: QualificationDecision): "configured" | "unqualified" | "stale" | "blocked" | "passed" | "failed" {
  if (!decision) return "unqualified";
  if (decision.status === "stale") return "stale";
  if (decision.status === "blocked") return "blocked";
  if (decision.status === "failed") return "failed";
  if (decision.qualifies) return "passed";
  if (decision.configured) return "configured";
  return "unqualified";
}

export function QualificationPage() {
  const [suites, setSuites] = useState<QualificationSuite[]>([]);
  const [routes, setRoutes] = useState<QualificationRoute[]>([]);
  const [decisions, setDecisions] = useState<QualificationDecision[]>([]);
  const [runs, setRuns] = useState<QualificationRunSummary[]>([]);
  const [runTotal, setRunTotal] = useState(0);
  const [selectedCapability, setSelectedCapability] = useState<QualificationCapability>("chat");
  const [selectedFixtures, setSelectedFixtures] = useState<string[]>([]);
  const [preview, setPreview] = useState<QualificationPreview | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [runDetail, setRunDetail] = useState<QualificationRunDetail | null>(null);
  const [historyCapability, setHistoryCapability] = useState("all");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const suiteByCapability = useMemo(
    () => Object.fromEntries(suites.map((item) => [item.capability, item])),
    [suites],
  );
  const routeByCapability = useMemo(
    () => Object.fromEntries(routes.map((item) => [item.capability, item])),
    [routes],
  );
  const decisionByCapability = useMemo(
    () => Object.fromEntries(decisions.map((item) => [item.capability, item])),
    [decisions],
  );
  const selectedSuite = suiteByCapability[selectedCapability];
  const selectedRoute = routeByCapability[selectedCapability];
  const selectedDecision = decisionByCapability[selectedCapability];

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const offset = (page - 1) * PAGE_SIZE;
      const [suiteBody, routeBody, decisionBody, runBody] = await Promise.all([
        api.qualificationSuites(),
        api.qualificationRoutes(),
        api.qualificationDecisions(),
        api.qualificationRuns({
          capability: historyCapability === "all" ? undefined : historyCapability,
          limit: PAGE_SIZE,
          offset,
        }),
      ]);
      setSuites(suiteBody.items);
      setRoutes(routeBody.items);
      setDecisions(decisionBody.items);
      setRuns(runBody.items);
      setRunTotal(runBody.total);
      const currentSuite = suiteBody.items.find((item) => item.capability === selectedCapability);
      setSelectedFixtures((current) => {
        if (current.length) return current.filter((id) => currentSuite?.fixture_ids.includes(id));
        return currentSuite?.fixture_ids.slice(0, 2) ?? [];
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "模型资格读取失败");
    } finally {
      setLoading(false);
    }
  }, [historyCapability, page, selectedCapability]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadRun = useCallback(async (runId: string) => {
    if (!runId) {
      setRunDetail(null);
      return;
    }
    try {
      setRunDetail(await api.qualificationRunDetail(runId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "鉴定运行详情读取失败");
    }
  }, []);

  const requestPreview = useCallback(async () => {
    if (!selectedFixtures.length) {
      setError("请至少选择一个仓库合成夹具");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const body =
        selectedCapability === "image"
          ? {
              capability: selectedCapability,
              fixture_ids: selectedFixtures,
              max_images: selectedFixtures.length,
            }
          : {
              capability: selectedCapability,
              fixture_ids: selectedFixtures,
              max_input_tokens: 1000,
              max_output_tokens: 500,
            };
      setPreview(await api.previewQualification(body));
    } catch (err) {
      setPreview(null);
      setError(err instanceof Error ? err.message : "鉴定预览失败");
    } finally {
      setBusy(false);
    }
  }, [selectedCapability, selectedFixtures]);

  const confirmPreview = useCallback(async () => {
    if (!preview) return;
    setBusy(true);
    setError("");
    try {
      const run = await api.confirmQualification(preview.confirmation_handle, crypto.randomUUID());
      setPreview(null);
      setConfirmOpen(false);
      await load();
      await loadRun(run.run_id);
    } catch (err) {
      setPreview(null);
      setConfirmOpen(false);
      setError(`${err instanceof Error ? err.message : "鉴定确认失败"}；预览可能已失效，请重新预览。`);
    } finally {
      setBusy(false);
    }
  }, [load, loadRun, preview]);

  const cancelRun = useCallback(async (runId: string) => {
    setBusy(true);
    setError("");
    try {
      await api.cancelQualificationRun(runId);
      await load();
      await loadRun(runId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "取消鉴定失败");
    } finally {
      setBusy(false);
    }
  }, [load, loadRun]);

  const pageCount = Math.max(1, Math.ceil(runTotal / PAGE_SIZE));

  return (
    <PageFrame>
      <PageTitle
        title="模型资格"
        description="对话、识图、文生图和统计是四条独立路由。配置、保护、就绪和鉴定是不同状态；鉴定通过只是证据，不会启用生产或 QQ 外发。"
        badge={
          <button type="button" className="ych-pill" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
            刷新
          </button>
        }
      />
      {error ? (
        <div className="mb-5">
          <ErrorState title="模型资格操作失败" detail={error} retry={() => void load()} />
        </div>
      ) : null}
      {loading && !routes.length ? (
        <SurfaceCard heading={{ title: "正在读取鉴定状态", subtitle: "不会触发模型调用或 QQ 外发。" }}>
          <LoadingState />
        </SurfaceCard>
      ) : null}
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-4">
        {CAPABILITIES.map((capability) => {
          const route = routeByCapability[capability];
          const decision = decisionByCapability[capability];
          const tone = qualificationTone(decision);
          const selected = selectedCapability === capability;
          return (
            <button
              key={capability}
              type="button"
              onClick={() => {
                setSelectedCapability(capability);
                setPreview(null);
                const nextSuite = suiteByCapability[capability];
                setSelectedFixtures(nextSuite?.fixture_ids.slice(0, 2) ?? []);
              }}
              className="min-h-0 text-left"
            >
              <SurfaceCard
                fill
                tone={selected ? "orange" : "surface"}
                heading={{
                  title: statusLabel(capability, QUALIFICATION_CAPABILITY_LABELS),
                  subtitle: route?.model_identifier || "未配置模型",
                }}
              >
                <div className="flex flex-1 flex-col gap-3">
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge value={route?.configured ? "已配置" : "未配置"} />
                    <StatusBadge value={route?.route_enabled ? "路由已开" : "路由关闭"} />
                    <StatusBadge value={route?.network_enabled ? "网络已开" : "网络关闭"} />
                    <StatusBadge value={statusLabel(decision?.status, QUALIFICATION_DECISION_LABELS, "未鉴定")} />
                  </div>
                  <p className="text-sm text-muted-foreground">
                    {tone === "configured"
                      ? "已配置但未鉴定，不能当作就绪或生产启用。"
                      : tone === "passed"
                        ? "鉴定通过只是证据，不会启用生产。"
                        : tone === "stale"
                          ? "先前证据已过期，当前路由仍未鉴定。"
                          : "默认拒绝。配置或就绪都不是鉴定通过。"}
                  </p>
                  <p className="mt-auto text-xs text-muted-foreground">
                    主机 {route?.sanitized_base_host ?? "unconfigured.local"}
                  </p>
                </div>
              </SurfaceCard>
            </button>
          );
        })}
      </div>
      <PageSplit className="mt-5 flex-none lg:grid-cols-2">
        <PageColumn>
          <SurfaceCard
            fill
            heading={{
              title: `${statusLabel(selectedCapability, QUALIFICATION_CAPABILITY_LABELS)}鉴定`,
              subtitle: selectedSuite
                ? `${selectedSuite.suite_id} · ${selectedSuite.version}`
                : "尚未载入合成套件",
            }}
          >
            {!selectedSuite ? (
              <EmptyState title="没有套件" detail="鉴定只能使用仓库内的合成夹具。" />
            ) : (
              <div className="flex min-h-0 flex-1 flex-col gap-4">
                <p className="text-sm text-muted-foreground">
                  当前决定：{statusLabel(selectedDecision?.status, QUALIFICATION_DECISION_LABELS, "未鉴定")}
                  。生产启用：否。隔离：不读用户资料、不写 outbox、不发 QQ。
                </p>
                {selectedDecision?.blocker_codes.length ? (
                  <div className="flex flex-wrap gap-2">
                    {selectedDecision.blocker_codes.map((code) => (
                      <StatusBadge key={code} value={statusLabel(code, QUALIFICATION_REASON_LABELS)} />
                    ))}
                  </div>
                ) : null}
                <div className="space-y-2">
                  <p className="text-sm font-semibold">合成夹具</p>
                  {selectedSuite.fixture_ids.map((fixtureId) => (
                    <label key={fixtureId} className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={selectedFixtures.includes(fixtureId)}
                        onCheckedChange={(checked) => {
                          setSelectedFixtures((current) =>
                            checked
                              ? [...current, fixtureId]
                              : current.filter((item) => item !== fixtureId),
                          );
                          setPreview(null);
                        }}
                      />
                      <span>{fixtureId}</span>
                    </label>
                  ))}
                </div>
                <div className="mt-auto flex flex-wrap gap-2">
                  <Button type="button" disabled={busy || !selectedRoute?.configured} onClick={() => void requestPreview()}>
                    预览受控实跑
                  </Button>
                  <AlertDialog open={confirmOpen} onOpenChange={(open) => (busy ? undefined : setConfirmOpen(open))}>
                    <AlertDialogTrigger asChild>
                      <Button type="button" variant="outline" disabled={!preview || busy}>
                        确认付费调用
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>确认一次受控模型调用</AlertDialogTitle>
                        <AlertDialogDescription asChild>
                          <div className="space-y-2 text-left">
                            <p>这会按预览上限对 {statusLabel(preview?.capability, QUALIFICATION_CAPABILITY_LABELS)} 发起付费模型请求。</p>
                            <p>夹具 {preview?.fixture_count ?? 0} 个，最多 {preview?.max_requests ?? 0} 次请求。</p>
                            <p>
                              保守费用上界 {preview?.conservative_max_cost ?? "—"} {preview?.currency ?? ""}，过期{" "}
                              {preview ? shanghaiDateTime(preview.expires_at) : "—"}。
                            </p>
                            <p>不会发送 QQ、不会写 outbox、不会启用该路由的生产开关。客户端不能提交通过决定。</p>
                          </div>
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel disabled={busy}>返回</AlertDialogCancel>
                        <AlertDialogAction disabled={busy || !preview} onClick={() => void confirmPreview()}>
                          确认调用
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
                {preview ? (
                  <div className="rounded-md border p-3 text-sm">
                    <p>夹具 {preview.fixture_count} 个，最多 {preview.max_requests} 次请求</p>
                    <p>
                      上限：输入 {preview.max_input_tokens ?? "—"} / 输出 {preview.max_output_tokens ?? "—"} / 图片{" "}
                      {preview.max_images ?? "—"}
                    </p>
                    <p>
                      保守费用上界 {preview.conservative_max_cost} {preview.currency}
                    </p>
                    <p>过期 {shanghaiDateTime(preview.expires_at)}</p>
                    <p>隔离 {preview.effect_isolation} · 不会启用生产</p>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">先预览才能确认。确认码不会出现在列表或主人命令里。</p>
                )}
              </div>
            )}
          </SurfaceCard>
        </PageColumn>
        <PageColumn>
          <SurfaceCard
            fill
            heading={{
              title: "选中运行证据",
              subtitle: runDetail ? runDetail.run_id : "从下方历史选择一条运行",
              action:
                runDetail && (runDetail.state === "prepared" || runDetail.state === "running") ? (
                  <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void cancelRun(runDetail.run_id)}>
                    取消运行
                  </Button>
                ) : undefined,
            }}
          >
            {!runDetail ? (
              <EmptyState title="尚未选择运行" detail="鉴定历史在下方全宽表格里，不会嵌套整页滚动。" />
            ) : (
              <div className="flex min-h-0 flex-1 flex-col gap-3">
                <div className="flex flex-wrap gap-2">
                  <StatusBadge value={statusLabel(runDetail.state, QUALIFICATION_RUN_STATE_LABELS)} />
                  <StatusBadge value={statusLabel(runDetail.capability, QUALIFICATION_CAPABILITY_LABELS)} />
                  <StatusBadge value="不会启用生产" />
                </div>
                <p className="text-sm text-muted-foreground">
                  关联 {runDetail.correlation_id || "—"} · {shanghaiDateTime(runDetail.created_at)}
                </p>
                <div className="min-h-0 flex-1 overflow-x-auto">
                  <DataTable
                    rows={runDetail.cases}
                    rowKey={(row) => row.case_id}
                    empty={<EmptyState title="还没有用例证据" detail="确认后才会创建用例，执行前保持待处理。" />}
                    columns={[
                      { key: "fixture", label: "夹具", render: (row) => row.fixture_id },
                      {
                        key: "state",
                        label: "状态",
                        render: (row) => statusLabel(row.state, QUALIFICATION_RUN_STATE_LABELS),
                      },
                      {
                        key: "reason",
                        label: "原因",
                        render: (row) => statusLabel(row.reason_code ?? undefined, QUALIFICATION_REASON_LABELS, "—"),
                      },
                      {
                        key: "tokens",
                        label: "用量",
                        render: (row) =>
                          `${row.evidence.input_tokens ?? "—"} / ${row.evidence.output_tokens ?? "—"} / ${row.evidence.image_count}`,
                      },
                    ]}
                  />
                </div>
              </div>
            )}
          </SurfaceCard>
        </PageColumn>
      </PageSplit>
      <SurfaceCard
        className="mt-5"
        heading={{
          title: "鉴定历史",
          subtitle: "全宽分页列表。筛选不会把一条路由的通过当成另一条路由的通过。",
          action: (
            <Select
              aria-label="按能力筛选鉴定历史"
              size="pill"
              value={historyCapability}
              onChange={(value) => {
                setHistoryCapability(value);
                setPage(1);
              }}
              options={[
                { value: "all", label: "全部能力" },
                ...CAPABILITIES.map((capability) => ({
                  value: capability,
                  label: statusLabel(capability, QUALIFICATION_CAPABILITY_LABELS),
                })),
              ]}
            />
          ),
        }}
      >
        <DataTable
          rows={runs}
          rowKey={(row) => row.run_id}
          page={page}
          pageCount={pageCount}
          onPageChange={setPage}
          empty={<EmptyState title="还没有鉴定运行" detail="预览并确认后才会出现历史。空列表不是通过。" />}
          columns={[
            {
              key: "capability",
              label: "能力",
              render: (row) => (
                <button type="button" className="text-left underline-offset-2 hover:underline" onClick={() => void loadRun(row.run_id)}>
                  {statusLabel(row.capability, QUALIFICATION_CAPABILITY_LABELS)}
                </button>
              ),
            },
            {
              key: "state",
              label: "状态",
              render: (row) => statusLabel(row.state, QUALIFICATION_RUN_STATE_LABELS),
            },
            { key: "model", label: "模型", render: (row) => row.model_identifier },
            { key: "suite", label: "套件", render: (row) => row.suite_version },
            { key: "created", label: "创建", render: (row) => shanghaiDateTime(row.created_at) },
            { key: "correlation", label: "关联", render: (row) => row.correlation_id.slice(0, 8) || "—" },
          ]}
        />
      </SurfaceCard>
    </PageFrame>
  );
}
