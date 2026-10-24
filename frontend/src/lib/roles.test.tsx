import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Role } from "@/api/types";
import CollectionsPage from "@/features/knowledge/CollectionsPage";
import EvaluationsPage from "@/features/evaluations/EvaluationsPage";
import { mockFetch, renderPage, signIn } from "@/test/utils";
import { hasRole, permissions } from "./roles";

const collection = {
  id: "col-1",
  name: "Handbook",
  description: null,
  created_at: "2026-10-24T10:00:00Z",
  updated_at: "2026-10-24T10:00:00Z",
  document_count: 2,
  ready_document_count: 2,
  chunk_count: 10,
  active_embedding_model: "hashing-embed",
};

function setup(role: Role) {
  signIn(role);
  mockFetch({
    "GET /api/v1/auth/me": () => ({ user_id: "u", tenant_id: "t", email: "x@example.com", role }),
    "GET /api/v1/collections": () => ({ items: [collection], total: 1, limit: 100, offset: 0 }),
    "GET /api/v1/evaluations/datasets": () => [{ name: "atlas-handbook-v1", version: "1", case_count: 60, categories: { factual: 40 }, document_count: 8 }],
    "GET /api/v1/evaluations": () => ({ items: [], total: 0, limit: 50, offset: 0 }),
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("role hierarchy", () => {
  it("orders viewer < member < admin < owner", () => {
    expect(hasRole("viewer", "member")).toBe(false);
    expect(hasRole("member", "member")).toBe(true);
    expect(hasRole("admin", "member")).toBe(true);
    expect(hasRole("owner", "admin")).toBe(true);
    expect(hasRole(null, "viewer")).toBe(false);
    expect(permissions.deleteCollections("member")).toBe(false);
    expect(permissions.runEvaluations("admin")).toBe(true);
  });
});

describe("role-gated actions", () => {
  it("viewer cannot create or manage collections", async () => {
    setup("viewer");
    renderPage(<CollectionsPage />);
    expect(await screen.findByText("Handbook")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /New collection/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Actions for Handbook" })).not.toBeInTheDocument();
  });

  it("member can create and rename but not delete collections", async () => {
    setup("member");
    renderPage(<CollectionsPage />);
    expect(await screen.findByRole("button", { name: /New collection/ })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Actions for Handbook" }));
    expect(await screen.findByRole("menuitem", { name: /Rename/ })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /Delete/ })).not.toBeInTheDocument();
  });

  it("admin can delete collections", async () => {
    setup("admin");
    renderPage(<CollectionsPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Actions for Handbook" }));
    expect(await screen.findByRole("menuitem", { name: /Delete/ })).toBeInTheDocument();
  });

  it("'Run evaluation' is disabled for members and enabled for admins", async () => {
    setup("member");
    const { unmount } = renderPage(<EvaluationsPage />);
    expect(await screen.findByTestId("gated-runEvaluations")).toBeInTheDocument();
    unmount();
    setup("owner");
    renderPage(<EvaluationsPage />);
    expect(await screen.findByText("atlas-handbook-v1")).toBeInTheDocument();
    expect(screen.queryByTestId("gated-runEvaluations")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Run evaluation/ })[0]).toBeEnabled();
  });
});
