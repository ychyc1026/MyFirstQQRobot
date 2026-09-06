import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "./components/ui/sonner";
import { getSessionToken } from "./lib/auth";

const AppShell = lazy(() =>
  import("./components/AppShell").then(({ AppShell }) => ({
    default: AppShell,
  })),
);
const AccountsPage = lazy(() =>
  import("./pages/AccountsPage").then(({ AccountsPage }) => ({
    default: AccountsPage,
  })),
);
const ActivityPage = lazy(() => import("./pages/ActivityPage").then(({ ActivityPage }) => ({ default: ActivityPage })));
const ApprovalsPage = lazy(() =>
  import("./pages/ApprovalsPage").then(({ ApprovalsPage }) => ({
    default: ApprovalsPage,
  })),
);
const ChatlogPage = lazy(() =>
  import("./pages/ChatlogPage").then(({ ChatlogPage }) => ({
    default: ChatlogPage,
  })),
);
const ConversationsPage = lazy(() =>
  import("./pages/ConversationsPage").then(({ ConversationsPage }) => ({
    default: ConversationsPage,
  })),
);
const DiaryPage = lazy(() =>
  import("./pages/DiaryPage").then(({ DiaryPage }) => ({ default: DiaryPage })),
);
const HandbookPage = lazy(() =>
  import("./pages/HandbookPage").then(({ HandbookPage }) => ({
    default: HandbookPage,
  })),
);
const KnowledgePage = lazy(() =>
  import("./pages/KnowledgePage").then(({ KnowledgePage }) => ({
    default: KnowledgePage,
  })),
);
const LoginPage = lazy(() =>
  import("./pages/LoginPage").then(({ LoginPage }) => ({ default: LoginPage })),
);
const MaterialsPage = lazy(() =>
  import("./pages/MaterialsPage").then(({ MaterialsPage }) => ({
    default: MaterialsPage,
  })),
);
const MemoriesPage = lazy(() =>
  import("./pages/MemoriesPage").then(({ MemoriesPage }) => ({
    default: MemoriesPage,
  })),
);
const OpsPage = lazy(() =>
  import("./pages/OpsPage").then(({ OpsPage }) => ({ default: OpsPage })),
);
const PipelinePage = lazy(() =>
  import("./pages/PipelinePage").then(({ PipelinePage }) => ({
    default: PipelinePage,
  })),
);
const QualificationPage = lazy(() =>
  import("./pages/QualificationPage").then(({ QualificationPage }) => ({
    default: QualificationPage,
  })),
);
const OverviewPage = lazy(() =>
  import("./pages/OverviewPage").then(({ OverviewPage }) => ({
    default: OverviewPage,
  })),
);
const PersonasPage = lazy(() =>
  import("./pages/PersonasPage").then(({ PersonasPage }) => ({
    default: PersonasPage,
  })),
);
const ProactivePage = lazy(() =>
  import("./pages/ProactivePage").then(({ ProactivePage }) => ({
    default: ProactivePage,
  })),
);
const PrivacyPage = lazy(() =>
  import("./pages/PrivacyPage").then(({ PrivacyPage }) => ({
    default: PrivacyPage,
  })),
);
const QzonePage = lazy(() =>
  import("./pages/QzonePage").then(({ QzonePage }) => ({ default: QzonePage })),
);
const QuotasPage = lazy(() =>
  import("./pages/QuotasPage").then(({ QuotasPage }) => ({
    default: QuotasPage,
  })),
);
const StatsPage = lazy(() =>
  import("./pages/StatsPage").then(({ StatsPage }) => ({ default: StatsPage })),
);
const SystemPage = lazy(() =>
  import("./pages/SystemPage").then(({ SystemPage }) => ({
    default: SystemPage,
  })),
);
const UsersPage = lazy(() =>
  import("./pages/UsersPage").then(({ UsersPage }) => ({ default: UsersPage })),
);

function RequireSession({ children }: { children: ReactNode }) {
  if (!getSessionToken()) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

export default function App() {
  return (
    <><Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background text-sm text-muted-foreground">
          正在载入 YCH 控制台…
        </div>
      }
    >
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <RequireSession>
              <AppShell />
            </RequireSession>
          }
        >
          <Route path="/" element={<OverviewPage />} />
          <Route path="/ops" element={<OpsPage />} />
          <Route path="/pipeline" element={<PipelinePage />} />
          <Route path="/models" element={<QualificationPage />} />
          <Route path="/stats" element={<StatsPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/activity" element={<ActivityPage />} />
          <Route path="/quotas" element={<QuotasPage />} />
          <Route path="/conversations" element={<ConversationsPage />} />
          <Route path="/chatlog" element={<ChatlogPage />} />
          <Route path="/handbook" element={<HandbookPage />} />
          <Route path="/users" element={<UsersPage />} />
          <Route path="/personas" element={<PersonasPage />} />
          <Route path="/memories" element={<MemoriesPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/qzone" element={<QzonePage />} />
          <Route path="/proactive" element={<ProactivePage />} />
          <Route path="/diary" element={<DiaryPage />} />
          <Route path="/materials" element={<MaterialsPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/privacy" element={<PrivacyPage />} />
          <Route path="/system" element={<SystemPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense><Toaster /></>
  );
}
