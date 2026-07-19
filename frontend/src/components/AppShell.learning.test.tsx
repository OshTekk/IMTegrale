// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { queryKeys } from "../lib/queries";
import type { Dashboard, Session } from "../types";
import { AppShell } from "./AppShell";
import { ToastProvider } from "./Toast";

const account = {
  id: "account-fictif-shell",
  display_name: "[FICTIF] Compte de test",
  imt_username: "compte.fictif",
};

function learningSession(authMethod: "imt" | "token"): Session {
  return {
    authenticated: true,
    role: "owner",
    auth_method: authMethod,
    account,
    learning: {
      available: true,
      audience_label: "[FICTIF] FIP 2028",
      level_label: "[FICTIF] 2A",
      reverify_required: false,
      catalog_version: "catalogue-fictif-v1",
    },
  };
}

function shell(client: QueryClient, session: Session) {
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/"]}>
          <Routes>
            <Route element={<AppShell session={session} preloadRoute={() => undefined} />}>
              <Route index element={<p>[FICTIF] Vue d'ensemble</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AppShell learning entry points", () => {
  it("removes navigation and CTA immediately when a primary session becomes a token", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const primary = learningSession("imt");
    client.setQueryData(queryKeys.session, primary);
    client.setQueryData(queryKeys.dashboard(account.id), {
      account: { manual_sync: undefined, last_sync_at: null },
      latest_event_id: undefined,
    } as unknown as Dashboard);

    const view = render(shell(client, primary));
    expect(screen.getByRole("link", { name: "Parcours" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Réussir ma 2A" })).toBeTruthy();
    expect(screen.getByText("Synchroniser")).toBeTruthy();

    const delegated = learningSession("token");
    client.setQueryData(queryKeys.session, delegated);
    view.rerender(shell(client, delegated));

    expect(screen.queryByRole("link", { name: "Parcours" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Réussir ma 2A" })).toBeNull();
    expect(screen.queryByText("Synchroniser")).toBeNull();
  });
});
