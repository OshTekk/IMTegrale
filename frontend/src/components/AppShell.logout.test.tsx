// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { queryKeys } from "../lib/queries";
import type { Dashboard, Session } from "../types";
import { AppShell } from "./AppShell";
import { ToastProvider } from "./Toast";

const account = {
  id: "account-fictif-logout",
  display_name: "[FICTIF] Compte logout",
  imt_username: "logout.fictif@imt-atlantique.fr",
};

const activeSession: Session = {
  authenticated: true,
  role: "owner",
  auth_method: "imt",
  account,
  learning: {
    available: false,
    audience_label: null,
    level_label: null,
    reverify_required: false,
    catalog_version: null,
  },
};

function renderShell(client: QueryClient) {
  client.setQueryData(queryKeys.session, activeSession);
  client.setQueryData(queryKeys.dashboard(account.id), {
    account: { manual_sync: null, last_sync_at: null },
    latest_event_id: 0,
  } as unknown as Dashboard);
  client.setQueryData(["account", account.id, "private-marker"], { value: "still-private" });

  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/app"]}>
          <Routes>
            <Route path="/app" element={<AppShell session={activeSession} preloadRoute={() => undefined} />}>
              <Route index element={<p>[FICTIF] Données privées</p>} />
            </Route>
            <Route path="/" element={<p>[FICTIF] Connexion</p>} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

async function openProfileAndLogout(user = userEvent.setup()) {
  await user.click(screen.getByRole("button", { name: /ouvrir le profil/i }));
  await user.click(screen.getByRole("menuitem", { name: "Se déconnecter" }));
  return user;
}

function sessionBroadcasts(storageWrite: ReturnType<typeof vi.spyOn>) {
  return storageWrite.mock.calls.filter((call: unknown[]) => call[0] === "botnote:session-change");
}

function requestsTo(fetchMock: ReturnType<typeof vi.fn>, pathname: string) {
  return fetchMock.mock.calls.filter(([input]) => {
    const value = input instanceof Request ? input.url : String(input);
    return new URL(value, window.location.origin).pathname === pathname;
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
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
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("AppShell server-confirmed logout", () => {
  it("keeps the authenticated UI and account cache when transport fails before revocation", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = new URL(input instanceof Request ? input.url : input.toString(), window.location.origin);
      if (url.pathname === "/api/v1/auth/logout") {
        return Promise.reject(new TypeError("synthetic transport failure before server"));
      }
      if (url.pathname === "/api/v1/auth/session") {
        return Promise.resolve(
          new Response(JSON.stringify(activeSession), {
            status: 200,
            headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
          }),
        );
      }
      throw new Error(`Unexpected synthetic request: ${url.pathname}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderShell(client);

    const user = await openProfileAndLogout();

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByText("[FICTIF] Connexion")).toBeNull();
    expect(screen.getByText("[FICTIF] Données privées")).toBeTruthy();
    expect(client.getQueryData(["account", account.id, "private-marker"])).toEqual({
      value: "still-private",
    });
    expect(storageWrite).not.toHaveBeenCalled();
    expect(await screen.findByRole("dialog", { name: "Déconnexion non confirmée" })).toBeTruthy();
    expect(screen.getByText("La session est encore active. Réessaie dans un instant.")).toBeTruthy();
    expect(requestsTo(fetchMock, "/api/v1/auth/logout")).toHaveLength(1);
    expect(requestsTo(fetchMock, "/api/v1/auth/session")).toHaveLength(1);
    await waitFor(() => expect(screen.getByRole("button", { name: "Réessayer" })).toBe(document.activeElement));

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("menuitem", { name: "Se déconnecter" })).toBe(document.activeElement);
  });

  it("finalizes exactly once when a retry succeeds after a confirmed failure", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic transport failure before server"))
      .mockResolvedValueOnce(
        new Response(JSON.stringify(activeSession), {
          status: 200,
          headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderShell(client);

    const user = await openProfileAndLogout();

    expect(await screen.findByRole("dialog", { name: "Déconnexion non confirmée" })).toBeTruthy();
    expect(client.getQueryData(["account", account.id, "private-marker"])).toEqual({ value: "still-private" });
    expect(sessionBroadcasts(storageWrite)).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Réessayer" }));

    expect(await screen.findByText("[FICTIF] Connexion")).toBeTruthy();
    expect(requestsTo(fetchMock, "/api/v1/auth/logout")).toHaveLength(2);
    expect(requestsTo(fetchMock, "/api/v1/auth/session")).toHaveLength(1);
    expect(sessionBroadcasts(storageWrite)).toHaveLength(1);
  });

  it("purges queries and mutations, broadcasts once, and navigates only after normal confirmation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.getMutationCache().build(client, {
      mutationKey: ["account", account.id, "private-mutation"],
      mutationFn: async () => ({ ok: true }),
    });
    renderShell(client);

    await openProfileAndLogout();

    expect(await screen.findByText("[FICTIF] Connexion")).toBeTruthy();
    expect(requestsTo(fetchMock, "/api/v1/auth/logout")).toHaveLength(1);
    expect(client.getQueryCache().findAll({ queryKey: ["account", account.id] })).toHaveLength(0);
    expect(client.getQueryData(queryKeys.session)).toEqual({ authenticated: false });
    expect(client.getMutationCache().getAll()).toHaveLength(0);
    expect(sessionBroadcasts(storageWrite)).toHaveLength(1);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("recovers a lost success response through an anonymous session read", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic response lost after commit"))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ authenticated: false }), {
          status: 200,
          headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderShell(client);

    await openProfileAndLogout();

    expect(await screen.findByText("[FICTIF] Connexion")).toBeTruthy();
    expect(requestsTo(fetchMock, "/api/v1/auth/logout")).toHaveLength(1);
    expect(requestsTo(fetchMock, "/api/v1/auth/session")).toHaveLength(1);
    expect(client.getQueryCache().findAll({ queryKey: ["account", account.id] })).toHaveLength(0);
    expect(client.getQueryData(queryKeys.session)).toEqual({ authenticated: false });
    expect(sessionBroadcasts(storageWrite)).toHaveLength(1);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps account state on double network failure and finalizes exactly once after retry", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic offline logout"))
      .mockRejectedValueOnce(new TypeError("synthetic offline verification"))
      .mockRejectedValueOnce(new TypeError("synthetic response lost after retry commit"))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ authenticated: false }), {
          status: 200,
          headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderShell(client);

    const user = await openProfileAndLogout();

    expect(await screen.findByRole("dialog", { name: "Impossible de confirmer la déconnexion" })).toBeTruthy();
    expect(
      screen.getByText(
        "L’état de la session n’a pas pu être vérifié. Réessaie ou recharge la page avant de quitter cet appareil.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Recharger la page" })).toBeTruthy();
    expect(client.getQueryData(["account", account.id, "private-marker"])).toEqual({ value: "still-private" });
    expect(sessionBroadcasts(storageWrite)).toHaveLength(0);
    expect(screen.queryByText("[FICTIF] Connexion")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Réessayer" }));

    expect(await screen.findByText("[FICTIF] Connexion")).toBeTruthy();
    expect(requestsTo(fetchMock, "/api/v1/auth/logout")).toHaveLength(2);
    expect(requestsTo(fetchMock, "/api/v1/auth/session")).toHaveLength(2);
    expect(sessionBroadcasts(storageWrite)).toHaveLength(1);
  });

  it("coalesces a double click into one logout request while the button is busy", async () => {
    const pendingLogout = deferred<Response>();
    const fetchMock = vi.fn().mockImplementation(() => pendingLogout.promise);
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderShell(client);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /ouvrir le profil/i }));
    const button = screen.getByRole("menuitem", { name: "Se déconnecter" });
    await user.dblClick(button);

    expect(requestsTo(fetchMock, "/api/v1/auth/logout")).toHaveLength(1);
    expect((screen.getByRole("menuitem", { name: "Déconnexion…" }) as HTMLButtonElement).disabled).toBe(true);
    expect(sessionBroadcasts(storageWrite)).toHaveLength(0);

    pendingLogout.resolve(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    expect(await screen.findByText("[FICTIF] Connexion")).toBeTruthy();
    expect(sessionBroadcasts(storageWrite)).toHaveLength(1);
  });

  it("purges principal A without presenting a concurrent principal B as anonymous", async () => {
    const sessionB = {
      ...activeSession,
      account: { ...account, id: "account-fictif-b", display_name: "[FICTIF] Compte B" },
    };
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic ambiguous logout"))
      .mockResolvedValueOnce(
        new Response(JSON.stringify(sessionB), {
          status: 200,
          headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderShell(client);

    await openProfileAndLogout();

    await waitFor(() => expect(client.getQueryData(["account", account.id, "private-marker"])).toBeUndefined());
    expect(client.getQueryData(queryKeys.session)).toEqual(sessionB);
    expect(screen.queryByText("[FICTIF] Connexion")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(sessionBroadcasts(storageWrite)).toHaveLength(1);
  });

  it("prevents a late account query from republishing private data after confirmation", async () => {
    const lateQuery = deferred<{ private: string }>();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const lateKey = ["account", account.id, "late-private-query"] as const;
    const lateRequest = client
      .fetchQuery({ queryKey: lateKey, queryFn: () => lateQuery.promise })
      .catch(() => undefined);
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderShell(client);

    await openProfileAndLogout();
    expect(await screen.findByText("[FICTIF] Connexion")).toBeTruthy();

    lateQuery.resolve({ private: "must-not-return" });
    await lateRequest;
    expect(client.getQueryData(lateKey)).toBeUndefined();
  });
});
