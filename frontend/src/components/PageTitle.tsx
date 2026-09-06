import type { ReactNode } from "react";

type PageTitleProps = {
  title: string;
  description?: string;
  badge?: ReactNode;
};

export function PageTitle({ title, description, badge }: PageTitleProps) {
  return (
    <div className="mb-6 flex flex-none flex-col justify-between gap-3 sm:flex-row sm:items-start">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">
          {title}
        </h1>
        {description ? (
          <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {badge ? <div className="flex shrink-0 items-center gap-2">{badge}</div> : null}
    </div>
  );
}
