import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { clearAccountState, queryKeys, replaceSessionState } from "./queries";

describe("replaceSessionState", () => {
  it("removes every previous account query and mutation before installing a session", () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(queryKeys.session, { authenticated: false });
    const observedSessionQuery = queryClient.getQueryCache().find({ queryKey: queryKeys.session });
    queryClient.setQueryData(queryKeys.dashboard("account-a"), { account: { id: "account-a" } });
    queryClient.setQueryData(queryKeys.settings("account-a"), { secretState: true });
    queryClient.setQueryData(queryKeys.tokens("account-a"), [{ id: "token-a" }]);
    queryClient.getMutationCache().build(queryClient, {
      mutationKey: ["account-a", "update"],
      mutationFn: async () => ({ ok: true })
    });

    const nextSession = {
      authenticated: true,
      role: "viewer" as const,
      auth_method: "token" as const,
      account: { id: "account-b", display_name: "Justine", imt_username: null }
    };
    replaceSessionState(queryClient, nextSession);

    expect(queryClient.getQueryData(queryKeys.dashboard("account-a"))).toBeUndefined();
    expect(queryClient.getQueryData(queryKeys.settings("account-a"))).toBeUndefined();
    expect(queryClient.getQueryData(queryKeys.tokens("account-a"))).toBeUndefined();
    expect(queryClient.getMutationCache().getAll()).toHaveLength(0);
    expect(queryClient.getQueryCache().find({ queryKey: queryKeys.session })).toBe(observedSessionQuery);
    expect(queryClient.getQueryData(queryKeys.session)).toEqual(nextSession);
  });

  it("clears authenticated data on revocation", () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(queryKeys.dashboard("account-a"), { account: { id: "account-a" } });

    replaceSessionState(queryClient, { authenticated: false });

    expect(queryClient.getQueryData(queryKeys.dashboard("account-a"))).toBeUndefined();
    expect(queryClient.getQueryData(queryKeys.session)).toEqual({ authenticated: false });
  });

  it("namespaces account data and clears every account namespace atomically", () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(queryKeys.dashboard("account-a"), { owner: "a" });
    queryClient.setQueryData(queryKeys.dashboard("account-b"), { owner: "b" });

    clearAccountState(queryClient);

    expect(queryClient.getQueryData(queryKeys.dashboard("account-a"))).toBeUndefined();
    expect(queryClient.getQueryData(queryKeys.dashboard("account-b"))).toBeUndefined();
  });
});
