import { CalendarDays, ChevronDown } from "lucide-react";
import { useState } from "react";
import { shanghaiDayKey } from "../lib/datetime";
import { CalendarPanel } from "./CalendarPanel";
import { parseDayKey, weekdayLabel } from "./calendarUtils";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";

type DatePickerProps = {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  hint?: string;
  placeholder?: string;
  className?: string;
  allowClear?: boolean;
  variant?: "field" | "inline" | "compact";
  align?: "left" | "right";
};

export function DatePicker({
  value,
  onChange,
  label,
  hint,
  placeholder = "选择日期",
  className = "",
  allowClear = false,
  variant = "field",
  align = "right",
}: DatePickerProps) {
  const [open, setOpen] = useState(false);
  const today = shanghaiDayKey();
  const current = value || today;
  const { year, month, day } = parseDayKey(current);
  const subtitle =
    hint ??
    (value === today
      ? "今天"
      : value && value < today
        ? "已过去"
        : value && value > today
          ? "未到"
          : "");

  function pickDay(nextKey: string) {
    onChange(nextKey);
    setOpen(false);
  }

  const trigger =
    variant === "inline" ? (
      <button
        type="button"
        className="inline-flex items-center gap-1 rounded-md bg-muted p-1 pl-3.5 pr-1 text-left transition hover:bg-muted/90"
        aria-label={label ?? "选择日期"}
      >
        <span className="text-xs font-bold tracking-[-0.02em] text-foreground">
          {month}月{day}日
        </span>
        <span className="rounded-md bg-card px-2.5 py-1 text-[11px] font-semibold text-muted-foreground">
          周{weekdayLabel(current)}
        </span>
      </button>
    ) : variant === "compact" ? (
      <button
        type="button"
        className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-background px-3 text-left text-sm text-foreground shadow-sm outline-none transition hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        aria-label={label ?? "选择日期"}
      >
        <CalendarDays className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className={`min-w-0 flex-1 truncate ${value ? "" : "text-muted-foreground"}`}>
          {value ? `${year}年${month}月${day}日 · 周${weekdayLabel(current)}` : placeholder}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
      </button>
    ) : (
      <button
        type="button"
        className="flex w-full items-center gap-4 rounded-2xl bg-muted/50 px-3 py-3 text-left transition hover:bg-muted/75"
        aria-label={label ?? "选择日期"}
      >
        <span
          className="grid h-[72px] w-[72px] shrink-0 place-items-center rounded-lg bg-muted text-foreground"
          aria-hidden
        >
          <span className="text-[34px] font-extrabold leading-none tracking-[-0.05em]">{day}</span>
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-base font-bold tracking-[-0.03em] text-foreground">
            {year}年{month}月
          </span>
          <span className="mt-1 block text-xs text-muted-foreground">
            星期{weekdayLabel(current)}
            {subtitle ? ` · ${subtitle}` : ""}
          </span>
        </span>
        <span className="shrink-0 rounded-md bg-card px-3 py-1.5 text-[11px] font-bold text-muted-foreground">
          切换
        </span>
      </button>
    );

  return (
    <div className={className}>
      {label && variant !== "inline" ? (
        <span className="mb-1 block text-xs text-muted-foreground">{label}</span>
      ) : null}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>{trigger}</PopoverTrigger>
        <PopoverContent
          align={align === "right" ? "end" : "start"}
          className="w-[min(320px,calc(100vw-2rem))] overflow-hidden p-0"
          aria-label="选择日期"
        >
          <CalendarPanel
            embedded
            compact
            value={current}
            onPickDay={pickDay}
            allowClear={allowClear}
            onClear={() => {
              onChange("");
              setOpen(false);
            }}
          />
        </PopoverContent>
      </Popover>
    </div>
  );
}
