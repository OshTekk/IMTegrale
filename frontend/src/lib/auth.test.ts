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
  const scope = `bss1_${"a".repeat(64)}`;
  const session: Session = {
    authenticated: true,
    session_scope: scope,
    role: "owner",
    auth_method: "imt",
    account: { id: "account-fictif-a", display_name: "Alice Exemple", imt_username: "alice.exemple" },
    private_comparisons: { available: true },
  };

  it("reste stable pour la même session sans dépendre du nom visible", () => {
    expect(primarySessionScope(session)).toBe(scope);
    expect(
      primarySessionScope({
        ...session,
        account: { ...session.account!, display_name: "Nom affiché modifié" },
      }),
    ).toBe(scope);
  });

  it("change seulement lorsque le serveur émet un nouveau scope opaque", () => {
    const next = { ...session, session_scope: `bss1_${"b".repeat(64)}` };
    expect(primarySessionScope(next)).not.toBe(primarySessionScope(session));
  });

  it.each([
    ["scope absent", { ...session, session_scope: undefined }],
    ["scope invalide", { ...session, session_scope: "account-fictif-a" }],
    ["capacité absente", { ...session, private_comparisons: { available: false } }],
    ["session anonyme", { ...session, authenticated: false }],
  ])("échoue fermé lorsque le contrat est invalide : %s", (_label, next) => {
    expect(primarySessionScope(next)).toBe("unverified");
  });
});
