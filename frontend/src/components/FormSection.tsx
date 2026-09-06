import type { ReactNode } from "react";
import { Separator } from "./ui/separator";

export function FormSection({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return <section className="grid gap-5 py-5 md:grid-cols-[220px_1fr]"><div><h3 className="text-sm font-medium">{title}</h3>{description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}</div><div className="space-y-4">{children}</div><Separator className="md:col-span-2" /></section>;
}
