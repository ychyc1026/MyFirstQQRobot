export const SHANGHAI_TIMEZONE = "Asia/Shanghai";
export const CONTENT_CHAR_LIMIT = 20_000;

export function shanghaiDayKey(now = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: SHANGHAI_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

export function shanghaiClock(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso.slice(11, 16) || iso;
  }
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: SHANGHAI_TIMEZONE,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(parsed);
}

export function shanghaiDateTime(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: SHANGHAI_TIMEZONE,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(parsed);
}

/** Treat datetime-local as Asia/Shanghai wall clock (UTC+8, no DST). */
export function shanghaiLocalToIso(local: string): string {
  const value = local.length === 16 ? `${local}:00` : local;
  const parsed = new Date(`${value}+08:00`);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error("计划时间无效");
  }
  return parsed.toISOString();
}
