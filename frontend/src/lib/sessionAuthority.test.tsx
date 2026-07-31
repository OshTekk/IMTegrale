// @vitest-environment jsdom

import { useQueryClient } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { useLayoutEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Session } from "../types";
import { EpochQueryClientHost } from "./epochQueryClient";
import { queryKeys } from "./queries";
import { sessionDeadlineFromResponse, SessionAuthority, SessionAuthorityRoot } from "./sessionAuthority";
import { SessionSecurityBoundary } from "./sessionSecurity";

const SCOPE_A = `bss1_${"a".repeat(64)}`;
const SCOPE_B = `bss1_${"b".repeat(64)}`;

function session(
  scope: string,
  accountId: string,
  options?: {
    serverTime?: string;
    expiresAt?: string;
    authMethod?: "imt" | "passkey" | "token";
    available?: boolean;
  },
): Session {
  return {
    authenticated: true,
    session_scope: scope,
    session_expires_at: options?.expiresAt ?? "2099-07-30T12:05:00.000Z",
    server_time: options?.serverTime ?? "2099-07-30T12:00:00.000Z",
    role: "owner",
    auth_method: options?.authMethod ?? "imt",
    account: { id: accountId, display_name: accountId, imt_username: null },
    private_comparisons: { available: options?.available ?? true },
  };
}

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const authorities: SessionAuthority[] = [];

