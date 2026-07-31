// @vitest-environment jsdom

import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { queryKeys } from "./queries";
import { resetPrivateComparisonLeasesForTests, usePrivateComparisonLeaseOpen } from "./privateComparisonLease";
import { useAccountEventStream } from "./useAccountEventStream";

const PUBLIC_ID = `pc_${"a".repeat(24)}`;
const ACCOUNT_ID = "account-fixture";

class FakeEventSource {
  static current: FakeEventSource | null = null;

  readonly listeners = new Map<string, EventListener[]>();
  onerror: ((event: Event) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;

  constructor(readonly url: string) {
    FakeEventSource.current = this;
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    const callable = typeof listener === "function" ? listener : (event: Event) => listener.handleEvent(event);
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), callable]);
  }

  close(): void {}

  dispatch(type: string, event: Event): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function TerminalProbe() {
  const queryClient = useQueryClient();
  const open = usePrivateComparisonLeaseOpen(PUBLIC_ID);
  useAccountEventStream(ACCOUNT_ID, null);
  const detail = queryClient.getQueryData<{ peer: string }>(queryKeys.privateComparison(ACCOUNT_ID, PUBLIC_ID));
  return <span>{open ? detail?.peer : "FERME"}</span>;
}

afterEach(() => {
  cleanup();
  FakeEventSource.current = null;
  resetPrivateComparisonLeasesForTests();
  vi.unstubAllGlobals();
});

describe("useAccountEventStream", () => {
  it("ferme le DOM et purge caches et mutations avant tout refetch terminal", () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    client.setQueryData(queryKeys.privateComparison(ACCOUNT_ID, PUBLIC_ID), {
      peer: "DONNEE_PRIVEE_TERMINALE",
    });
    client.setQueryData(queryKeys.privateComparisons(ACCOUNT_ID), {
      comparisons: [{ public_id: PUBLIC_ID, other_participant: "PAIR" }],
    });
    client.getMutationCache().build(client, {
      mutationFn: async () => ({ bearer: "ONE_SHOT" }),
      meta: { privateComparison: true },
    });

    render(<TerminalProbe />, { wrapper: wrapper(client) });
    expect(screen.getByText("DONNEE_PRIVEE_TERMINALE")).toBeTruthy();
    expect(FakeEventSource.current?.url).toBe("/api/v1/events");

    act(() => {
      FakeEventSource.current?.dispatch(
        "update",
        new MessageEvent("update", {
          data: JSON.stringify({
            cursor: `evc1_${"b".repeat(32)}`,
            kind: "private_comparison:revoked",
            public_id: PUBLIC_ID,
          }),
          lastEventId: `evc1_${"b".repeat(32)}`,
        }),
      );
    });

    expect(screen.queryByText("DONNEE_PRIVEE_TERMINALE")).toBeNull();
    expect(screen.getByText("FERME")).toBeTruthy();
    expect(client.getQueryData(queryKeys.privateComparison(ACCOUNT_ID, PUBLIC_ID))).toBeUndefined();
    expect(client.getQueryData(queryKeys.privateComparisons(ACCOUNT_ID))).toBeUndefined();
    expect(client.getMutationCache().getAll()).toHaveLength(0);
  });
});
