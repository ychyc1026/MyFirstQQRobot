import { Link } from "react-router-dom";
import { Tag } from "./Tag";

type QueueRowProps = {
  title: string;
  badge: string;
  meta?: string;
  to: string;
};

export function QueueRow({ title, badge, meta, to }: QueueRowProps) {
  const tone =
    badge.includes("待") || badge.includes("紧急") || badge === "High"
      ? "orange"
      : badge === "关闭"
        ? "mute"
        : "ink";
  return (
    <Link
      to={to}
      className="flex items-center justify-between gap-3 rounded-2xl px-1 py-3 transition hover:bg-muted/70"
    >
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-foreground">{title}</p>
        {meta ? <p className="mt-1 text-xs text-muted-foreground">{meta}</p> : null}
      </div>
      <Tag tone={tone}>{badge}</Tag>
    </Link>
  );
}
