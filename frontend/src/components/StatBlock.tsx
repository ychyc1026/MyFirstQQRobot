type StatBlockProps = {
  label: string;
  value: string | number;
  hint?: string;
  closed?: boolean;
  onClick?: () => void;
};

export function StatBlock({ label, value, hint, closed, onClick }: StatBlockProps) {
  const TagName = onClick ? "button" : "div";
  return (
    <TagName
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={`h-full rounded-lg border bg-card p-5 text-left text-card-foreground shadow-sm ${
        onClick ? "transition-colors hover:bg-accent/50" : ""
      }`}
    >
      <p className="text-sm font-medium text-muted-foreground">{label}</p>
      <p
        className={`mt-2 font-bold leading-none tracking-tight text-foreground ${
          closed ? "text-2xl" : "text-3xl"
        }`}
      >
        {closed ? "关闭" : value}
      </p>
      {hint ? <p className="mt-2 text-xs text-muted-foreground">{hint}</p> : null}
    </TagName>
  );
}
