import { Skeleton } from "./ui/skeleton";
import { cn } from "../lib/utils";

type LoadingStateProps = {
  rows?: number;
  className?: string;
};

export function LoadingState({ rows = 3, className }: LoadingStateProps) {
  return (
    <div
      className={cn("space-y-3", className)}
      role="status"
      aria-busy="true"
      aria-label="加载中"
    >
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className="h-12 w-full" />
      ))}
    </div>
  );
}
