import { shanghaiDayKey } from "../lib/datetime";

export const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

export function pad(value: number) {
  return String(value).padStart(2, "0");
}

export function toDayKey(year: number, month: number, day: number) {
  return `${year}-${pad(month)}-${pad(day)}`;
}

export function parseDayKey(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return {
    year: Number.isFinite(year) ? year : new Date().getFullYear(),
    month: Number.isFinite(month) ? month : 1,
    day: Number.isFinite(day) ? day : 1,
  };
}

export function daysInMonth(year: number, month: number) {
  return new Date(year, month, 0).getDate();
}

export function weekdayIndex(year: number, month: number, day: number) {
  const weekday = new Date(year, month - 1, day).getDay();
  return weekday === 0 ? 6 : weekday - 1;
}

export function weekdayLabel(value: string) {
  const { year, month, day } = parseDayKey(value);
  const labels = ["日", "一", "二", "三", "四", "五", "六"];
  return labels[new Date(year, month - 1, day).getDay()] ?? "";
}

export function weekAround(dayKey: string) {
  const { year, month, day } = parseDayKey(dayKey);
  const center = new Date(year, month - 1, day);
  const weekday = center.getDay();
  const mondayOffset = weekday === 0 ? -6 : 1 - weekday;
  const start = new Date(center);
  start.setDate(center.getDate() + mondayOffset);
  const items: string[] = [];
  for (let index = 0; index < 7; index += 1) {
    const next = new Date(start);
    next.setDate(start.getDate() + index);
    items.push(toDayKey(next.getFullYear(), next.getMonth() + 1, next.getDate()));
  }
  return items;
}

export function anchorDayKey(value: string, viewYear: number, viewMonth: number, today = shanghaiDayKey()) {
  if (value) {
    const parsed = parseDayKey(value);
    if (parsed.year === viewYear && parsed.month === viewMonth) return value;
  }
  const parsedToday = parseDayKey(today);
  if (parsedToday.year === viewYear && parsedToday.month === viewMonth) return today;
  return toDayKey(viewYear, viewMonth, 1);
}

export function buildMonthCells(viewYear: number, viewMonth: number) {
  const total = daysInMonth(viewYear, viewMonth);
  const leading = weekdayIndex(viewYear, viewMonth, 1);
  const items: Array<{ key: string; day: number; muted: boolean; year: number; month: number }> = [];
  const prevMonth = viewMonth === 1 ? 12 : viewMonth - 1;
  const prevYear = viewMonth === 1 ? viewYear - 1 : viewYear;
  const prevTotal = daysInMonth(prevYear, prevMonth);
  for (let index = leading - 1; index >= 0; index -= 1) {
    const dayNumber = prevTotal - index;
    items.push({
      key: `${prevYear}-${prevMonth}-${dayNumber}`,
      day: dayNumber,
      muted: true,
      year: prevYear,
      month: prevMonth,
    });
  }
  for (let dayNumber = 1; dayNumber <= total; dayNumber += 1) {
    items.push({
      key: toDayKey(viewYear, viewMonth, dayNumber),
      day: dayNumber,
      muted: false,
      year: viewYear,
      month: viewMonth,
    });
  }
  const nextMonth = viewMonth === 12 ? 1 : viewMonth + 1;
  const nextYear = viewMonth === 12 ? viewYear + 1 : viewYear;
  let dayNumber = 1;
  while (items.length % 7 !== 0) {
    items.push({
      key: `${nextYear}-${nextMonth}-${dayNumber}`,
      day: dayNumber,
      muted: true,
      year: nextYear,
      month: nextMonth,
    });
    dayNumber += 1;
  }
  return items;
}