afterEach(() => {
  cleanup();
  for (const authority of authorities.splice(0)) authority.stopForTests();
  Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
  document.documentElement.removeAttribute("data-session-security");
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("SessionAuthority", () => {
  it("jette la réponse A tardive après une transition et conserve B", async () => {
    const first = deferred<Session>();
    const second = deferred<Session>();
    const requests = [first, second];
    const authority = new SessionAuthority({
      initialSession: session(SCOPE_A, "account-a"),
      transport: () => requests.shift()!.promise,
    });
    authorities.push(authority);

    const staleA = authority.refreshAuthoritativeSession();
    authority.invalidate("session-change");
    const freshB = authority.refreshAuthoritativeSession();
    second.resolve(session(SCOPE_B, "account-b"));
    await freshB;
    first.resolve(session(SCOPE_A, "account-a"));
    await staleA;

    expect(authority.getSnapshot()).toMatchObject({
      securityState: "verified",
      authEpoch: 1,
      sessionScope: SCOPE_B,
      currentRequestSequence: 2,
      latestCommittedSequence: 2,
    });
    expect(authority.getSnapshot().session?.account?.id).toBe("account-b");
  });

  it("publie seulement la dernière de trois requêtes inversées même si le transport ignore abort", async () => {
    const first = deferred<Session>();
    const second = deferred<Session>();
    const third = deferred<Session>();
    const queue = [first, second, third];
    const authority = new SessionAuthority({
      initialSession: session(SCOPE_A, "account-a"),
      transport: () => queue.shift()!.promise,
    });
    authorities.push(authority);
    const one = authority.refreshAuthoritativeSession();
    const two = authority.refreshAuthoritativeSession();
    const three = authority.refreshAuthoritativeSession();

    third.resolve(session(SCOPE_A, "account-a"));
    await three;
    second.resolve(session(SCOPE_A, "account-a"));
    first.resolve(session(SCOPE_A, "account-a"));
    await Promise.all([one, two]);

    expect(authority.getSnapshot().securityState).toBe("verified");
    expect(authority.getSnapshot().currentRequestSequence).toBe(3);
    expect(authority.getSnapshot().latestCommittedSequence).toBe(3);
  });

  it("ignore une erreur ancienne après le commit de la requête la plus récente", async () => {
    const slow = deferred<Session>();
    const fast = deferred<Session>();
    const queue = [slow, fast];
    const authority = new SessionAuthority({
      initialSession: session(SCOPE_A, "account-a"),
      transport: () => queue.shift()!.promise,
    });
    authorities.push(authority);
    const stale = authority.refreshAuthoritativeSession();
    const current = authority.refreshAuthoritativeSession();
    fast.resolve(session(SCOPE_A, "account-a"));
    await current;
    slow.reject(new Error("late network failure"));
    await stale;

    expect(authority.getSnapshot().securityState).toBe("verified");
    expect(authority.getSnapshot().latestCommittedSequence).toBe(2);
  });

  it("ignore toute réponse reçue après logout", async () => {
    const late = deferred<Session>();
    const authority = new SessionAuthority({
      initialSession: session(SCOPE_A, "account-a"),
      transport: () => late.promise,
    });
    authorities.push(authority);
    const request = authority.refreshAuthoritativeSession();
    authority.signalLocalTransition("logout");
    late.resolve(session(SCOPE_A, "account-a"));
    await request;

    expect(authority.getSnapshot().securityState).toBe("anonymous");
    expect(authority.getSnapshot().session).toEqual({ authenticated: false });
    expect(authority.getSnapshot().sessionScope).toBeNull();
  });

  it("ignore toute réponse reçue après expiration", async () => {
    const late = deferred<Session>();
    const authority = new SessionAuthority({
      initialSession: session(SCOPE_A, "account-a"),
      transport: () => late.promise,
    });
    authorities.push(authority);
    const request = authority.refreshAuthoritativeSession();
    authority.invalidate("expired", "expired");
    late.resolve(session(SCOPE_A, "account-a"));
    await request;

    expect(authority.getSnapshot().securityState).toBe("expired");
    expect(authority.getSnapshot().session).toBeUndefined();
    expect(authority.getSnapshot().sessionScope).toBeNull();
  });

  it("soustrait tout le RTT et refuse expiration égale, RTT excessif, NaN et overflow", () => {
    const valid = sessionDeadlineFromResponse(session(SCOPE_A, "account-a"), 1_000, 1_400, 10_000);
    expect(valid?.monotonicDeadline).toBe(301_000);
    expect(valid?.wallDeadline).toBe(309_600);
    expect(sessionDeadlineFromResponse(session(SCOPE_A, "account-a"), 0, 300_001)).toBeNull();
    expect(
      sessionDeadlineFromResponse(
        session(SCOPE_A, "account-a", {
          expiresAt: "2099-07-30T12:00:00.000Z",
        }),
        0,
        0,
      ),
    ).toBeNull();
    expect(
      sessionDeadlineFromResponse(
        session(SCOPE_A, "account-a", {
          serverTime: "invalid",
        }),
        0,
        0,
      ),
    ).toBeNull();
    expect(sessionDeadlineFromResponse(session(SCOPE_A, "account-a"), 10, Number.POSITIVE_INFINITY)).toBeNull();
  });

  it("accepte une horloge serveur future relativement cohérente", () => {
    const future = session(SCOPE_A, "account-a", {
      serverTime: "2100-01-01T00:00:00.000Z",
      expiresAt: "2100-01-01T00:05:00.000Z",
    });

    expect(sessionDeadlineFromResponse(future, 4_000, 4_250, 9_000)).toMatchObject({
      scope: SCOPE_A,
      monotonicDeadline: 304_000,
      wallDeadline: 308_750,
    });
  });

  it("ne prolonge pas la deadline du même scope et refuse server_time régressif", async () => {
    const authority = new SessionAuthority({
      initialSession: session(SCOPE_A, "account-a"),
      transport: async () =>
        session(SCOPE_A, "account-a", {
          serverTime: "2099-07-30T11:59:59.000Z",
          expiresAt: "2099-07-30T13:00:00.000Z",
        }),
    });
    authorities.push(authority);
    const initialDeadline = authority.getSnapshot().monotonicDeadline;
    await authority.refreshAuthoritativeSession();

    expect(authority.getSnapshot().securityState).toBe("expired");
    expect(authority.getSnapshot().monotonicDeadline).toBeNull();
    expect(initialDeadline).not.toBeNull();
  });

  it("refuse un principal incohérent même si le transport répète le même scope", async () => {
    const authority = new SessionAuthority({
      initialSession: session(SCOPE_A, "account-a"),
      transport: async () => session(SCOPE_A, "account-b"),
    });
    authorities.push(authority);

    await authority.refreshAuthoritativeSession();

    expect(authority.getSnapshot()).toMatchObject({
      securityState: "expired",
      authEpoch: 1,
      session: undefined,
      sessionScope: null,
    });
  });

  it("autorise une vraie extension uniquement après un nouveau scope et une nouvelle époque", async () => {
    const replacement = session(SCOPE_B, "account-a", {
      authMethod: "passkey",
      serverTime: "2099-07-30T12:01:00.000Z",
      expiresAt: "2099-07-30T13:01:00.000Z",
    });
    const responses = [replacement, replacement];
    const authority = new SessionAuthority({
      initialSession: session(SCOPE_A, "account-a"),
      transport: async () => responses.shift()!,
    });
    authorities.push(authority);

    await authority.refreshAuthoritativeSession();

    expect(authority.getSnapshot()).toMatchObject({
      securityState: "verified",
      authEpoch: 1,
      sessionScope: SCOPE_B,
      latestCommittedSequence: 2,
    });
    expect(authority.getSnapshot().session?.auth_method).toBe("passkey");
  });

  it("déduplique un même signal inter-onglets sans boucle de revalidation", () => {
    const authority = new SessionAuthority({
      initialSession: session(SCOPE_A, "account-a"),
      transport: () => new Promise(() => {}),
    });
    authorities.push(authority);
    authority.start({ refresh: false });
    const payload = JSON.stringify({
      version: 1,
      type: "session-change",
      nonce: "00000000-0000-4000-8000-000000000004",
    });

    act(() => {
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: "botnote:session-change",
          newValue: payload,
        }),
      );
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: "botnote:session-change",
          newValue: payload,
        }),
      );
    });

    expect(authority.getSnapshot().authEpoch).toBe(1);
    expect(authority.getSnapshot().currentRequestSequence).toBe(1);
  });
});

