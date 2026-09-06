import type { ReactNode } from "react";
import { CardHeading, type CardLevel } from "./CardHeading";

type SurfaceCardProps = {
  children: ReactNode;
  className?: string;
  padding?: string;
  tone?: "surface" | "orange" | "sage" | "ink";
  fill?: boolean;
  emphasis?: "featured" | "default" | "compact";
  heading?: {
    title: string;
    subtitle?: string;
    level?: CardLevel;
    action?: ReactNode;
  };
};

const TONE = {
  surface: "bg-card text-card-foreground",
  orange: "border-primary/20 bg-primary/5 text-foreground",
  sage: "bg-card text-card-foreground",
  ink: "border-border bg-card text-card-foreground",
};

const EMPHASIS = {
  featured: "p-6",
  default: "p-6",
  compact: "p-4",
};

export function SurfaceCard({
  children,
  className = "",
  padding,
  tone = "surface",
  fill = false,
  emphasis = "default",
  heading,
}: SurfaceCardProps) {
  const pad = padding ?? EMPHASIS[emphasis];
  return (
    <section
      className={`rounded-lg border shadow-sm ${TONE[tone]} ${pad} ${fill ? "flex h-full min-h-0 flex-col" : ""} ${className}`.trim()}
    >
      {heading ? (
        <CardHeading
          title={heading.title}
          subtitle={heading.subtitle}
          level={heading.level}
          action={heading.action}
        />
      ) : null}
      {children}
    </section>
  );
}
