import { CalendarClock, ChevronDown } from "lucide-react";
import { useState } from "react";
import { shanghaiClock, shanghaiDayKey } from "../lib/datetime";
import { CalendarPanel } from "./CalendarPanel";
import { pad, parseDayKey, weekdayLabel } from "./calendarUtils";
import { Select } from "./Select";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";

type DateTimePickerProps = {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  placeholder?: string;
  className?: string;
  allowClear?: boolean;
  align?: "left" | "right";
};

const HOURS = Array.from({ length: 24 }, (_, value) => ({ value: pad(value), label: `${pad(value)} 时` }));
const MINUTES = Array.from({ length: 60 }, (_, value) => ({ value: pad(value), label: `${pad(value)} 分` }));

function nowLocal() {
  const iso = new Date().toISOString();
  return `${shanghaiDayKey()}T${shanghaiClock(iso)}`;
}

function parts(value: string) {
  const match = value.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/);
  if (match) return { day: match[1], hour: match[2], minute: match[3] };
  const current = nowLocal();
  return { day: current.slice(0, 10), hour: current.slice(11, 13), minute: current.slice(14, 16) };
}

export function DateTimePicker({
  value,
  onChange,
  label,
  placeholder = "选择日期和时间",
  className = "",
  allowClear = true,
  align = "right",
}: DateTimePickerProps) {
  const [open, setOpen] = useState(false);
  const initial = parts(value);
  const [draftDay, setDraftDay] = useState(initial.day);
  const [draftHour, setDraftHour] = useState(initial.hour);
  const [draftMinute, setDraftMinute] = useState(initial.minute);
  const selected = value ? parts(value) : null;
  const selectedDate = parseDayKey(selected?.day ?? shanghaiDayKey());

  function changeOpen(next: boolean) {
    if (next) {
      const draft = parts(value);
      setDraftDay(draft.day);
      setDraftHour(draft.hour);
      setDraftMinute(draft.minute);
    }
    setOpen(next);
  }

  function applyNow() {
    onChange(nowLocal());
    setOpen(false);
  }

  function applyDraft() {
    onChange(`${draftDay}T${draftHour}:${draftMinute}`);
    setOpen(false);
  }

  return (
    <div className={className}>
      {label ? <span className="mb-1 block text-xs text-muted-foreground">{label}</span> : null}
      <Popover open={open} onOpenChange={changeOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-background px-3 text-left text-sm text-foreground shadow-sm outline-none transition hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            aria-label={label ?? "选择日期和时间"}
          >
            <CalendarClock className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className={`min-w-0 flex-1 truncate ${selected ? "" : "text-muted-foreground"}`}>
              {selected
                ? `${selectedDate.year}年${selectedDate.month}月${selectedDate.day}日 · 周${weekdayLabel(selected.day)} · ${selected.hour}:${selected.minute}`
                : placeholder}
            </span>
            <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          </button>
        </PopoverTrigger>
        <PopoverContent
          align={align === "right" ? "end" : "start"}
          className="w-[min(340px,calc(100vw-2rem))] overflow-hidden p-0"
          aria-label="选择日期和时间"
        >
          <CalendarPanel
            embedded
            compact
            value={draftDay}
            onPickDay={setDraftDay}
            showTodayButton={false}
            footer={
              <div className="mt-1 flex flex-wrap items-center gap-2 border-t border-border/60 pt-1">
                <button
                  type="button"
                  className="rounded-md bg-muted px-3 py-1 text-xs font-semibold text-foreground"
                  onClick={applyNow}
                >
                  现在
                </button>
                {allowClear ? (
                  <button
                    type="button"
                    className="rounded-md px-3 py-1 text-xs font-semibold text-muted-foreground hover:bg-muted"
                    onClick={() => {
                      onChange("");
                      setOpen(false);
                    }}
                  >
                    清除
                  </button>
                ) : null}
                <div className="ml-auto flex gap-2">
                  <button
                    type="button"
                    className="rounded-md px-3 py-1 text-xs font-semibold text-muted-foreground hover:bg-muted"
                    onClick={() => setOpen(false)}
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    className="rounded-md bg-primary px-3.5 py-1 text-xs font-bold text-primary-foreground"
                    onClick={applyDraft}
                  >
                    完成
                  </button>
                </div>
              </div>
            }
          >
            <div className="mt-1 border-t border-border/60 pt-1">
              <p className="mb-2 text-xs font-semibold text-foreground">时间</p>
              <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
                <Select value={draftHour} onChange={setDraftHour} options={HOURS} aria-label="小时" />
                <span className="text-sm font-bold text-muted-foreground">:</span>
                <Select value={draftMinute} onChange={setDraftMinute} options={MINUTES} aria-label="分钟" />
              </div>
            </div>
          </CalendarPanel>
        </PopoverContent>
      </Popover>
    </div>
  );
}
