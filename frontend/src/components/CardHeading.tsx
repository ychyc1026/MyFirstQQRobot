import type { ReactNode } from "react";

export type CardLevel = "primary" | "secondary" | "tertiary";

const TITLE: Record<CardLevel, string> = {
  primary: "text-lg font-semibold leading-tight tracking-tight text-foreground",
  secondary: "text-base font-semibold leading-snug text-foreground",
  tertiary: "text-sm font-medium leading-snug text-foreground",
};

const SUBTITLE: Record<CardLevel, string> = {
  primary: "mt-1 text-sm leading-6 text-muted-foreground",
  secondary: "mt-1 text-xs leading-5 text-muted-foreground",
  tertiary: "mt-0.5 text-xs leading-5 text-muted-foreground",
};

const SPACING: Record<CardLevel, string> = {
  primary: "mb-5",
  secondary: "mb-3",
  tertiary: "mb-2",
};

type CardHeadingProps = {
  title: string;
  subtitle?: string;
  level?: CardLevel;
  action?: ReactNode;
  className?: string;
};

export function CardHeading({
  title,
  subtitle,
  level = "secondary",
  action,
  className = "",
}: CardHeadingProps) {
  return (
    <div className={`flex items-start justify-between gap-3 ${SPACING[level]} ${className}`.trim()}>
      <div className="min-w-0">
        <h2 className={TITLE[level]}>{title}</h2>
        {subtitle ? <p className={SUBTITLE[level]}>{subtitle}</p> : null}
      </div>
      {action ? <div className="flex shrink-0 items-start">{action}</div> : null}
    </div>
  );
}
