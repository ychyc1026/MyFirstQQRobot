import type { HTMLAttributes } from "react";
import { cn } from "../lib/utils";

/**
 * Route-level frame. The app shell gives it the remaining viewport height;
 * content can still grow naturally when a page becomes longer than the viewport.
 */
export function PageFrame({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-page-layout="frame"
      className={cn("flex min-h-0 w-full min-w-0 flex-col", className)}
      {...props}
    />
  );
}

/**
 * Main page region. It consumes unused viewport height and stretches its columns
 * to one shared bottom edge without relying on fixed pixel heights.
 */
export function PageSplit({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-page-layout="split"
      className={cn("grid min-h-0 flex-1 grid-cols-1 items-stretch gap-5", className)}
      {...props}
    />
  );
}

/**
 * A vertical page column. New cards are appended normally; only the current last
 * card absorbs spare height, so adding another card automatically moves the fill
 * responsibility to the new bottom card.
 */
export function PageColumn({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-page-layout="column"
      className={cn("flex min-h-0 flex-col gap-5 [&>:last-child]:flex-1", className)}
      {...props}
    />
  );
}
