// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { queryKeys } from "../lib/queries";
import type { Dashboard, Session } from "../types";
import { AppShell } from "./AppShell";
import { ToastProvider } from "./Toast";

const accountA = {
  id: "account-fictif-logout-a",
  display_name: "[FICTIF] Compte logout A",
  imt_username: "logout.a.fictif@example.test",
};

const activeSessionA: Session = {
  authenticated: true,
  role: "owner",
  auth_method: "imt",
  needs_security_setup: false,
  needs_sync_setup: false,
  account: accountA,
  learning: {
    available: false,
    audience_label: null,
    level_label: null,
    reverify_required: false,
    catalog_version: null,
  },
};

const activeSessionB: Session = {
  ...activeSessionA,
  account: {
    id: "account-fictif-logout-b",
    display_name: "[FICTIF] Compte logout B",
    imt_username: "logout.b.fictif@example.test",
  },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store" },
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

function seedClient(client: QueryClient, session: Session = activeSessionA): void {
  const accountId = session.account!.id;
  client.setQueryData(queryKeys.session, session);
  client.setQueryData(queryKeys.dashboard(accountId), {
    account: { manual_sync: null, last_sync_at: null },
    latest_event_id: 0,
  } as unknown as Dashboard);
  client.setQueryData(["account", accountId, "private-marker"], { value: `private-${accountId}` });
}

function shellTree(client: QueryClient, session: Session, strict = false): ReactNode {
  const tree = (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/app"]}>
          <Routes>
            <Route path="/app" element={<AppShell session={session} preloadRoute={() => undefined} />}>
              <Route index element={<p>[FICTIF] Données privées</p>} />
            </Route>
            <Route path="/" element={<p>[FICTIF] Connexion</p>} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
  return strict ? <StrictMode>{tree}</StrictMode> : tree;
}

function renderShell(client: QueryClient, session: Session = activeSessionA, strict = false) {
  seedClient(client, session);
  return render(shellTree(client, session, strict));
}

async function clickLogout(user = userEvent.setup()) {
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

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  );
  vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(true);
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("AppShell server-confirmed logout", () => {
  it("keeps authority, caches, and other tabs intact when the same principal is still active", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic transport failure before commit"))
      .mockResolvedValueOnce(jsonResponse(activeSessionA));
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderShell(client);

    const user = await clickLogout();

    expect(await screen.findByRole("dialog", { name: "Session active" })).toBeTruthy();
    expect(screen.getByText("[FICTIF] Données privées")).toBeTruthy();
    expect(client.getQueryData(["account", accountA.id, "private-marker"])).toEqual({
      value: `private-${accountA.id}`,
    });
    expect(client.getQueryData(queryKeys.session)).toEqual(activeSessionA);
    expect(sessionBroadcasts(storageWrite)).toHaveLength(0);
    expect(requestsTo(fetchMock, "/api/v1/auth/logout")).toHaveLength(1);
    expect(requestsTo(fetchMock, "/api/v1/auth/session")).toHaveLength(1);
    await waitFor(() => expect(screen.getByRole("button", { name: "Réessayer" })).toBe(document.activeElement));

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    const logoutButton = screen.getByRole("menuitem", { name: "Se déconnecter" });
    expect(logoutButton).toBe(document.activeElement);
    expect((logoutButton as HTMLButtonElement).disabled).toBe(false);
  });

  it("retries after failed verification without publishing success prematurely", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic transport failure before commit"))
      .mockResolvedValueOnce(jsonResponse(activeSessionA))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderShell(client);

    const user = await clickLogout();

    expect(await screen.findByRole("dialog", { name: "Session active" })).toBeTruthy();
    expect(client.getQueryData(["account", accountA.id, "private-marker"])).toBeDefined();
    expect(client.getQueryData(queryKeys.session)).toEqual(activeSessionA);
    expect(sessionBroadcasts(storageWrite)).toHaveLength(0);
    expect(screen.queryByText("[FICTIF] Connexion")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Réessayer" }));

    expect(await screen.findByText("[FICTIF] Connexion")).toBeTruthy();
    expect(requestsTo(fetchMock, "/api/v1/auth/logout")).toHaveLength(2);
    expect(requestsTo(fetchMock, "/api/v1/auth/session")).toHaveLength(1);
    expect(client.getQueryData(["account", accountA.id, "private-marker"])).toBeUndefined();
    expect(sessionBroadcasts(storageWrite)).toHaveLength(1);
  });

  it("shows requesting then verifying while preserving query and mutation state", async () => {
    const logoutResponse = deferred<Response>();
    const verificationResponse = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => logoutResponse.promise)
      .mockImplementationOnce(() => verificationResponse.promise);
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.getMutationCache().build(client, {
      mutationKey: ["account", accountA.id, "private-mutation"],
      mutationFn: async () => ({ ok: true }),
    });
    renderShell(client);

    void clickLogout();
    await waitFor(() =>
      expect(screen.getByRole("menuitem", { name: "Se déconnecter" }).getAttribute("aria-busy")).toBe("true"),
    );
    expect(client.getQueryData(["account", accountA.id, "private-marker"])).toBeDefined();
    expect(client.getMutationCache().getAll()).toHaveLength(1);

    logoutResponse.reject(new TypeError("synthetic ambiguous transport"));
    await waitFor(() =>
      expect(screen.getByRole("menuitem", { name: "Se déconnecter" }).getAttribute("aria-busy")).toBe("true"),
    );
    expect(client.getQueryData(["account", accountA.id, "private-marker"])).toBeDefined();
    expect(client.getMutationCache().getAll()).toHaveLength(1);

    verificationResponse.resolve(jsonResponse(activeSessionA));
    expect(await screen.findByRole("dialog", { name: "Session active" })).toBeTruthy();
  });

  it("purges both caches, installs anonymity, broadcasts once, and navigates only after confirmation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.getMutationCache().build(client, {
      mutationKey: ["account", accountA.id, "private-mutation"],
      mutationFn: async () => ({ ok: true }),
    });
    renderShell(client);

    await clickLogout();

    expect(await screen.findByText("[FICTIF] Connexion")).toBeTruthy();
    expect(client.getQueryCache().findAll({ queryKey: ["account", accountA.id] })).toHaveLength(0);
    expect(client.getMutationCache().getAll()).toHaveLength(0);
    expect(client.getQueryData(queryKeys.session)).toEqual({ authenticated: false });
    expect(sessionBroadcasts(storageWrite)).toHaveLength(1);
    expect(requestsTo(fetchMock, "/api/v1/auth/session")).toHaveLength(0);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("recovers a response lost after commit through an anonymous no-store read", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic response lost after commit"))
      .mockResolvedValueOnce(jsonResponse({ authenticated: false }));
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderShell(client);

    await clickLogout();

    expect(await screen.findByText("[FICTIF] Connexion")).toBeTruthy();
    expect(requestsTo(fetchMock, "/api/v1/auth/session")).toHaveLength(1);
    expect(client.getQueryData(queryKeys.session)).toEqual({ authenticated: false });
    expect(sessionBroadcasts(storageWrite)).toHaveLength(1);
  });

  it("keeps authority indeterminate offline, then retries from the keyboard without false success", async () => {
    const online = vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderShell(client);

    const user = await clickLogout();

    expect(await screen.findByRole("dialog", { name: "Session inconnue" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Recharger" })).toBeTruthy();
    expect(client.getQueryData(["account", accountA.id, "private-marker"])).toBeDefined();
    expect(sessionBroadcasts(storageWrite)).toHaveLength(0);
    expect(fetchMock).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Réessayer" })).toBe(document.activeElement));

    online.mockReturnValue(true);
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await user.keyboard("{Enter}");

    expect(await screen.findByText("[FICTIF] Connexion")).toBeTruthy();
    expect(requestsTo(fetchMock, "/api/v1/auth/logout")).toHaveLength(1);
    expect(sessionBroadcasts(storageWrite)).toHaveLength(1);
  });

  it("coalesces a double click into one POST and exposes aria-busy", async () => {
    const pendingLogout = deferred<Response>();
    const fetchMock = vi.fn().mockImplementation(() => pendingLogout.promise);
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderShell(client, activeSessionA, true);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /ouvrir le profil/i }));
    const button = screen.getByRole("menuitem", { name: "Se déconnecter" });
    await user.dblClick(button);

    expect(requestsTo(fetchMock, "/api/v1/auth/logout")).toHaveLength(1);
    await waitFor(() =>
      expect(screen.getByRole("menuitem", { name: "Se déconnecter" }).getAttribute("aria-busy")).toBe("true"),
    );
    expect(sessionBroadcasts(storageWrite)).toHaveLength(0);

    pendingLogout.resolve(jsonResponse({ ok: true }));
    expect(await screen.findByText("[FICTIF] Connexion")).toBeTruthy();
    expect(sessionBroadcasts(storageWrite)).toHaveLength(1);
  });

  it("purges principal A, installs principal B, and emits only a generic session change", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic ambiguous logout"))
      .mockResolvedValueOnce(jsonResponse(activeSessionB));
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderShell(client);

    await clickLogout();

    await waitFor(() => expect(client.getQueryData(queryKeys.session)).toEqual(activeSessionB));
    expect(client.getQueryData(["account", accountA.id, "private-marker"])).toBeUndefined();
    client.setQueryData(["account", activeSessionB.account!.id, "private-marker"], { value: "private-b" });
    expect(client.getQueryData(["account", activeSessionB.account!.id, "private-marker"])).toEqual({
      value: "private-b",
    });
    expect(screen.queryByText("[FICTIF] Connexion")).toBeNull();
    expect(sessionBroadcasts(storageWrite)).toHaveLength(1);
  });

  it("prevents a late query from republishing principal A after confirmation", async () => {
    const lateQuery = deferred<{ private: string }>();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const lateKey = ["account", accountA.id, "late-private-query"] as const;
    const lateRequest = client
      .fetchQuery({ queryKey: lateKey, queryFn: () => lateQuery.promise })
      .catch(() => undefined);
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    renderShell(client);

    await clickLogout();
    expect(await screen.findByText("[FICTIF] Connexion")).toBeTruthy();

    lateQuery.resolve({ private: "must-not-return" });
    await lateRequest;
    expect(client.getQueryData(lateKey)).toBeUndefined();
  });

  it("prevents a late mutation callback from republishing principal A after confirmation", async () => {
    const lateMutation = deferred<{ private: string }>();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const lateKey = ["account", accountA.id, "late-private-mutation"] as const;
    const mutation = client.getMutationCache().build(client, {
      mutationKey: lateKey,
      mutationFn: () => lateMutation.promise,
      onSuccess: (data) => client.setQueryData(lateKey, data),
    });
    const mutationRequest = mutation.execute(undefined).catch(() => undefined);
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    renderShell(client);

    await clickLogout();
    expect(await screen.findByText("[FICTIF] Connexion")).toBeTruthy();

    lateMutation.resolve({ private: "must-not-return" });
    await mutationRequest;
    await waitFor(() => expect(client.getQueryData(lateKey)).toBeUndefined());
  });

  it("ignores a late response after the principal changes", async () => {
    const pendingLogout = deferred<Response>();
    const fetchMock = vi.fn().mockImplementation(() => pendingLogout.promise);
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = renderShell(client);

    void clickLogout();
    await waitFor(() =>
      expect(screen.getByRole("menuitem", { name: "Se déconnecter" }).getAttribute("aria-busy")).toBe("true"),
    );

    seedClient(client, activeSessionB);
    view.rerender(shellTree(client, activeSessionB));
    pendingLogout.resolve(jsonResponse({ ok: true }));

    await waitFor(() =>
      expect(screen.getByRole("menuitem", { name: "Se déconnecter" }).getAttribute("aria-busy")).toBe("false"),
    );
    expect(client.getQueryData(queryKeys.session)).toEqual(activeSessionB);
    expect(client.getQueryData(["account", accountA.id, "private-marker"])).toBeDefined();
    expect(sessionBroadcasts(storageWrite)).toHaveLength(0);
  });

  it("ignores a response that arrives after unmount", async () => {
    const pendingLogout = deferred<Response>();
    const fetchMock = vi.fn().mockImplementation(() => pendingLogout.promise);
    vi.stubGlobal("fetch", fetchMock);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = renderShell(client);

    void clickLogout();
    await waitFor(() =>
      expect(screen.getByRole("menuitem", { name: "Se déconnecter" }).getAttribute("aria-busy")).toBe("true"),
    );
    view.unmount();
    pendingLogout.resolve(jsonResponse({ ok: true }));
    await Promise.resolve();
    await Promise.resolve();

    expect(client.getQueryData(queryKeys.session)).toEqual(activeSessionA);
    expect(client.getQueryData(["account", accountA.id, "private-marker"])).toBeDefined();
    expect(sessionBroadcasts(storageWrite)).toHaveLength(0);
  });
});