describe("EpochQueryClientHost", () => {
  it("abandonne le QueryClient A pendant que la boundary de route est démontée", async () => {
    const authority = new SessionAuthority({ initialSession: session(SCOPE_A, "account-a") });
    authorities.push(authority);
    const observedClients: ReturnType<typeof useQueryClient>[] = [];

    function ClientProbe() {
      const client = useQueryClient();
      useLayoutEffect(() => {
        observedClients.push(client);
      }, [client]);
      return <span data-testid="epoch-client">client</span>;
    }

    render(
      <SessionAuthorityRoot authority={authority} autoStart={false}>
        <EpochQueryClientHost>
          <ClientProbe />
          <SessionSecurityBoundary>
            <span>DONNEE_A</span>
          </SessionSecurityBoundary>
        </EpochQueryClientHost>
      </SessionAuthorityRoot>,
    );
    const oldClient = observedClients.at(-1)!;
    oldClient.setQueryData(queryKeys.privateComparison("account-a", `pc_${"x".repeat(24)}`), {
      owner: "A",
    });

    act(() => authority.invalidate("session-change"));

    await waitFor(() => expect(observedClients.at(-1)).not.toBe(oldClient));
    expect(oldClient.getQueryCache().getAll()).toHaveLength(0);
    expect(oldClient.getMutationCache().getAll()).toHaveLength(0);
    expect(screen.queryByText("DONNEE_A")).toBeNull();
  });

  it("garde l’observateur actif sans aucune route privée montée", () => {
    const authority = new SessionAuthority({
      initialSession: session(SCOPE_A, "account-a"),
      transport: () => new Promise(() => {}),
    });
    authorities.push(authority);
    authority.start({ refresh: false });
    render(
      <SessionAuthorityRoot authority={authority}>
        <EpochQueryClientHost>
          <span>ROUTE_PUBLIQUE</span>
        </EpochQueryClientHost>
      </SessionAuthorityRoot>,
    );

    act(() => {
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: "botnote:session-change",
          newValue: JSON.stringify({
            version: 1,
            type: "session-change",
            nonce: "00000000-0000-4000-8000-000000000003",
          }),
        }),
      );
    });

    expect(authority.getSnapshot().securityState).toBe("invalidating");
    expect(authority.getSnapshot().authEpoch).toBe(1);
    expect(screen.getByText("ROUTE_PUBLIQUE")).toBeTruthy();
  });
});
