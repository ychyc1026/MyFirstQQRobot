import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { shanghaiDayKey } from "../lib/datetime";
import {
  WEEKDAYS,
  anchorDayKey,
  buildMonthCells,
  parseDayKey,
  toDayKey,
  weekdayLabel,
  weekAround,
} from "./calendarUtils";

type CalendarPanelProps = {
  value: string;
  onPickDay: (dayKey: string) => void;
  align?: "left" | "right";
  allowClear?: boolean;
  onClear?: () => void;
  showTodayButton?: boolean;
  embedded?: boolean;
  compact?: boolean;
  children?: ReactNode;
  footer?: ReactNode;
};

export function CalendarPanel({
  value,
  onPickDay,
  align = "right",
  allowClear = false,
  onClear,
  showTodayButton = true,
  embedded = false,
  compact = false,
  children,
  footer,
}: CalendarPanelProps) {
  const today = shanghaiDayKey();
  const parsed = parseDayKey(value || today);
  const [viewYear, setViewYear] = useState(parsed.year);
  const [viewMonth, setViewMonth] = useState(parsed.month);

  useEffect(() => {
    const next = parseDayKey(value || today);
    setViewYear(next.year);
    setViewMonth(next.month);
  }, [today, value]);

  const anchor = anchorDayKey(value, viewYear, viewMonth, today);
  const weekStrip = useMemo(() => weekAround(anchor), [anchor]);
  const cells = useMemo(() => buildMonthCells(viewYear, viewMonth), [viewMonth, viewYear]);

  function shiftMonth(delta: number) {
    let month = viewMonth + delta;
    let year = viewYear;
    while (month < 1) {
      month += 12;
      year -= 1;
    }
    while (month > 12) {
      month -= 12;
      year += 1;
    }
    setViewMonth(month);
    setViewYear(year);
  }

  return (
    <div
      role={embedded ? undefined : "dialog"}
      aria-label={embedded ? undefined : "选择日期"}
      className={embedded ? "w-full overflow-hidden bg-popover" : `absolute z-50 mt-2 w-[min(320px,calc(100vw-2rem))] overflow-hidden rounded-lg border bg-popover shadow-md ${align === "right" ? "right-0" : "left-0"}`}
    >
      {!compact ? <div className="border-b border-border/6 bg-muted/55 p-2">
        <div className="grid grid-cols-7 gap-1">
          {weekStrip.map((dayKey) => {
            const item = parseDayKey(dayKey);
            const selected = dayKey === value;
            const isToday = dayKey === today;
            return (
              <button
                key={dayKey}
                type="button"
                onClick={() => onPickDay(dayKey)}
                className={`flex flex-col items-center rounded-2xl px-1 py-2 transition ${
                  selected
                    ? "bg-primary text-primary-foreground"
                    : isToday
                      ? "bg-card text-primary"
                      : "text-foreground hover:bg-card/80"
                }`}
              >
                <span className="text-[10px] font-semibold opacity-80">周{weekdayLabel(dayKey)}</span>
                <span className="mt-0.5 text-sm font-extrabold tracking-[-0.03em]">{item.day}</span>
              </button>
            );
          })}
        </div>
      </div> : null}

      <div className={compact ? "px-3 pb-1 pt-1" : "px-3 pb-3 pt-3"}>
        <div className={`${compact ? "mb-2" : "mb-3"} flex items-center justify-between gap-2`}>
          <button
            type="button"
            className={`inline-flex ${compact ? "h-8" : "h-9"} items-center gap-1 rounded-md px-2 text-xs font-semibold text-muted-foreground transition hover:bg-muted hover:text-foreground`}
            onClick={() => shiftMonth(-1)}
            aria-label="上个月"
          >
            <ChevronLeft size={15} strokeWidth={2} />
            上月
          </button>
          <p className="text-center text-sm font-bold tracking-[-0.03em] text-foreground">
            {viewYear}
            <span className="mx-1 text-muted-foreground">·</span>
            {viewMonth}月
          </p>
          <button
            type="button"
            className={`inline-flex ${compact ? "h-8" : "h-9"} items-center gap-1 rounded-md px-2 text-xs font-semibold text-muted-foreground transition hover:bg-muted hover:text-foreground`}
            onClick={() => shiftMonth(1)}
            aria-label="下个月"
          >
            下月
            <ChevronRight size={15} strokeWidth={2} />
          </button>
        </div>

        <div className="grid grid-cols-7 gap-1 text-center text-[10px] font-semibold text-muted-foreground">
          {WEEKDAYS.map((weekday) => (
            <span key={weekday} className={compact ? "py-0" : "py-1"}>
              {weekday}
            </span>
          ))}
        </div>

        <div className="mt-1 grid grid-cols-7 gap-1">
          {cells.map((cell) => {
            const key = toDayKey(cell.year, cell.month, cell.day);
            const selected = key === value;
            const isToday = key === today;
            return (
              <button
                key={cell.key}
                type="button"
                onClick={() => onPickDay(key)}
                className={`relative flex ${compact ? "h-7" : "h-10"} flex-col items-center justify-center rounded-xl text-sm font-semibold transition ${
                  selected
                    ? "bg-primary text-primary-foreground"
                    : isToday
                      ? "bg-muted/80 text-primary-deep"
                      : cell.muted
                        ? "text-muted-foreground/45 hover:bg-muted/60 hover:text-muted-foreground"
                        : "text-foreground hover:bg-muted"
                }`}
              >
                {cell.day}
                {isToday && !selected ? (
                  <span className="absolute bottom-1.5 h-1 w-1 rounded-full bg-primary" aria-hidden />
                ) : null}
              </button>
            );
          })}
        </div>

        {children}

        {footer ?? (
          <div className="mt-3 flex items-center gap-2 border-t border-border/6 pt-3">
            {showTodayButton ? (
              <button
                type="button"
                className="rounded-md bg-primary px-3.5 py-1.5 text-xs font-bold text-primary-foreground transition hover:opacity-90"
                onClick={() => onPickDay(today)}
              >
                跳到今天
              </button>
            ) : null}
            {allowClear ? (
              <button
                type="button"
                className="rounded-md px-3 py-1.5 text-xs font-semibold text-muted-foreground transition hover:bg-muted hover:text-foreground"
                onClick={onClear}
              >
                清除
              </button>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}
