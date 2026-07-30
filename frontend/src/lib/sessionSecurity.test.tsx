// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Session } from "../types";
import { queryKeys } from "./queries";
import { SessionSecurityBoundary, useSessionSecurity } from "./sessionSecurity";

const SCOPE_A = `bss1_${"a".repeat(64)}`;
const SCOPE_B = `bss1_${"b".repeat(64)}`;
const SERVER_TIME = "2099-07-30T12:00:00.000Z";

function authenticatedSession(
  scope = SCOPE_A,
  expiresAt = "2099-07-30T12:05:00.000Z",
  serverTime = SERVER_TIME,
): Session {
  return {
    authenticated: true,
    session_scope: scope,
    session_expires_at: expiresAt,
    server_time: serverTime,
    role: "owner",
    auth_method: "imt",
    account: {
      id: scope === SCOPE_A ? "account-a" : "account-b",
      display_name: scope === SCOPE_A ? "Compte A" : "Compte B",
      imt_username: null,
    },
    private_comparisons: { available: true },
  };
}

function SecurityProbe() {
  const security = useSessionSecurity();
  return (
    <>
      <span data-testid="security-state">{security.status}</span>
      <span>DONNEE_PRIVEE_SYNTHETIQUE</span>
    </>
  );
}

function Providers({
  children,
  client,
  session,
  refetchSession,
}: {
  children: ReactNode;
  client: QueryClient;
  session: Session;
  refetchSession: () => Promise<Session | undefined>;
}) {
  return (
    <QueryClientProvider client={client}>
      <SessionSecurityBoundary session={session} sessionPending={false} refetchSession={refetchSession}>
        {children}
      </SessionSecurityBoundary>
    </QueryClientProvider>
  );
}

function persistedPageTransition(type: "pagehide" | "pageshow"): PageTransitionEvent {
  const event = new Event(type) as PageTransitionEvent;
  Object.defineProperty(event, "persisted", { value: true });
  return event;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-session-security");
  vi.restoreAllMocks();
});

describe("SessionSecurityBoundary", () => {
  it("expire sur la deadline monotone hors ligne et purge données et mutations", async () => {
    vi.useFakeTimers();
    const client = new QueryClient();
    client.setQueryData(queryKeys.privateComparison("account-a", `pc_${"x".repeat(24)}`), {
      confidential: true,
    });
    client.getMutationCache().build(client, {
      mutationFn: async () => undefined,
      meta: { privateComparisonSecurity: true },
    });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
    const session = authenticatedSession(SCOPE_A, "2099-07-30T12:00:05.000Z");

    render(
      <Providers client={client} session={session} refetchSession={vi.fn()}>
        <SecurityProbe />
      </Providers>,
    );
    expect(screen.getByText("DONNEE_PRIVEE_SYNTHETIQUE")).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_001);
    });

    expect(screen.queryByText("DONNEE_PRIVEE_SYNTHETIQUE")).toBeNull();
    expect(document.documentElement.dataset.sessionSecurity).toBe("expired");
    expect(client.getQueriesData({ queryKey: queryKeys.privateComparisonsRoot("account-a") })).toEqual([]);
    expect(client.getMutationCache().getAll()).toHaveLength(0);
    expect(window.localStorage.getItem("botnote:session-change")).toBeTruthy();
  });

  it("bloque synchroniquement le DOM au signal inter-onglets avant le refetch", () => {
    const client = new QueryClient();
    let release: ((session: Session) => void) | undefined;
    const refetch = vi.fn(
      () =>
        new Promise<Session>((resolve) => {
          release = resolve;
        }),
    );
    render(
      <Providers client={client} session={authenticatedSession()} refetchSession={refetch}>
        <SecurityProbe />
      </Providers>,
    );
    const sensitive = document.querySelector<HTMLElement>("[data-private-sensitive]");
    expect(sensitive?.hidden).toBe(false);

    act(() => {
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: "botnote:session-change",
          newValue: "synthetic-session-change",
        }),
      );
    });

    expect(document.documentElement.dataset.sessionSecurity).toBe("invalidating");
    expect(sensitive?.hidden).toBe(true);
    expect(screen.queryByText("DONNEE_PRIVEE_SYNTHETIQUE")).toBeNull();
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(release).toBeTypeOf("function");
  });

  it("purge à pagehide et ne réaffiche après BFCache qu’après une session revérifiée", async () => {
    const client = new QueryClient();
    let resolveRefetch: ((session: Session) => void) | undefined;
    const replacement = authenticatedSession(SCOPE_B, "2099-07-30T12:06:00.000Z", "2099-07-30T12:01:00.000Z");
    const refetch = vi.fn(
      () =>
        new Promise<Session>((resolve) => {
          resolveRefetch = resolve;
        }),
    );
    const view = render(
      <Providers client={client} session={authenticatedSession()} refetchSession={refetch}>
        <SecurityProbe />
      </Providers>,
    );

    act(() => window.dispatchEvent(persistedPageTransition("pagehide")));
    expect(screen.queryByText("DONNEE_PRIVEE_SYNTHETIQUE")).toBeNull();
    expect(document.documentElement.dataset.sessionSecurity).toBe("verifying");

    act(() => window.dispatchEvent(persistedPageTransition("pageshow")));
    expect(screen.queryByText("DONNEE_PRIVEE_SYNTHETIQUE")).toBeNull();
    expect(refetch).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveRefetch?.(replacement);
      await Promise.resolve();
    });
    view.rerender(
      <Providers client={client} session={replacement} refetchSession={refetch}>
        <SecurityProbe />
      </Providers>,
    );
    expect(await screen.findByText("DONNEE_PRIVEE_SYNTHETIQUE")).toBeTruthy();
    expect(document.documentElement.dataset.sessionSecurity).toBe("verified");
  });

  it("aborte les lectures privées en vol avant de vider leur cache à pagehide", async () => {
    const client = new QueryClient();
    let requestSignal: AbortSignal | undefined;
    const request = client
      .fetchQuery({
        queryKey: queryKeys.privateComparison("account-a", `pc_${"q".repeat(24)}`),
        queryFn: ({ signal }) =>
          new Promise<never>((_resolve, reject) => {
            requestSignal = signal;
            signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
          }),
      })
      .catch(() => undefined);

    render(
      <Providers client={client} session={authenticatedSession()} refetchSession={vi.fn()}>
        <SecurityProbe />
      </Providers>,
    );
    expect(requestSignal?.aborted).toBe(false);

    act(() => window.dispatchEvent(persistedPageTransition("pagehide")));

    expect(requestSignal?.aborted).toBe(true);
    await request;
    expect(client.getQueriesData({ queryKey: queryKeys.privateComparisonsRoot("account-a") })).toEqual([]);
  });

  it("échoue fermé quand une session Comparaisons omet les métadonnées de sécurité", () => {
    const client = new QueryClient();
    const session = authenticatedSession();
    delete session.server_time;

    render(
      <Providers client={client} session={session} refetchSession={vi.fn()}>
        <SecurityProbe />
      </Providers>,
    );

    expect(screen.queryByText("DONNEE_PRIVEE_SYNTHETIQUE")).toBeNull();
    expect(document.documentElement.dataset.sessionSecurity).toBe("invalidating");
  });
});
