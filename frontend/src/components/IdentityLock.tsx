import { useNavigate } from "react-router-dom";
import type { Identity } from "../lib/api";
import { Tag } from "./Tag";

type IdentityLockProps = {
  identity?: Identity | null;
  fill?: boolean;
};

export function IdentityLock({ identity, fill = true }: IdentityLockProps) {
  const navigate = useNavigate();
  const brand = identity?.brand ?? "YCH";
  return (
    <section
      className={`ych-card-sage relative flex min-h-[250px] flex-col rounded-lg p-[22px_24px] text-foreground ${fill ? "h-full" : ""}`}
    >
      <Tag tone="orange">署名锁定</Tag>
      <div className="ych-mark-khaki mt-4 grid h-[88px] w-[88px] place-items-center rounded-3xl text-[36px] font-extrabold text-foreground">
        {brand.slice(0, 1)}
      </div>
      <p className="mt-3 text-[22px] font-bold tracking-[-0.03em] text-foreground">{brand}</p>
      <p className="mt-1.5 text-[13px] text-muted-foreground">创造者 · {identity?.creator_name || "—"}</p>
      <button
        type="button"
        className="absolute bottom-[18px] right-[18px] grid h-[52px] w-[52px] place-items-center rounded-md bg-primary text-[22px] font-bold text-primary-foreground"
        onClick={() => navigate("/system")}
        aria-label="系统与审计"
      >
        →
      </button>
    </section>
  );
}
