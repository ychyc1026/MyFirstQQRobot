import { OperatorLabelManager } from "../components/OperatorLabelManager";
import { PageFrame } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { SurfaceCard } from "../components/SurfaceCard";
import { HANDBOOK_GROUPS, HANDBOOK_RULES } from "../lib/ownerHandbook";

const RISK_LABELS = {
  read: "只读",
  controlled: "可控",
  high: "高风险",
};

export function HandbookPage() {
  return (
    <PageFrame className="gap-5">
      <PageTitle
        title="操作手册"
        description="主号私聊机器人才能用斜杠指令。完整文本也在仓库 docs/operator/。"
      />
      <div className="grid gap-4 xl:grid-cols-[.9fr_1.1fr]">
        <SurfaceCard>
          <p className="text-xs font-medium tracking-wide text-muted-foreground">每天先记住</p>
          <ul className="mt-4 space-y-3 text-sm leading-6 text-foreground">
            {HANDBOOK_RULES.map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
        </SurfaceCard>
        <OperatorLabelManager />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {HANDBOOK_GROUPS.map((group) => (
          <SurfaceCard key={group.title}>
            <p className="text-sm font-medium text-foreground">{group.title}</p>
            <ul className="mt-4 space-y-3">
              {group.items.map((item) => (
                <li key={item.command} className="rounded-2xl bg-muted/50 px-3 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <code className="text-sm font-medium text-foreground">{item.command}</code>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {RISK_LABELS[item.risk]}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">{item.meaning}</p>
                </li>
              ))}
            </ul>
          </SurfaceCard>
        ))}
      </div>
    </PageFrame>
  );
}
