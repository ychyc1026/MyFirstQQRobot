import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, AlertDescription } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { ApiError, api } from "../lib/api";
import { setSessionToken } from "../lib/auth";

export function LoginPage() {
  const navigate = useNavigate();
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const session = await api.createSession(token.trim());
      setSessionToken(session.access_token);
      navigate("/");
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) setError("尚未配置管理员令牌。写入本机 .env 后重启后端。");
      else setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen bg-muted/30 lg:grid-cols-2">
      <section className="hidden border-r bg-zinc-950 p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="flex items-center gap-3 font-semibold"><span className="grid h-9 w-9 place-items-center rounded-lg bg-white text-zinc-950">Y</span>YCH 智能体控制台</div>
        <div className="max-w-lg space-y-4"><p className="text-3xl font-semibold tracking-tight">本机优先、权限清晰、操作可追溯。</p><p className="text-sm leading-6 text-zinc-400">统一管理 QQ 消息、用户上下文、人格、记忆、主动任务与空间发布。高风险操作继续由主号确认。</p></div>
        <p className="text-xs text-zinc-500">Developer · YCH（维护者）</p>
      </section>
      <main className="flex items-center justify-center p-6 sm:p-10">
        <Card className="w-full max-w-md shadow-sm">
          <CardHeader><div className="mb-4 grid h-10 w-10 place-items-center rounded-lg bg-primary font-bold text-primary-foreground lg:hidden">Y</div><CardTitle className="text-2xl">登录控制台</CardTitle><CardDescription>使用本机管理员令牌换取短时会话。登录不会连接 QQ 或模型网络。</CardDescription></CardHeader>
          <CardContent><form className="space-y-5" onSubmit={onSubmit}>
            <div className="space-y-2"><Label htmlFor="admin-token">管理令牌</Label><Input id="admin-token" type="password" value={token} onChange={(event) => setToken(event.target.value)} placeholder="YCH_ADMIN_ACCESS_TOKEN" autoComplete="current-password" autoFocus /></div>
            {error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : null}
            <Button type="submit" disabled={busy || !token.trim()} className="w-full">{busy ? "正在换票…" : "进入控制台"}</Button>
          </form></CardContent>
        </Card>
      </main>
    </div>
  );
}
