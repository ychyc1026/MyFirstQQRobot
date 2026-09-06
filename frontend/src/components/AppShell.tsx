import type { LucideIcon } from "lucide-react";
import {
  Activity,
  BookOpen,
  Bot,
  CalendarClock,
  ChevronDown,
  Gauge,
  Image,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquare,
  Moon,
  NotebookPen,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  Sun,
  Users,
  Waypoints,
  Workflow,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, type ReadinessDetail, type ReadinessSummary } from "../lib/api";
import { CAPABILITY_SCOPE_LABELS, READINESS_STATUS_LABELS } from "../lib/labels";
import { clearSessionToken } from "../lib/auth";
import { getStoredTheme, setStoredTheme, type ThemeId } from "../lib/theme";
import { cn } from "../lib/utils";
import { Avatar, AvatarFallback } from "./ui/avatar";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "./ui/command";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import { ScrollArea } from "./ui/scroll-area";
import { Separator } from "./ui/separator";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "./ui/sheet";

type NavLeaf = { to: string; label: string; icon: LucideIcon; end?: boolean; keywords?: string };
type NavGroup = { id: string; label: string; items: NavLeaf[] };

const GROUPS: NavGroup[] = [
  {
    id: "observe",
    label: "概览与运行",
    items: [
      { to: "/", label: "总览", icon: LayoutDashboard, end: true, keywords: "dashboard 首页" },
      { to: "/handbook", label: "操作手册", icon: BookOpen, keywords: "指令 帮助 备注 命令" },
      { to: "/ops", label: "运行近况", icon: Activity, keywords: "状态 worker" },
      { to: "/pipeline", label: "消息链路", icon: Workflow, keywords: "回复 runtime" },
      { to: "/models", label: "模型资格", icon: Bot, keywords: "鉴定 路由 模型 chat vision" },
      { to: "/stats", label: "统计分析", icon: Gauge, keywords: "趋势 token" },
    ],
  },
  {
    id: "people",
    label: "用户与上下文",
    items: [
      { to: "/users", label: "用户", icon: Users, keywords: "关系 好友 历史 备注" },
      { to: "/personas", label: "人格", icon: Sparkles, keywords: "角色 定义" },
      { to: "/memories", label: "记忆", icon: Waypoints, keywords: "冲突 偏好" },
      { to: "/quotas", label: "配额", icon: Gauge, keywords: "限额 token" },
    ],
  },
  {
    id: "conversation",
    label: "对话与知识",
    items: [
      { to: "/conversations", label: "会话检查", icon: MessageSquare, keywords: "上下文 影子" },
      { to: "/chatlog", label: "聊天记录", icon: MessageSquare, keywords: "消息 私聊 群聊" },
      { to: "/knowledge", label: "资料炼化", icon: BookOpen, keywords: "文件 知识" },
      { to: "/approvals", label: "汇报与审批", icon: ShieldCheck, keywords: "确认 待办" },
    ],
  },
  {
    id: "automation",
    label: "自动化",
    items: [
      { to: "/proactive", label: "主动消息", icon: CalendarClock, keywords: "定时 调度" },
      { to: "/diary", label: "YCH 日记", icon: NotebookPen, keywords: "日期 内容" },
      { to: "/materials", label: "主动材料", icon: BookOpen, keywords: "上传 核验" },
      { to: "/qzone", label: "QQ 空间", icon: Image, keywords: "说说 发布" },
    ],
  },
  {
    id: "system",
    label: "系统",
    items: [
      { to: "/activity", label: "操作日志", icon: Activity, keywords: "审计 登录 操作 命令" },
      { to: "/accounts", label: "账号", icon: Bot, keywords: "机器人 主号" },
      { to: "/privacy", label: "隐私审计", icon: ShieldCheck, keywords: "导出 删除 访问" },
      { to: "/system", label: "系统设置", icon: Settings, keywords: "数据库 备份 版本" },
    ],
  },
];

