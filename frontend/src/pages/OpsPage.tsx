import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { IdentityLock } from "../components/IdentityLock";
import { EmptyState } from "../components/EmptyState";
import { PageColumn, PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { StatBlock } from "../components/StatBlock";
import { SurfaceCard } from "../components/SurfaceCard";
import { TrendCard } from "../components/TrendCard";
import { Badge } from "../components/ui/badge";
import {
  api,
  type ActivityItem,
  type Identity,
  type OpsSnapshot,
  type ProtectionSnapshot,
  type SeriesPoint,
} from "../lib/api";
import { activityActionLabel, activityDetailLabel, activitySourceLabel } from "../lib/labels";

function LaneCard({
  title,
  lane,
  openLabels,
}: {
  title: string;
  lane: Record<string, string | number>;
  openLabels: [string, string][];
}) {
  const closed = lane.state === "closed";
  return (
    <SurfaceCard emphasis="compact">
      <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-muted-foreground">{title}</p>
      {closed ? (
        <p className="mt-2 text-xl font-semibold text-foreground">关闭</p>
      ) : (
        <dl className="mt-2 space-y-1.5 text-xs">
          {openLabels.map(([key, label]) => (
            <div key={key} className="flex justify-between">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="font-medium text-foreground">{lane[key] ?? 0}</dd>
            </div>
          ))}
        </dl>
      )}
    </SurfaceCard>
  );
}

function ProtectionFold({
  chat,
  image,
}: {
  chat?: ProtectionSnapshot;
  image?: ProtectionSnapshot;
}) {
  const [open, setOpen] = useState(false);
  return (
    <SurfaceCard emphasis="compact">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 text-left"
        onClick={() => setOpen((value) => !value)}
      >
        <div>
          <p className="text-sm font-semibold text-foreground/80">模型用量</p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">未配置时只显示状态，不画空饼图</p>
        </div>
        <span className="shrink-0 text-xs text-muted-foreground">{open ? "收起" : "展开"}</span>
      </button>
      {open ? (
        <div className="mt-5 grid grid-cols-2 gap-4 text-sm">
          {[
            { label: "聊天", snap: chat, showTokens: true },
            { label: "图片", snap: image, showTokens: false },
          ].map((item) => (
              <div key={item.label} className="rounded-2xl bg-muted/60 p-4">
                <p className="font-medium text-foreground">{item.label}</p>
                <p className="mt-2 text-muted-foreground">
                  熔断 {item.snap?.state === "open" ? "打开" : "关闭"} · 当日{" "}
                  {item.snap?.requests_used ?? 0}/{item.snap?.request_limit ?? 0}
                </p>
                {item.showTokens ? (
                  <p className="mt-1 text-muted-foreground">
                    token {item.snap?.tokens_used ?? 0}/{item.snap?.token_limit ?? 0}
                  </p>
                ) : null}
              </div>
            ))}
        </div>
      ) : null}
    </SurfaceCard>
  );
}

export function OpsPage() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [snapshot, setSnapshot] = useState<OpsSnapshot | null>(null);
  const [points, setPoints] = useState<SeriesPoint[]>([]);
  const [days, setDays] = useState<1 | 7 | 30>(7);
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [chat, setChat] = useState<ProtectionSnapshot>();
  const [image, setImage] = useState<ProtectionSnapshot>();
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.identity(),
      api.opsSnapshot(),
      api.opsActivity(),
      api.protection(),
    ])
      .then(([identityBody, snapshotBody, activityBody, protectionBody]) => {
        if (cancelled) return;
        setIdentity(identityBody);
        setSnapshot(snapshotBody);
        setActivity(activityBody.items);
        setChat(protectionBody.chat);
        setImage(protectionBody.image);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .opsSeries(days)
      .then((body) => {
        if (!cancelled) setPoints(body.points);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  return (
    <PageFrame>
      <PageTitle
        title="近况"
        description="人和程序刚才做了什么。关闭的能力显示「关闭」，不用 0 代替。"
        badge={
          <span className="ych-pill">
            {snapshot?.onebot.connected ? "OneBot 已连接" : "OneBot 未连接"}
          </span>
        }
      />
      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}
      <PageSplit className="gap-4 xl:grid-cols-[minmax(0,1.8fr)_minmax(280px,1fr)]">
        <PageColumn className="gap-4">
          <div className="grid grid-cols-3 gap-4 max-md:grid-cols-1">
            <StatBlock
              label="待你处理"
              value={snapshot?.attention.needs_owner ?? 0}
              hint={`审批 ${snapshot?.attention.pending_approvals ?? 0} · 紧急汇报 ${snapshot?.attention.urgent_reports ?? 0}`}
            />
            <StatBlock
              label="今日入库"
              value={snapshot?.inbound_today ?? 0}
              hint={snapshot?.day ? `${snapshot.day} · ${snapshot.timezone}` : "Asia/Shanghai"}
            />
            <StatBlock
              label="异常"
              value={snapshot?.anomalies.total ?? 0}
              hint="空间不确定 / 外发失败 / 熔断 / 汇报投递失败"
            />
          </div>
          <TrendCard points={points} days={days} onDaysChange={setDays} />
          <div className="grid grid-cols-3 gap-4 max-md:grid-cols-1">
            <LaneCard
              title="影子"
              lane={snapshot?.lanes.shadow ?? { state: "closed" }}
              openLabels={[
                ["completed", "成功"],
                ["failed", "失败"],
                ["skipped", "跳过"],
              ]}
            />
            <LaneCard
              title="外发"
              lane={snapshot?.lanes.outbound ?? { state: "closed" }}
              openLabels={[
                ["pending", "待发"],
                ["failed", "失败"],
              ]}
            />
            <LaneCard
              title="空间"
              lane={snapshot?.lanes.qzone ?? { state: "closed" }}
              openLabels={[
                ["publishing", "发布中"],
                ["uncertain", "不确定"],
                ["pending_delete", "待删"],
              ]}
            />
          </div>
          <ProtectionFold chat={chat} image={image} />
        </PageColumn>
        <PageColumn className="gap-4">
          <IdentityLock identity={identity} />
          <SurfaceCard>
            <p className="text-xs font-medium tracking-wide text-muted-foreground">Worker</p>
            <ul className="mt-4 space-y-3 text-sm">
              {(snapshot?.workers ?? []).map((worker) => (
                <li key={worker.key} className="flex items-center justify-between">
                  <span className="text-foreground">{worker.name}</span>
                  <span
                    className={`rounded-md px-2.5 py-1 text-xs ${
                      worker.state === "运行中"
                        ? "bg-emerald-50 text-emerald-800"
                        : worker.state === "已暂停"
                          ? "bg-amber-50 text-amber-800"
                          : "bg-black/5 text-muted-foreground"
                    }`}
                  >
                    {worker.state}
                  </span>
                </li>
              ))}
            </ul>
          </SurfaceCard>
          <SurfaceCard>
            <div className="flex items-center justify-between gap-3">
              <div><p className="text-sm font-semibold text-foreground">最近操作摘要</p><p className="mt-1 text-xs text-muted-foreground">审计、主号命令与汇报的最近变化</p></div>
              <Link to="/activity" className="text-xs font-medium text-primary hover:underline">查看全部</Link>
            </div>
            <ul className="mt-4 space-y-3">
              {activity.length === 0 ? (
                <li>
                  <EmptyState tone="inline" className="py-0" title="还没有操作记录" />
                </li>
              ) : (
                activity.slice(0, 3).map((item) => (
                  <li key={`${item.source}-${item.at}-${item.title}`} className="rounded-md border bg-muted/20 p-3">
                    <Link to={item.href} className="block hover:opacity-80">
                      <div className="flex items-start justify-between gap-3"><p className="text-sm font-medium text-foreground">{activityActionLabel(item.title)}</p><Badge variant="outline" className="shrink-0">{activitySourceLabel(item.source)}</Badge></div>
                      <p className="mt-1 text-xs text-muted-foreground">{activityDetailLabel(item.detail)}</p>
                      <p className="mt-1 text-[11px] text-muted-foreground">{item.at.slice(0, 16).replace("T", " ")}</p>
                    </Link>
                  </li>
                ))
              )}
            </ul>
          </SurfaceCard>
        </PageColumn>
      </PageSplit>
    </PageFrame>
  );
}
