import { AlertTriangle } from "lucide-react";
import { Button } from "./ui/button";

export function ErrorState({ title = "无法载入数据", detail, retry }: { title?: string; detail?: string; retry?: () => void }) {
  return <div className="flex min-h-48 flex-col items-center justify-center rounded-lg border border-destructive/30 bg-destructive/5 p-8 text-center"><AlertTriangle className="mb-3 h-8 w-8 text-destructive" /><p className="font-medium">{title}</p>{detail ? <p className="mt-1 max-w-md text-sm text-muted-foreground">{detail}</p> : null}{retry ? <Button variant="outline" size="sm" className="mt-4" onClick={retry}>重试</Button> : null}</div>;
}
