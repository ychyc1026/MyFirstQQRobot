import { Badge } from "./ui/badge";

const risky = /failed|error|blocked|open|critical|stale|expired|失败|熔断|拒绝|阻止|过期|不可执行|异常|缺失|无效/i;
const healthy = /active|healthy|ready|completed|success|正常|完成|已启用|已验证|可执行|通过/i;
const waiting = /pending|waiting|paused|unknown|待|暂停|未知/i;

export function StatusBadge({ value }: { value?: string | null }) {
  const text = value || "未知";
  const variant = risky.test(text) ? "destructive" : healthy.test(text) ? "default" : waiting.test(text) ? "secondary" : "outline";
  return <Badge variant={variant}>{text}</Badge>;
}
