import { Inbox } from "lucide-react";
import { cn } from "../lib/utils";

export type EmptyTone = "panel" | "inline" | "select";

type EmptyStateProps = {
  title?: string;
  detail?: string;
  tone?: EmptyTone;
  className?: string;
};

export function EmptyState({
  title = "暂无数据",
  detail,
  tone = "panel",
  className,
}: EmptyStateProps) {
  const resolvedDetail =
    detail === undefined && tone === "panel"
      ? "当前筛选条件下没有可显示的记录。"
      : detail;

  if (tone === "inline") {
    return (
      <div className={cn("py-6 text-sm text-muted-foreground", className)} role="status">
        <p className="font-medium text-foreground/80">{title}</p>
        {resolvedDetail ? <p className="mt-1 max-w-prose">{resolvedDetail}</p> : null}
      </div>
    );
  }

  if (tone === "select") {
    return (
      <div
        className={cn(
          "flex min-h-40 flex-col items-center justify-center py-10 text-center text-sm text-muted-foreground",
          className,
        )}
        role="status"
      >
        <p className="font-medium text-foreground/80">{title}</p>
        {resolvedDetail ? <p className="mt-1 max-w-sm">{resolvedDetail}</p> : null}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex min-h-48 flex-col items-center justify-center rounded-lg border border-dashed p-8 text-center",
        className,
      )}
      role="status"
    >
      <Inbox className="mb-3 h-8 w-8 text-muted-foreground" aria-hidden />
      <p className="font-medium">{title}</p>
      {resolvedDetail ? (
        <p className="mt-1 max-w-sm text-sm text-muted-foreground">{resolvedDetail}</p>
      ) : null}
    </div>
  );
}
