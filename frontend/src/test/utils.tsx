import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";
import { vi } from "vitest";
import { tokenStore } from "@/api/client";
import type { Me, Role } from "@/api/types";
import { TooltipProvider } from "@/components/ui/overlays";
import { AuthProvider } from "@/lib/auth";
import { ThemeProvider } from "@/lib/theme";

export function makeUser(role: Role): Me {
  return { user_id: "u-1", tenant_id: "t-1", email: `${role}@example.com`, role };
}

/** Pretend a user is signed in (token + cached profile), as after a real login. */
export function signIn(role: Role): Me {
  const user = makeUser(role);
  tokenStore.set("test-token");
  localStorage.setItem("atlas.user", JSON.stringify(user));
  return user;
}

type Handler = (request: { url: URL; method: string; body: unknown }) => Response | unknown | Promise<Response | unknown>;

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

export function apiError(status: number, code: string, message: string, extra: Record<string, unknown> = {}, headers: Record<string, string> = {}) {
  return json({ error: { code, message, request_id: "req-123", ...extra } }, status, headers);
}

/**
 * Replace global fetch with a route table keyed by "METHOD /path" (query string ignored).
 * Unmatched requests fail the test loudly with a 599 so missing mocks are obvious.
 */
export function mockFetch(routes: Record<string, Handler>) {
  const calls: Array<{ method: string; path: string; body: unknown }> = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown = undefined;
    if (typeof init?.body === "string") body = JSON.parse(init.body);
    calls.push({ method, path: url.pathname, body });
    const handler = routes[`${method} ${url.pathname}`];
    if (!handler) return json({ error: { code: "unmocked", message: `No mock for ${method} ${url.pathname}`, request_id: null } }, 599);
    const result = await handler({ url, method, body });
    return result instanceof Response ? result : json(result);
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

export function createTestQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
}

/** Render routes inside every app provider, starting at `initialPath`. */
export function renderRoutes(routes: RouteObject[], initialPath: string) {
  const client = createTestQueryClient();
  const router = createMemoryRouter(routes, { initialEntries: [initialPath] });
  const utils = render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <AuthProvider>
            <RouterProvider router={router} />
          </AuthProvider>
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
  return { ...utils, router, client };
}

/** Render a single element at a route path (for components using params/links). */
export function renderPage(element: ReactElement, { path = "/", initialPath = "/" }: { path?: string; initialPath?: string } = {}) {
  return renderRoutes([{ path, element }, { path: "*", element: <p>other page</p> }], initialPath);
}
