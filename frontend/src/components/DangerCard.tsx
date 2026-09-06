import type { ReactNode } from "react";

type DangerCardProps = {
  children: ReactNode;
  className?: string;
};

export function DangerCard({ children, className = "" }: DangerCardProps) {
  return (
    <section
      className={`rounded-lg border border-destructive/30 bg-destructive/5 p-5 text-destructive ${className}`.trim()}
    >
      {children}
    </section>
  );
}
