import { type FormEvent, useEffect, useMemo, useState } from "react";
import {
  Fingerprint,
  History,
  LockKeyhole,
  ShieldCheck,
  Users,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { DangerCard } from "../components/DangerCard";
import { DatePicker } from "../components/DatePicker";
import { EmptyState } from "../components/EmptyState";
import { PageColumn, PageFrame, PageSplit } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { SurfaceCard } from "../components/SurfaceCard";
import {
  api,
  type ControlCommandResult,
  type KnownUser,
  type UserDetail,
  type UserIdentitySummary,
  type UserReplyStyle,
} from "../lib/api";
import { peerSubtitle, peerTitle } from "../lib/peerLabels";

const FRIEND_LABELS: Record<string, string> = {
  unknown: "未知",
  existing_friend: "旧友",
  new_friend: "新友",
  not_friend: "非好友",
};

const HISTORY_LABELS: Record<string, string> = {
  deny: "禁止",
  file_import_only: "仅文件导入",
  selected_range: "指定范围",
  one_time: "一次授权",
};

const QZONE_LABELS: Record<string, string> = {
  deny: "禁止",
  one_time: "一次授权",
  ttl: "限期授权",
};

const SOURCE_LABELS: Record<string, string> = {
  insufficient_evidence: "证据不足",
  baseline_snapshot: "好友基线",
  friend_add_after_baseline: "基线后的新增好友事件",
  friend_add_seen_before_baseline: "基线前看到新增事件",
  owner_override: "主控手动标记",
  owner_override_released: "已恢复证据自动判定",
};

const EVIDENCE_LABELS: Record<string, string> = {
  baseline_snapshot: "好友基线记录",
  friend_add_event: "新增好友事件",
  message_observed: "消息观察（不用于判断新友）",
  owner_override: "主控手动标记",
  imported_profile: "导入资料",
};

function fmt(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", { hour12: false });
}

function policyDetail(detail: UserDetail) {
  const policy = detail.history_policy;
  if (policy.mode === "one_time") {
    return `剩余 ${policy.one_time_remaining ?? 0} 次 · 单次最多 ${policy.max_messages ?? 0} 条`;
  }
  if (policy.mode === "selected_range") {
    return `${policy.selected_from ?? "—"} 至 ${policy.selected_to ?? "—"} · 最多 ${policy.max_messages ?? 0} 条`;
  }
  if (policy.mode === "file_import_only")
    return "只能由主控上传文件，不调用 NapCat 历史接口";
  return "没有明确授权时，读取请求会在网络调用前被拒绝";
}

function qzonePolicyDetail(detail: UserDetail) {
  const policy = detail.qzone_profile_policy;
  if (policy.mode === "one_time") {
    return `剩余 ${policy.one_time_remaining ?? 0} 次 · 最多 ${policy.max_items ?? 0} 条`;
  }
  if (policy.mode === "ttl") {
    return `最多 ${policy.max_items ?? 0} 条 · 到期 ${fmt(policy.expires_at)}`;
  }
  return "好友身份不等于空间访问授权";
}

export function UsersPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedQq = searchParams.get("qq") ?? "";
  const [items, setItems] = useState<KnownUser[]>([]);
  const [summary, setSummary] = useState<UserIdentitySummary | null>(null);
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [lookup, setLookup] = useState(selectedQq);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [style, setStyle] = useState<UserReplyStyle | null>(null);
  const [historyCount, setHistoryCount] = useState(50);
  const [rangeFrom, setRangeFrom] = useState("");
  const [rangeTo, setRangeTo] = useState("");
  const [profileItems, setProfileItems] = useState(10);
  const [profileDays, setProfileDays] = useState(7);
  const [labelDraft, setLabelDraft] = useState("");

  async function reload(qq = selectedQq) {
    const listed = await api.users();
    setItems(listed.items);
    setSummary(listed.summary);
    if (!qq) {
      setDetail(null);
      setStyle(null);
      return;
    }
    const [detailBody, styleBody] = await Promise.all([
      api.userDetail(qq),
      api.userReplyStyle(qq),
    ]);
    setDetail(detailBody);
    setStyle(styleBody);
    setLabelDraft(detailBody.operator_label ?? "");
  }

  useEffect(() => {
    reload().catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!selectedQq) {
      setDetail(null);
      setStyle(null);
      return;
    }
    Promise.all([api.userDetail(selectedQq), api.userReplyStyle(selectedQq)])
      .then(([detailBody, styleBody]) => {
        setDetail(detailBody);
        setStyle(styleBody);
        setLabelDraft(detailBody.operator_label ?? "");
      })
      .catch((err: Error) => setError(err.message));
  }, [selectedQq]);

  const selected = useMemo(
    () => items.find((item) => item.user_qq === selectedQq) ?? null,
    [items, selectedQq],
  );
  const visibleItems = useMemo(() => {
    const needle = lookup.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((item) => {
      const label = (item.operator_label ?? "").toLowerCase();
      return item.user_qq.includes(needle) || label.includes(needle);
    });
  }, [items, lookup]);

  function select(qq: string) {
    const next = new URLSearchParams();
    if (qq) next.set("qq", qq);
    setSearchParams(next, { replace: true });
  }

  function openLookup(event: FormEvent) {
    event.preventDefault();
    const raw = lookup.trim();
    if (/^\d+$/.test(raw)) {
      setError("");
      select(raw);
      return;
    }
    const matched = items.filter((item) =>
      (item.operator_label ?? "").toLowerCase().includes(raw.toLowerCase()),
    );
    if (matched.length === 1) {
      setError("");
      select(matched[0].user_qq);
      return;
    }
    setError(matched.length ? "备注对应多个人，请再输入 QQ" : "找不到这个备注，也可直接输入 QQ");
  }

  async function saveLabel() {
    if (!selectedQq) return;
    setBusy(true);
    setError("");
    try {
      await api.setOperatorLabel("user", selectedQq, labelDraft.trim());
      setNotice(labelDraft.trim() ? `已备注 ${labelDraft.trim()}` : "已清除备注");
      await reload(selectedQq);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存备注失败");
    } finally {
      setBusy(false);
    }
  }

  function resultNotice(action: string, result: ControlCommandResult) {
    if (result.status === "pending_approval") {
      const code = String(result.data.approval_code ?? "");
      return code
        ? `已提交审批 ${code}，去「汇报与审批」确认。`
        : "已提交审批。";
    }
    if (action === "user.qzone_profile_preview") {
      if (result.data.allowed !== true)
        return `没有读取：${String(result.data.reason ?? "策略拒绝")}`;
      const summary = (result.data.summary ?? {}) as {
        scanned?: number;
        candidate_count?: number;
        images_seen?: number;
        videos_unread?: number;
      };
      return `空间资料预览完成：扫到 ${Number(summary.scanned ?? 0)} 条，候选 ${Number(summary.candidate_count ?? 0)} 条；主号会收到同一条说明。`;
    }
    return "策略已更新并写入审计记录。";
  }

  async function runUser(action: string, extra: Record<string, string> = {}) {
    if (!selectedQq) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.runCommand(action, {
        user_qq: selectedQq,
        ...extra,
      });
      if (result.status === "failed")
        throw new Error(String(result.data.message ?? "操作失败"));
      setNotice(resultNotice(action, result));
      await reload(selectedQq);
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function previewBaseline() {
    const confirmed = window.confirm(
      "这会从当前 NapCat 读取一次好友 QQ 列表，只暂存数量、QQ 列表和摘要；仍需在审批页再次确认，才会建立旧友基线。继续吗？",
    );
    if (!confirmed) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.runCommand("friend_baseline.preview", {});
      if (result.status === "failed")
        throw new Error(String(result.data.message ?? "好友基线预览失败"));
      const count = Number(result.data.friend_count ?? 0);
      setNotice(
        `已暂存 ${count} 位好友的基线候选；尚未改变任何人的身份，请到审批页确认。`,
      );
      await reload(selectedQq);
    } catch (err) {
      setError(err instanceof Error ? err.message : "好友基线预览失败");
    } finally {
      setBusy(false);
    }
  }

  function previewQzoneProfile() {
    const confirmed = window.confirm(
      "若全局空间采集开关和该用户授权都已开启，这一步会立即访问该用户的 QQ 空间并生成临时候选。继续吗？",
    );
    if (confirmed) void runUser("user.qzone_profile_preview");
  }

  async function saveStyle(event: FormEvent) {
    event.preventDefault();
    if (!selectedQq || !style) return;
    if (style.min_bubbles > style.max_bubbles) {
      setError("最少句数不能大于最多句数");
      return;
    }
    if (style.sentence_min_chars > style.sentence_max_chars) {
      setError("每句最短不能大于最长");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const saved = await api.updateUserReplyStyle(selectedQq, {
        min_bubbles: style.min_bubbles,
        max_bubbles: style.max_bubbles,
        sentence_min_chars: style.sentence_min_chars,
        sentence_max_chars: style.sentence_max_chars,
      });
      setStyle(saved);
      setNotice("已保存回复条数和句长；没有发送 QQ 消息。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存回复风格失败");
    } finally {
      setBusy(false);
    }
  }

  function requestRange(event: FormEvent) {
    event.preventDefault();
    if (!rangeFrom || !rangeTo) {
      setError("请填写开始和结束日期");
      return;
    }
    void runUser("user.history_selected_range", {
      selected_from: rangeFrom,
      selected_to: rangeTo,
      max_messages: String(historyCount),
    });
  }

  const friendState =
    detail?.relationship?.friend_state ?? selected?.friend_state ?? "unknown";
  const frozen = Boolean(
    detail?.relationship?.data_frozen ?? selected?.data_frozen,
  );
  const confidence =
    detail?.relationship?.confidence ?? selected?.confidence ?? 0;

  return (
    <PageFrame>
      <PageTitle
        title="用户身份与隐私"
        description="先有证据，再分类；先有明确授权，再读取。旧友身份不会自动开放聊天历史或 QQ 空间。"
        badge={
          <span className="ych-pill">
            默认拒绝 · 冻结 {summary?.frozen_users ?? 0}
          </span>
        }
      />
      {error ? (
        <p className="mb-4 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="mb-4 rounded-2xl bg-primary/10 px-4 py-3 text-sm text-primary">
          {notice}
        </p>
      ) : null}

      <div className="mb-5 grid grid-cols-1 gap-4 xl:grid-cols-[1.25fr_.75fr]">
        <SurfaceCard className="overflow-hidden">
          <div className="flex items-start justify-between gap-5">
            <div>
              <div className="flex items-center gap-2 text-primary">
                <Fingerprint size={18} />
                <p className="text-xs font-semibold tracking-[0.16em]">
                  EVIDENCE FIRST
                </p>
              </div>
              <h2 className="mt-4 text-2xl font-semibold tracking-tight text-foreground">
                好友基线决定“旧友”的可靠起点
              </h2>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">
                第一次发消息只会留下“见过此人”的证据，不会被猜成新友。基线建立后，只有明确的新增好友事件才会自动标记为新友。
              </p>
            </div>
            <div className="hidden rounded-3xl bg-muted p-4 text-primary sm:block">
              <Users size={28} />
            </div>
          </div>
          <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5">
            <p className="text-sm text-foreground">
              {summary?.latest_baseline
                ? `最近基线：${fmt(summary.latest_baseline.captured_at)} · ${summary.latest_baseline.friend_count} 位`
                : "尚未建立好友基线；所有无充分证据的关系保持未知。"}
            </p>
            <button
              type="button"
              disabled={busy}
              onClick={previewBaseline}
              className="rounded-md bg-primary px-5 py-2.5 text-sm text-primary-foreground disabled:opacity-60"
            >
              读取并暂存基线预览
            </button>
          </div>
        </SurfaceCard>
        <SurfaceCard tone="ink">
          <div className="flex items-center gap-2 text-primary-sand">
            <ShieldCheck size={18} />
            <p className="text-xs font-semibold tracking-[0.16em]">
              PRIVACY GATE
            </p>
          </div>
          <p className="mt-4 text-xl font-semibold">四道门默认关闭</p>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            聊天历史、空间资料、模型使用与派生记忆分别授权。冻结用户会立即阻断新的读取和派生写入。
          </p>
          <div className="mt-5 flex flex-wrap gap-2 text-xs text-muted-foreground">
            <span className="rounded-md bg-white/10 px-3 py-1.5">
              历史：拒绝
            </span>
            <span className="rounded-md bg-white/10 px-3 py-1.5">
              空间：拒绝
            </span>
            <span className="rounded-md bg-white/10 px-3 py-1.5">
              读取有审计
            </span>
          </div>
        </SurfaceCard>
      </div>

      <div className="mb-5 grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[
          ["旧友", summary?.friend_states.existing_friend ?? 0],
          ["新友", summary?.friend_states.new_friend ?? 0],
          ["未知", summary?.friend_states.unknown ?? 0],
          ["非好友", summary?.friend_states.not_friend ?? 0],
        ].map(([label, value]) => (
          <SurfaceCard key={label} padding="p-5">
            <p className="text-xs tracking-wide text-muted-foreground">{label}</p>
            <p className="mt-2 text-3xl font-semibold text-foreground">{value}</p>
          </SurfaceCard>
        ))}
      </div>

      <PageSplit className="gap-4 xl:grid-cols-[.72fr_1.28fr]">
        <SurfaceCard fill>
          <form className="mb-4 flex gap-2" onSubmit={openLookup}>
            <input
              value={lookup}
              onChange={(event) => setLookup(event.target.value)}
              placeholder="搜索备注或 QQ"
              className="min-w-0 flex-1 rounded-2xl border border-border bg-muted/50 px-4 py-3 text-sm outline-none"
            />
            <button
              type="submit"
              className="rounded-md bg-primary px-4 py-3 text-sm text-primary-foreground"
            >
              查看
            </button>
          </form>
          {items.length === 0 ? (
            <EmptyState
              tone="inline"
              className="py-8"
              title="还没有用户记录。"
              detail="输入 QQ 查看不会读取其聊天历史或空间。"
            />
          ) : visibleItems.length === 0 ? (
            <EmptyState tone="inline" className="py-8" title="没有匹配的备注或 QQ。" />
          ) : (
            <ul className="divide-y divide-black/5">
              {visibleItems.map((item) => (
                <li key={item.user_qq}>
                  <button
                    type="button"
                    className={`flex w-full items-center justify-between gap-3 rounded-2xl px-2 py-3 text-left transition hover:bg-muted/60 ${selectedQq === item.user_qq ? "bg-muted/70" : ""}`}
                    onClick={() => select(item.user_qq)}
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">
                        {peerTitle(item.user_qq, item.operator_label)}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {peerSubtitle(item.user_qq, item.operator_label)
                          ? `${peerSubtitle(item.user_qq, item.operator_label)} · `
                          : ""}
                        {HISTORY_LABELS[item.history_mode] ?? item.history_mode}
                        {item.data_frozen ? " · 已冻结" : ""}
                      </p>
                    </div>
                    <span className="shrink-0 rounded-md bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                      {FRIEND_LABELS[item.friend_state] ?? item.friend_state}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </SurfaceCard>

        <PageColumn className="gap-4">
          {detail ? (
            <>
              <SurfaceCard>
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <p className="text-xs font-medium tracking-wide text-muted-foreground">
                      用户身份卡
                    </p>
                    <h2 className="mt-2 text-3xl font-semibold text-foreground">
                      {peerTitle(detail.user_qq, detail.operator_label)}
                    </h2>
                    <p className="mt-2 text-sm text-muted-foreground">
                      {detail.user_qq}
                      {" · "}
                      首次观察 {fmt(selected?.first_seen_at)} · 最近活动{" "}
                      {fmt(selected?.last_seen_at)}
                    </p>
                    <form
                      className="mt-4 flex flex-wrap gap-2"
                      onSubmit={(event) => {
                        event.preventDefault();
                        void saveLabel();
                      }}
                    >
                      <input
                        value={labelDraft}
                        onChange={(event) => setLabelDraft(event.target.value)}
                        placeholder="备注名，例如 小明"
                        maxLength={32}
                        className="min-w-0 flex-1 rounded-2xl border border-border bg-muted/50 px-4 py-2 text-sm outline-none"
                      />
                      <button
                        type="submit"
                        disabled={busy}
                        className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                      >
                        保存备注
                      </button>
                    </form>
                  </div>
                  <div className="text-right">
                    <span className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">
                      {FRIEND_LABELS[friendState] ?? friendState}
                    </span>
                    <p className="mt-3 text-xs text-muted-foreground">
                      可信度 {Math.round(confidence * 100)}%
                    </p>
                  </div>
                </div>
                <div className="mt-5 rounded-3xl bg-muted/55 p-4">
                  <p className="text-sm font-medium text-foreground">为什么这样判定</p>
                  <p className="mt-2 text-sm leading-6 text-muted-foreground">
                    {SOURCE_LABELS[
                      detail.relationship?.state_source ??
                        "insufficient_evidence"
                    ] ?? detail.relationship?.state_source}
                  </p>
                  {detail.identity_evidence.length ? (
                    <ul className="mt-4 space-y-2 border-t border-border pt-4 text-xs text-muted-foreground">
                      {detail.identity_evidence.slice(0, 6).map((item) => (
                        <li
                          key={item.id}
                          className="flex justify-between gap-4"
                        >
                          <span>
                            {EVIDENCE_LABELS[item.evidence_type] ??
                              item.evidence_type}{" "}
                            · {item.source}
                          </span>
                          <span className="shrink-0">
                            {fmt(item.observed_at)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <EmptyState
                      tone="inline"
                      className="mt-3 py-0 text-xs"
                      title="还没有身份证据"
                      detail="保持未知是正常结果。"
                    />
                  )}
                </div>
                <div className="mt-5 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                    onClick={() => runUser("user.mark_existing_friend")}
                  >
                    主控标为旧友
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    className="rounded-md bg-muted px-4 py-2 text-sm text-foreground disabled:opacity-60"
                    onClick={() => runUser("user.mark_new_friend")}
                  >
                    主控标为新友
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    className="rounded-md bg-muted px-4 py-2 text-sm text-foreground disabled:opacity-60"
                    onClick={() => runUser("user.mark_not_friend")}
                  >
                    标为非好友
                  </button>
                  {detail.relationship?.state_source === "owner_override" ? (
                    <button
                      type="button"
                      disabled={busy}
                      className="rounded-md border border-border bg-white px-4 py-2 text-sm text-foreground disabled:opacity-60"
                      onClick={() => runUser("user.restore_evidence")}
                    >
                      恢复证据自动判定
                    </button>
                  ) : null}
                </div>
              </SurfaceCard>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <SurfaceCard>
                  <div className="flex items-center gap-2 text-primary">
                    <History size={18} />
                    <p className="text-xs font-semibold tracking-[0.14em]">
                      聊天历史
                    </p>
                  </div>
                  <p className="mt-3 text-xl font-semibold text-foreground">
                    {HISTORY_LABELS[detail.history_policy.mode] ??
                      detail.history_policy.mode}
                  </p>
                  <p className="mt-2 min-h-12 text-sm leading-6 text-muted-foreground">
                    {policyDetail(detail)}
                  </p>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                      onClick={() => runUser("user.history_deny")}
                    >
                      禁止
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      className="rounded-md bg-muted px-4 py-2 text-sm text-foreground disabled:opacity-60"
                      onClick={() => runUser("user.history_file_only")}
                    >
                      仅文件导入
                    </button>
                  </div>
                  <div className="mt-5 border-t border-border pt-4">
                    <label className="text-xs text-muted-foreground">
                      最多消息条数（1–500）
                      <input
                        type="number"
                        min={1}
                        max={500}
                        value={historyCount}
                        onChange={(event) =>
                          setHistoryCount(Number(event.target.value))
                        }
                        className="mt-1 w-full rounded-xl border border-border bg-white px-3 py-2 text-sm text-foreground"
                      />
                    </label>
                    <button
                      type="button"
                      disabled={busy}
                      className="mt-3 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                      onClick={() =>
                        runUser("user.history_one_time", {
                          max_messages: String(historyCount),
                        })
                      }
                    >
                      申请一次读取授权
                    </button>
                  </div>
                  <form
                    className="mt-5 space-y-3 border-t border-border pt-4"
                    onSubmit={requestRange}
                  >
                    <p className="text-xs leading-5 text-muted-foreground">
                      指定范围只用于能在读取前严格限制日期的导入文件或适配器；不会绕过限制去拉取
                      NapCat 全量历史。
                    </p>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <DatePicker
                        variant="compact"
                        label="开始日期"
                        value={rangeFrom}
                        onChange={setRangeFrom}
                        allowClear
                        align="left"
                      />
                      <DatePicker
                        variant="compact"
                        label="结束日期"
                        value={rangeTo}
                        onChange={setRangeTo}
                        allowClear
                      />
                    </div>
                    <button
                      type="submit"
                      disabled={busy}
                      className="rounded-md bg-muted px-4 py-2 text-sm text-foreground disabled:opacity-60"
                    >
                      申请指定范围
                    </button>
                  </form>
                </SurfaceCard>

                <SurfaceCard>
                  <div className="flex items-center gap-2 text-primary">
                    <LockKeyhole size={18} />
                    <p className="text-xs font-semibold tracking-[0.14em]">
                      QQ 空间资料
                    </p>
                  </div>
                  <p className="mt-3 text-xl font-semibold text-foreground">
                    {QZONE_LABELS[detail.qzone_profile_policy.mode] ??
                      detail.qzone_profile_policy.mode}
                  </p>
                  <p className="mt-2 min-h-12 text-sm leading-6 text-muted-foreground">
                    {qzonePolicyDetail(detail)}
                  </p>
                  {detail.latest_collection ? (
                    <p className="mt-2 text-sm leading-6 text-muted-foreground">
                      最近一次：扫到 {detail.latest_collection.scanned} 条，识图{" "}
                      {detail.latest_collection.images_seen} 张，封面{" "}
                      {detail.latest_collection.covers_seen} 条，视频未读{" "}
                      {detail.latest_collection.videos_unread} 条。{" "}
                      <Link
                        to={`/memories?qq=${detail.user_qq}`}
                        className="text-primary underline-offset-4 hover:underline"
                      >
                        查看空间派生记忆
                      </Link>
                    </p>
                  ) : (
                    <EmptyState
                      tone="inline"
                      className="mt-2 py-0"
                      title="还没有采集记录。"
                      detail="一次最多扫 10 条，授权上限 20 条。"
                    />
                  )}
                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                      onClick={() => runUser("user.qzone_profile_deny")}
                    >
                      禁止
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      className="rounded-md bg-muted px-4 py-2 text-sm text-foreground disabled:opacity-60"
                      onClick={previewQzoneProfile}
                    >
                      授权后读取预览
                    </button>
                  </div>
                  <div className="mt-5 grid grid-cols-2 gap-3 border-t border-border pt-4">
                    <label className="text-xs text-muted-foreground">
                      最多动态（1–20）
                      <input
                        type="number"
                        min={1}
                        max={20}
                        value={profileItems}
                        onChange={(event) =>
                          setProfileItems(Number(event.target.value))
                        }
                        className="mt-1 w-full rounded-xl border border-border px-3 py-2 text-sm"
                      />
                    </label>
                    <label className="text-xs text-muted-foreground">
                      有效天数（1–90）
                      <input
                        type="number"
                        min={1}
                        max={90}
                        value={profileDays}
                        onChange={(event) =>
                          setProfileDays(Number(event.target.value))
                        }
                        className="mt-1 w-full rounded-xl border border-border px-3 py-2 text-sm"
                      />
                    </label>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                      onClick={() =>
                        runUser("user.qzone_profile_one_time", {
                          max_items: String(profileItems),
                        })
                      }
                    >
                      申请一次授权
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      className="rounded-md bg-muted px-4 py-2 text-sm text-foreground disabled:opacity-60"
                      onClick={() =>
                        runUser("user.qzone_profile_ttl", {
                          days: String(profileDays),
                          max_items: String(profileItems),
                        })
                      }
                    >
                      申请限期授权
                    </button>
                  </div>
                  <p className="mt-4 text-xs leading-5 text-muted-foreground">
                    读取结果只生成有期限的候选，不会直接变成事实或永久记忆。
                  </p>
                </SurfaceCard>
              </div>

              {style ? (
                <SurfaceCard>
                  <form onSubmit={saveStyle}>
                    <p className="text-sm font-medium text-foreground">
                      回复条数与句长
                    </p>
                    <p className="mt-2 text-xs leading-5 text-muted-foreground">
                      只调整给这个用户回话时的气泡拆分；不改变人格，也不会立即发送消息。
                    </p>
                    <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                      {[
                        ["最少句数", "min_bubbles", 1, 10],
                        ["最多句数", "max_bubbles", 1, 10],
                        ["每句最短", "sentence_min_chars", 4, 120],
                        ["每句最长", "sentence_max_chars", 8, 200],
                      ].map(([label, key, min, max]) => (
                        <label key={String(key)} className="text-xs text-muted-foreground">
                          {label}
                          <input
                            type="number"
                            min={Number(min)}
                            max={Number(max)}
                            value={style[key as keyof UserReplyStyle] as number}
                            onChange={(event) =>
                              setStyle({
                                ...style,
                                [key]: Number(event.target.value),
                              })
                            }
                            className="mt-1 w-full rounded-xl border border-border bg-white px-3 py-2 text-sm text-foreground"
                          />
                        </label>
                      ))}
                    </div>
                    <button
                      type="submit"
                      disabled={busy}
                      className="mt-4 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-60"
                    >
                      保存回复风格
                    </button>
                  </form>
                </SurfaceCard>
              ) : null}

              <DangerCard>
                <div className="flex items-center gap-2 text-red-800">
                  <ShieldCheck size={18} />
                  <p className="text-sm font-medium">冻结、导出与删除</p>
                </div>
                <p className="mt-2 text-sm leading-6 text-red-700/90">
                  冻结立即阻断新的读取和派生写入。导出与删除先进入审批；删除确认后仍需查看影响预览，且受隐私任务总开关保护。
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    className="rounded-md bg-white px-4 py-2 text-sm text-red-800 disabled:opacity-60"
                    onClick={() =>
                      runUser(frozen ? "privacy.unfreeze" : "privacy.freeze")
                    }
                  >
                    {frozen ? "解除冻结" : "立即冻结"}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    className="rounded-md bg-white px-4 py-2 text-sm text-red-800 disabled:opacity-60"
                    onClick={() => runUser("privacy.export")}
                  >
                    申请导出
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    className="rounded-md bg-red-700 px-4 py-2 text-sm text-white disabled:opacity-60"
                    onClick={() => runUser("privacy.delete")}
                  >
                    申请删除
                  </button>
                </div>
                {notice.includes("审批") ? (
                  <Link
                    to="/approvals"
                    className="mt-3 inline-block text-sm text-red-800 underline"
                  >
                    去汇报与审批
                  </Link>
                ) : null}
              </DangerCard>
            </>
          ) : (
            <SurfaceCard className="h-full">
              <EmptyState
                tone="select"
                title="从用户列表选择一个账号，或输入 QQ 查看。"
                detail="仅查看不会访问聊天历史或 QQ 空间。"
              />
            </SurfaceCard>
          )}
        </PageColumn>
      </PageSplit>
    </PageFrame>
  );
}
