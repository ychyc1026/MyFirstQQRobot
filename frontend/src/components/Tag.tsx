type TagTone = "orange" | "ink" | "mute" | "on-dark";

const TONE: Record<TagTone, string> = {
  orange: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300",
  ink: "border-transparent bg-primary text-primary-foreground",
  mute: "bg-secondary text-secondary-foreground",
  "on-dark": "border-white/20 bg-white/10 text-white",
};

export function Tag({
  children,
  tone = "ink",
}: {
  children: string;
  tone?: TagTone;
}) {
  return <span className={`ych-tag ${TONE[tone]}`}>{children}</span>;
}