const ALL_ITEMS = GROUPS.flatMap((group) => group.items);

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <ScrollArea className="min-h-0 flex-1 px-3">
      <nav className="space-y-4 pb-5" aria-label="主导航">
        {GROUPS.map((group) => (
          <div key={group.id}>
            <p className="mb-1 px-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              {group.label}
            </p>
            <div className="space-y-1">
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    onClick={onNavigate}
                    className={({ isActive }) =>
                      cn(
                        "flex h-9 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors",
                        isActive
                          ? "bg-accent text-accent-foreground"
                          : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                      )
                    }
                  >
                    <Icon className="h-4 w-4" strokeWidth={1.9} />
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
    </ScrollArea>
  );
}

function Brand() {
  return (
    <div className="flex h-14 items-center gap-3 px-5">
      <div className="grid h-8 w-8 place-items-center rounded-md bg-primary text-xs font-bold text-primary-foreground">
        Y
      </div>
      <div className="leading-tight">
        <p className="text-sm font-semibold">YCH</p>
        <p className="text-xs text-muted-foreground">QQ Assistant</p>
      </div>
    </div>
  );
}

export function AppShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const [theme, setTheme] = useState<ThemeId>(getStoredTheme);
  const [commandOpen, setCommandOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [readiness, setReadiness] = useState<ReadinessSummary | null>(null);
  const [readinessDetail, setReadinessDetail] = useState<ReadinessDetail | null>(null);
  const [connected, setConnected] = useState(true);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        await api.status();
        if (active) {
          setConnected(true);
        }
        try {
          const readinessBody = await api.readinessSummary();
          if (active) setReadiness(readinessBody);
          const controlled = readinessBody.profiles.find((item) => item.profile === "controlled_real_effect");
          if (controlled) {
            const detail = await api.readinessCurrent(controlled.profile, controlled.capability_scope, readinessBody.bot_qq, readinessBody.process_instance_id);
            if (active) setReadinessDetail(detail);
          }
        } catch {
          if (active) { setReadiness(null); setReadinessDetail(null); }
        }
      } catch {
        if (active) setConnected(false);
      }
    }
    void load();
    const timer = window.setInterval(load, 15_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen((current) => !current);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const current = useMemo(
    () => ALL_ITEMS.find((item) => item.end ? location.pathname === item.to : location.pathname === item.to || location.pathname.startsWith(`${item.to}/`)),
    [location.pathname],
  );

  function chooseTheme(next: ThemeId) {
    setTheme(next);
    setStoredTheme(next);
  }

  function logout() {
    clearSessionToken();
    navigate("/login");
  }

  const controlledReadiness = readiness?.profiles.find((item) => item.profile === "controlled_real_effect");
  const controlledText = !controlledReadiness?.evaluated
    ? "尚未评估"
    : controlledReadiness.stale_probe_count
      ? "证据过期"
      : readinessDetail?.blocker_codes?.includes("durable_pause_active")
        ? "持久暂停"
        : controlledReadiness.status === "passed"
          ? "活动且可执行"
          : controlledReadiness.status === "blocked"
            ? "已阻止"
            : (READINESS_STATUS_LABELS[controlledReadiness.status] ?? controlledReadiness.status);

  return (
    <div className="min-h-screen bg-background text-foreground md:grid md:grid-cols-[256px_minmax(0,1fr)]">
      <aside className="hidden min-h-0 flex-col border-r bg-card md:sticky md:top-0 md:flex md:h-screen md:self-start">
        <Brand />
        <Separator />
        <div className="pt-4" />
        <SidebarNav />
        <div className="border-t p-3">
          <div className="flex items-center gap-3 rounded-md px-2 py-2">
            <Avatar><AvatarFallback>姚</AvatarFallback></Avatar>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">维护者</p>
              <p className="truncate text-xs text-muted-foreground">主控 · 2000000001</p>
            </div>
          </div>
        </div>
      </aside>

      <div className="flex min-h-screen min-w-0 flex-col">
        <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/80 md:px-6">
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="md:hidden" aria-label="打开导航"><Menu className="h-5 w-5" /></Button>
            </SheetTrigger>
            <SheetContent side="left" className="flex w-[280px] flex-col p-0">
              <SheetTitle className="sr-only">YCH 导航</SheetTitle>
              <Brand /><Separator/><div className="pt-4"/><SidebarNav onNavigate={() => setMobileOpen(false)} />
            </SheetContent>
          </Sheet>

          <div className="hidden items-center gap-2 text-sm md:flex">
            <span className="text-muted-foreground">YCH</span>
            <span className="text-muted-foreground">/</span>
            <span className="font-medium">{current?.label ?? "控制台"}</span>
          </div>

          <Button variant="outline" className="ml-0 h-9 flex-1 justify-start text-muted-foreground sm:max-w-xs md:ml-4" onClick={() => setCommandOpen(true)}>
            <Search className="h-4 w-4" />
            <span className="hidden sm:inline">搜索页面与安全对象…</span>
            <span className="sm:hidden">搜索…</span>
          </Button>

          <div className="ml-auto flex items-center gap-1.5">
            <Badge
              variant={controlledReadiness?.status === "passed" && !controlledReadiness.stale_probe_count ? "success" : controlledReadiness?.status === "blocked" ? "destructive" : "secondary"}
              className="hidden gap-1.5 lg:inline-flex"
              title="这是当前进程的受控真实效果证据，不代表仅完成了配置。"
            >
              {CAPABILITY_SCOPE_LABELS[controlledReadiness?.capability_scope ?? "qq_reply"]}：{controlledText}
            </Badge>
            <Button variant="ghost" size="icon" aria-label="切换明暗模式" onClick={() => chooseTheme(theme === "light" ? "dark" : "light")}>
              {theme === "light" ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" className="h-9 gap-2 px-2">
                  <Avatar className="h-7 w-7"><AvatarFallback>姚</AvatarFallback></Avatar>
                  <ChevronDown className="hidden h-3.5 w-3.5 text-muted-foreground sm:block" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuLabel>
                  <span className="block">维护者</span>
                  <span className="block text-xs font-normal text-muted-foreground">YCH 创造者与主控</span>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => navigate("/system")}><Settings className="mr-2 h-4 w-4" />系统与审计</DropdownMenuItem>
                <DropdownMenuItem onSelect={logout} className="text-destructive focus:text-destructive"><LogOut className="mr-2 h-4 w-4" />退出登录</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        {!connected ? <div className="border-b border-destructive/30 bg-destructive/10 px-6 py-2 text-sm text-destructive">本地后端当前不可用。页面保持只读，所有修改操作均不会被视为成功。</div> : null}
        <main className="flex min-w-0 flex-1">
          <div className="mx-auto flex w-full max-w-[1600px] flex-1 flex-col p-4 md:p-6 lg:p-8 [&>*]:flex-1"><Outlet /></div>
        </main>
      </div>

      <Dialog open={commandOpen} onOpenChange={setCommandOpen}>
        <DialogContent className="overflow-hidden p-0 shadow-2xl sm:max-w-xl" aria-describedby={undefined}>
          <DialogTitle className="sr-only">全局搜索</DialogTitle>
          <Command>
            <CommandInput placeholder="搜索页面、功能或安全对象…" />
            <CommandList>
              <CommandEmpty>没有找到匹配项。</CommandEmpty>
              {GROUPS.map((group) => (
                <CommandGroup key={group.id} heading={group.label}>
                  {group.items.map((item) => {
                    const Icon = item.icon;
                    return <CommandItem key={item.to} value={`${item.label} ${item.keywords ?? ""}`} onSelect={() => { navigate(item.to); setCommandOpen(false); }}><Icon className="mr-2 h-4 w-4" />{item.label}<CommandShortcut>跳转</CommandShortcut></CommandItem>;
                  })}
                </CommandGroup>
              ))}
            </CommandList>
          </Command>
        </DialogContent>
      </Dialog>
    </div>
  );
}
