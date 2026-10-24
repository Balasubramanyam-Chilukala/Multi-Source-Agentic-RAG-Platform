import { lazy, type ReactNode } from "react";
import { Navigate, Outlet, createBrowserRouter, useLocation, useSearchParams, type RouteObject } from "react-router";
import { AppShell } from "@/components/layout/AppShell";
import { useAuth } from "@/lib/auth";
import { RouteError } from "@/features/RouteError";

const LoginPage = lazy(() => import("@/features/auth/AuthPages").then((m) => ({ default: m.LoginPage })));
const RegisterPage = lazy(() => import("@/features/auth/AuthPages").then((m) => ({ default: m.RegisterPage })));
const DashboardPage = lazy(() => import("@/features/dashboard/DashboardPage"));
const CollectionsPage = lazy(() => import("@/features/knowledge/CollectionsPage"));
const CollectionDetailPage = lazy(() => import("@/features/knowledge/CollectionDetailPage"));
const ChatPage = lazy(() => import("@/features/chat/ChatPage"));
const EvaluationsPage = lazy(() => import("@/features/evaluations/EvaluationsPage"));
const EvaluationRunPage = lazy(() => import("@/features/evaluations/EvaluationRunPage"));
const RunsPage = lazy(() => import("@/features/runs/RunsPage"));
const RunDetailPage = lazy(() => import("@/features/runs/RunDetailPage"));
const SettingsPage = lazy(() => import("@/features/settings/SettingsPage"));

/** Redirects to /login (remembering where the user was going) when there is no session. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useAuth();
  const location = useLocation();
  if (!isAuthenticated) {
    const next = location.pathname + location.search;
    return <Navigate to={`/login${next && next !== "/" ? `?next=${encodeURIComponent(next)}` : ""}`} replace />;
  }
  return <>{children}</>;
}

/** Only allow in-app relative paths as a post-login destination (no open redirects). */
export function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

function GuestOnly() {
  const { isAuthenticated } = useAuth();
  const [params] = useSearchParams();
  return isAuthenticated ? <Navigate to={safeNext(params.get("next"))} replace /> : <Outlet />;
}

export const routes: RouteObject[] = [
  {
    errorElement: <RouteError />,
    children: [
      {
        element: <GuestOnly />,
        children: [
          { path: "/login", element: <LoginPage /> },
          { path: "/register", element: <RegisterPage /> },
        ],
      },
      {
        element: (
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        ),
        children: [
          { index: true, element: <DashboardPage /> },
          { path: "knowledge", element: <CollectionsPage /> },
          { path: "knowledge/:collectionId", element: <CollectionDetailPage /> },
          // One optional-segment route so the page is not remounted when a new conversation gets its id.
          { path: "chat/:conversationId?", element: <ChatPage /> },
          { path: "evaluations", element: <EvaluationsPage /> },
          { path: "evaluations/:runId", element: <EvaluationRunPage /> },
          { path: "runs", element: <RunsPage /> },
          { path: "runs/:runId", element: <RunDetailPage /> },
          { path: "settings", element: <SettingsPage /> },
          { path: "*", element: <RouteError notFound /> },
        ],
      },
    ],
  },
];

export const createAppRouter = () => createBrowserRouter(routes);
