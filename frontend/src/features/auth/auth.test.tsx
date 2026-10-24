import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { tokenStore } from "@/api/client";
import { routes } from "@/router";
import { apiError, mockFetch, renderRoutes, signIn } from "@/test/utils";

afterEach(() => vi.unstubAllGlobals());

describe("auth", () => {
  it("redirects to /login when there is no session", async () => {
    tokenStore.set(null);
    mockFetch({});
    const { router } = renderRoutes(routes, "/runs");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.location.search).toBe("?next=%2Fruns");
  });

  it("a 401 from any API call clears the token and redirects to login", async () => {
    signIn("member");
    mockFetch({
      "GET /api/v1/auth/me": () => apiError(401, "unauthenticated", "Token expired"),
      "GET /api/v1/dashboard": () => apiError(401, "unauthenticated", "Token expired"),
    });
    const { router } = renderRoutes(routes, "/");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(tokenStore.get()).toBeNull();
    expect(localStorage.getItem("atlas.token")).toBeNull();
  });

  it("login stores the token and returns to the requested page", async () => {
    tokenStore.set(null);
    const { calls } = mockFetch({
      "POST /api/v1/auth/login": () => ({
        access_token: "new-token",
        token_type: "bearer",
        expires_in: 3600,
        user: { user_id: "u", tenant_id: "t", email: "a@example.com", role: "admin" },
      }),
      "GET /api/v1/runs": () => ({ items: [], total: 0, limit: 25, offset: 0 }),
    });
    const { router } = renderRoutes(routes, "/login?next=%2Fruns");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "a@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/runs"));
    expect(tokenStore.get()).toBe("new-token");
    expect(calls[0]?.body).toEqual({ email: "a@example.com", password: "correct-horse-battery" });
  });

  it("shows the error message and request id on failed login", async () => {
    tokenStore.set(null);
    mockFetch({ "POST /api/v1/auth/login": () => apiError(401, "unauthenticated", "Invalid email or password") });
    renderRoutes(routes, "/login");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "a@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Invalid email or password")).toBeInTheDocument();
    expect(screen.getByText(/Request ID: req-123/)).toBeInTheDocument();
  });
});
