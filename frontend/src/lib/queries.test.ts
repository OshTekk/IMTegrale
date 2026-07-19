import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { clearAccountState, clearAccountStateOnCapabilityChange, queryKeys, replaceSessionState } from "./queries";

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
      mutationFn: async () => ({ ok: true }),
    });

    const nextSession = {
      authenticated: true,
      role: "viewer" as const,
      auth_method: "token" as const,
      account: { id: "account-b", display_name: "Justine", imt_username: null },
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

  it("namespaces every learning cache by account and immutable catalog version", () => {
    expect(queryKeys.learningCatalog("account-a", "release-v1")).toEqual([
      "account",
      "account-a",
      "learning",
      "release-v1",
      "catalog",
    ]);
    expect(queryKeys.learningContent("account-a", "release-v1", "lesson.one")).toEqual([
      "account",
      "account-a",
      "learning",
      "release-v1",
      "content",
      "lesson.one",
    ]);
    expect(queryKeys.learningProgress("account-b", "release-v2")).toEqual([
      "account",
      "account-b",
      "learning",
      "release-v2",
      "progress",
    ]);
  });

  it("purges catalog, content, search and progress together on an account change", () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(queryKeys.learningCatalog("account-a", "release-v1"), { private: "catalog" });
    queryClient.setQueryData(queryKeys.learningContent("account-a", "release-v1", "lesson.one"), { private: "lesson" });
    queryClient.setQueryData(queryKeys.learningSearch("account-a", "release-v1", "query"), { private: "result" });
    queryClient.setQueryData(queryKeys.learningProgress("account-a", "release-v1"), { private: "progress" });

    clearAccountState(queryClient);

    expect(queryClient.getQueryCache().findAll({ queryKey: queryKeys.account })).toHaveLength(0);
  });

  it("purges private learning caches on a same-account IMT to token downgrade", () => {
    const queryClient = new QueryClient();
    const previous = {
      authenticated: true,
      role: "owner" as const,
      auth_method: "imt" as const,
      account: { id: "account-a", display_name: "[FICTIF] Compte", imt_username: "fictif" },
      learning: {
        available: true,
        audience_label: "FIP 2028",
        level_label: "2A",
        reverify_required: false,
        catalog_version: "release-v1",
      },
    };
    queryClient.setQueryData(queryKeys.session, previous);
    queryClient.setQueryData(queryKeys.learningContent("account-a", "release-v1", "lesson.one"), { private: "lesson" });

    clearAccountStateOnCapabilityChange(queryClient, previous, {
      ...previous,
      auth_method: "token",
      learning: { ...previous.learning, available: true },
    });

    expect(
      queryClient.getQueryData(queryKeys.learningContent("account-a", "release-v1", "lesson.one")),
    ).toBeUndefined();
  });

  it("purges an immutable release scope before installing a new catalog version", () => {
    const queryClient = new QueryClient();
    const previous = {
      authenticated: true,
      role: "owner" as const,
      auth_method: "imt" as const,
      account: { id: "account-a", display_name: "[FICTIF] Compte", imt_username: "fictif" },
      learning: {
        available: true,
        audience_label: "FIP 2028",
        level_label: "2A",
        reverify_required: false,
        catalog_version: "release-v1",
      },
    };
    queryClient.setQueryData(queryKeys.learningCatalog("account-a", "release-v1"), { release_id: "release-v1" });

    clearAccountStateOnCapabilityChange(queryClient, previous, {
      ...previous,
      learning: { ...previous.learning, catalog_version: "release-v2" },
    });

    expect(queryClient.getQueryData(queryKeys.learningCatalog("account-a", "release-v1"))).toBeUndefined();
  });
});
