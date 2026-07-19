// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { inspectFetchRequest, readFetchJson } from "../test/fetchRequest";
import type { Session } from "../types";
import { queryKeys, useDeleteLearningProgress, useLearningSearch } from "./queries";

const learningSession: Session = {
  authenticated: true,
  role: "owner",
  auth_method: "imt",
  account: { id: "account-fictif-query", display_name: "[FICTIF] Compte", imt_username: "fictif" },
  learning: {
    available: true,
    audience_label: "[FICTIF] FIP 2028",
    level_label: "[FICTIF] 2A",
    reverify_required: false,
    catalog_version: "release-fictive-v1",
  },
};

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function clientWithSession(): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(queryKeys.session, learningSession);
  return client;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("learning query boundaries", () => {
  it("keeps the search term out of the URL and sends it only to the protected POST route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          release_id: "release-fictive-v1",
          items: [],
          has_more: false,
          next_offset: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = clientWithSession();

    const query = renderHook(() => useLearningSearch("notion fictive"), { wrapper: wrapper(client) });
    await waitFor(() => expect(query.result.current.isSuccess).toBe(true));

    const [input, init] = fetchMock.mock.calls[0]!;
    const request = inspectFetchRequest(input, init);
    expect(request.pathname).toBe("/api/v1/learning/search");
    expect(request.method).toBe("POST");
    expect(request.url).not.toContain("notion%20fictive");
    expect(request.url).not.toContain("notion fictive");
    expect(await readFetchJson(input, init)).toMatchObject({ query: "notion fictive", limit: 20 });
  });

  it("invalidates both progress and recent attempts after a complete reset", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          deleted: { progress: 1, attempts: 2 },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = clientWithSession();
    const progressKey = queryKeys.learningProgress("account-fictif-query", "release-fictive-v1");
    const attemptsKey = queryKeys.learningAttempts("account-fictif-query", "release-fictive-v1");
    client.setQueryData(progressKey, { items: [{ content_id: "lesson.fictive" }] });
    client.setQueryData(attemptsKey, { items: [{ id: "attempt.fictif" }] });
    const mutation = renderHook(() => useDeleteLearningProgress(), { wrapper: wrapper(client) });

    await act(async () => {
      await mutation.result.current.mutateAsync();
    });

    expect(client.getQueryState(progressKey)?.isInvalidated).toBe(true);
    expect(client.getQueryState(attemptsKey)?.isInvalidated).toBe(true);
    const [input, init] = fetchMock.mock.calls[0]!;
    const request = inspectFetchRequest(input, init);
    expect(request.pathname).toBe("/api/v1/learning/progress");
    expect(request.method).toBe("DELETE");
  });
});
