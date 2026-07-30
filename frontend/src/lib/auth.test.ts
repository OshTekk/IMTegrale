import { describe, expect, it } from "vitest";
import type { Session } from "../types";
import { isPrimaryOwnerSession } from "./auth";
import { primarySessionScope } from "./securityScope";

describe("isPrimaryOwnerSession", () => {
  it.each(["imt", "passkey"] as const)("accepts an owner authenticated with %s", (authMethod) => {
    expect(isPrimaryOwnerSession({ role: "owner", auth_method: authMethod })).toBe(true);
  });

  it("rejects delegated, viewer, and incomplete sessions", () => {
    expect(isPrimaryOwnerSession({ role: "owner", auth_method: "token" })).toBe(false);
    expect(isPrimaryOwnerSession({ role: "viewer", auth_method: "passkey" })).toBe(false);
    expect(isPrimaryOwnerSession({ role: "owner" })).toBe(false);
  });
});

describe("primarySessionScope", () => {
  const session: Session = {
    authenticated: true,
    session_scope: `bss1_${"a".repeat(64)}`,
    role: "owner",
    auth_method: "imt",
    account: { id: "account-fictif-a", display_name: "Alice Exemple", imt_username: "alice.exemple" },
    private_comparisons: { available: true },
  };

  it("reste stable pour la même session sans dépendre du nom visible", () => {
    expect(primarySessionScope(session)).toBe(
      primarySessionScope({
        ...session,
        account: { ...session.account!, display_name: "Nom affiché modifié" },
      }),
    );
  });

  it.each([
    ["session web", { ...session, session_scope: `bss1_${"b".repeat(64)}` }],
    ["compte", { ...session, account: { ...session.account!, id: "account-fictif-b" } }],
    ["rôle", { ...session, role: "viewer" as const }],
    ["authentification", { ...session, auth_method: "token" as const }],
    ["capacité", { ...session, private_comparisons: { available: false } }],
    ["compte absent", { ...session, account: undefined }],
    ["expiration", { authenticated: false }],
  ])("change lorsque la frontière %s change", (_label, next) => {
    expect(primarySessionScope(next)).not.toBe(primarySessionScope(session));
  });
});
